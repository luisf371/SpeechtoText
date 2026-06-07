import json
import queue
import threading
import time
from typing import Callable, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from loguru import logger
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

PARAKEET_STREAMING_SAMPLE_RATE = 16000
PARAKEET_STREAMING_CHANNELS = 1
PARAKEET_STREAMING_SAMPLE_WIDTH_BYTES = 2
PARAKEET_STREAMING_FRAME_SAMPLES = 512
PARAKEET_STREAMING_FRAME_BYTES = (
    PARAKEET_STREAMING_FRAME_SAMPLES
    * PARAKEET_STREAMING_SAMPLE_WIDTH_BYTES
    * PARAKEET_STREAMING_CHANNELS
)
PARAKEET_STREAMING_STOP_SILENCE_SECONDS = 0.75
PARAKEET_STREAMING_DRAIN_TIMEOUT_SECONDS = 5.0
PARAKEET_STREAMING_FINAL_TEXT_QUIET_SECONDS = 0.75
PARAKEET_STREAMING_CONNECT_TIMEOUT_SECONDS = 10.0
PARAKEET_STREAMING_DEFAULT_VAD_END_SILENCE_MS = 250
PARAKEET_STREAMING_DEFAULT_MAX_CHUNK_SECONDS = 8.0
PARAKEET_STREAMING_DEFAULT_BATCH_SIZE = 4
PARAKEET_STREAMING_DEFAULT_BATCH_WINDOW_MS = 15


def build_parakeet_ws_url(
    endpoint: str,
    *,
    vad_end_silence_ms: int | None = None,
    vad_max_chunk_seconds: float | None = None,
    transcription_batch_size: int | None = None,
    transcription_batch_window_ms: int | None = None,
) -> str:
    """Normalize a Parakeet HTTP or WebSocket endpoint to the native /ws route."""
    normalized = (endpoint or "").strip().rstrip("/")
    if not normalized:
        normalized = "http://localhost:8000"

    parts = urlsplit(normalized)
    scheme = parts.scheme or "http"
    netloc = parts.netloc
    path = parts.path.rstrip("/")
    query = parts.query

    if not netloc and parts.path:
        # urlsplit("localhost:8000") treats "localhost" as a scheme.
        fallback = urlsplit(f"http://{normalized}")
        scheme = fallback.scheme
        netloc = fallback.netloc
        path = fallback.path.rstrip("/")
        query = fallback.query

    for suffix in ("/v1/audio/transcriptions", "/audio/transcriptions"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break

    if path.endswith("/transcribe"):
        path = path[: -len("/transcribe")]
    if path.endswith("/v1"):
        path = path[: -len("/v1")]
    if path.endswith("/ws"):
        ws_path = path
    else:
        ws_path = f"{path}/ws" if path else "/ws"

    query_params = dict(parse_qsl(query, keep_blank_values=True))
    if vad_end_silence_ms is not None:
        query_params["vad_end_silence_ms"] = str(vad_end_silence_ms)
    if vad_max_chunk_seconds is not None:
        query_params["vad_max_chunk_seconds"] = str(vad_max_chunk_seconds)
    if transcription_batch_size is not None:
        query_params["transcription_batch_size"] = str(transcription_batch_size)
    if transcription_batch_window_ms is not None:
        query_params["transcription_batch_window_ms"] = str(
            transcription_batch_window_ms
        )

    ws_scheme = "wss" if scheme in {"https", "wss"} else "ws"
    return urlunsplit((ws_scheme, netloc, ws_path, urlencode(query_params), ""))


class ParakeetStreamingSession:
    """Threaded WebSocket session for Parakeet's native streaming endpoint."""

    def __init__(
        self,
        endpoint: str,
        on_text: Callable[[str], None],
        *,
        drain_timeout: float = PARAKEET_STREAMING_DRAIN_TIMEOUT_SECONDS,
        vad_end_silence_ms: int | None = None,
        vad_max_chunk_seconds: float | None = None,
        transcription_batch_size: int | None = None,
        transcription_batch_window_ms: int | None = None,
    ):
        self.ws_url = build_parakeet_ws_url(
            endpoint,
            vad_end_silence_ms=vad_end_silence_ms,
            vad_max_chunk_seconds=vad_max_chunk_seconds,
            transcription_batch_size=transcription_batch_size,
            transcription_batch_window_ms=transcription_batch_window_ms,
        )
        self.on_text = on_text
        self.drain_timeout = drain_timeout
        self._audio_queue: queue.Queue[Optional[bytes]] = queue.Queue()
        self._stop_event = threading.Event()
        self._connected_event = threading.Event()
        self._last_text_time = 0.0
        self._connection = None
        self.error: Exception | None = None
        self._thread: threading.Thread | None = None
        self._send_lock = threading.Lock()
        self._finish_lock = threading.Lock()

    @property
    def is_active(self) -> bool:
        """Return True while the session thread is running or connecting."""
        return bool(
            self._thread
            and self._thread.is_alive()
            and not self._stop_event.is_set()
        )

    def start(self) -> None:
        """Start the WebSocket session in a background thread."""
        if self._thread and self._thread.is_alive():
            return

        self.error = None
        self._stop_event.clear()
        self._connected_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="ParakeetStreamingSession",
        )
        self._thread.start()

    def send_audio(self, chunk: bytes) -> None:
        """Queue raw PCM bytes for sending. Safe for the audio recording thread."""
        if not chunk or self._stop_event.is_set():
            return
        self._audio_queue.put(bytes(chunk))

    def inject_silence(self, seconds: float = PARAKEET_STREAMING_STOP_SILENCE_SECONDS):
        """Queue silence so upstream VAD can flush the last utterance."""
        frame_count = max(1, int(seconds * PARAKEET_STREAMING_SAMPLE_RATE / 512))
        silence = b"\x00" * PARAKEET_STREAMING_FRAME_BYTES
        for _ in range(frame_count):
            self._audio_queue.put(silence)

    def stop(self) -> None:
        """Close the socket and stop the session thread."""
        self._stop_event.set()
        self._audio_queue.put(None)
        self._close_connection()

        if self._thread:
            self._thread.join(timeout=self.drain_timeout + 1.0)

    def finish_recording(self) -> None:
        """Flush the final utterance while keeping the WebSocket connected."""
        with self._finish_lock:
            stop_started_at = time.monotonic()
            self.inject_silence()
            deadline = stop_started_at + self.drain_timeout

            while time.monotonic() < deadline and not self._stop_event.is_set():
                final_text_seen = self._last_text_time >= stop_started_at
                quiet_after_final_text = (
                    final_text_seen
                    and time.monotonic() - self._last_text_time
                    >= PARAKEET_STREAMING_FINAL_TEXT_QUIET_SECONDS
                )
                if self._audio_queue.empty() and quiet_after_final_text:
                    break
                time.sleep(0.05)

    def _run(self) -> None:
        sender = None
        try:
            logger.info(f"Connecting to Parakeet WebSocket endpoint: {self.ws_url}")
            with connect(
                self.ws_url,
                open_timeout=PARAKEET_STREAMING_CONNECT_TIMEOUT_SECONDS,
            ) as websocket:
                self._connection = websocket
                self._connected_event.set()

                sender = threading.Thread(
                    target=self._send_loop,
                    daemon=True,
                    name="ParakeetStreamingSender",
                )
                sender.start()

                self._receive_loop(websocket)
                self._stop_event.set()
                self._audio_queue.put(None)
                sender.join(timeout=1.0)
        except Exception as e:
            self.error = e
            logger.error(f"Parakeet streaming session failed: {e}")
        finally:
            self._stop_event.set()
            self._audio_queue.put(None)
            if sender and sender.is_alive():
                sender.join(timeout=1.0)
            self._connected_event.clear()
            self._connection = None

    def _send_loop(self) -> None:
        while not self._stop_event.is_set():
            chunk = self._audio_queue.get()
            if chunk is None:
                break
            if not self._connected_event.wait(
                timeout=PARAKEET_STREAMING_CONNECT_TIMEOUT_SECONDS
            ):
                logger.warning(
                    "Timed out waiting for Parakeet WebSocket before sending audio"
                )
                break
            connection = self._connection
            if not connection:
                break
            try:
                with self._send_lock:
                    connection.send(chunk)
            except ConnectionClosed:
                self._stop_event.set()
                break
            except Exception as e:
                self.error = e
                self._stop_event.set()
                logger.error(f"Failed to send Parakeet streaming audio: {e}")
                break

    def _receive_loop(self, websocket) -> None:
        while not self._stop_event.is_set():
            try:
                message = websocket.recv(timeout=0.25)
            except TimeoutError:
                continue
            except ConnectionClosed:
                break

            self._handle_message(message)

    def _handle_message(self, message: str | bytes) -> None:
        try:
            if isinstance(message, bytes):
                message = message.decode("utf-8")
            payload = json.loads(message)
        except Exception:
            logger.debug(f"Ignoring non-JSON Parakeet streaming message: {message!r}")
            return

        text = str(payload.get("text", ""))
        if text.strip():
            logger.debug(f"Parakeet streaming text received: {text!r}")
            self._last_text_time = time.monotonic()
            self.on_text(text)
            return

        status = payload.get("status")
        if status:
            logger.debug(f"Parakeet streaming status: {status}")

    def _close_connection(self) -> None:
        connection = self._connection
        if not connection:
            return
        try:
            with self._send_lock:
                connection.close()
        except Exception as e:
            logger.debug(f"Error closing Parakeet streaming connection: {e}")
