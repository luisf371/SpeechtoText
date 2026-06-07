import pyaudio
import wave
import threading
import tempfile
import os
import sys
from contextlib import contextmanager
from typing import Callable, Optional
from loguru import logger

from src.config.constants import AUDIO_RECORDING_THREAD_TIMEOUT_SECONDS


_NATIVE_STDERR_LOCK = threading.Lock()


def _show_audio_backend_warnings() -> bool:
    return os.environ.get("PUSH_TO_TALK_SHOW_AUDIO_BACKEND_WARNINGS", "").lower() in {
        "1",
        "true",
        "yes",
    }


@contextmanager
def _suppress_native_stderr():
    """Temporarily silence C libraries that write directly to stderr."""
    if _show_audio_backend_warnings():
        yield
        return

    try:
        stderr_fd = sys.stderr.fileno()
    except (AttributeError, OSError, ValueError):
        yield
        return

    with _NATIVE_STDERR_LOCK:
        saved_fd = None
        devnull_fd = None
        try:
            saved_fd = os.dup(stderr_fd)
            devnull_fd = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull_fd, stderr_fd)
            yield
        finally:
            if saved_fd is not None:
                try:
                    os.dup2(saved_fd, stderr_fd)
                except OSError:
                    pass
            for fd in (devnull_fd, saved_fd):
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass


class AudioRecorder:
    def __init__(
        self,
        sample_rate: int = 16000,
        chunk_size: int = 1024,
        channels: int = 1,
        audio_format: int = pyaudio.paInt16,
    ):
        """
        Initialize the audio recorder.

        Args:
            sample_rate: Sample rate in Hz (16kHz is optimal for Whisper)
            chunk_size: Size of audio chunks
            channels: Number of audio channels (1 for mono)
            audio_format: Audio format (16-bit is optimal for Whisper)
        """
        self.sample_rate = sample_rate
        self.chunk_size = chunk_size
        self.channels = channels
        self.audio_format = audio_format

        self.is_recording = False
        self.audio_data = []
        self.recording_thread: Optional[threading.Thread] = None
        self.chunk_callback: Callable[[bytes], None] | None = None
        self.store_audio_data = True
        self._state_lock = threading.RLock()

        self.audio_interface = None
        self.stream = None
        self._init_error: Optional[Exception] = None

        # Initialize PyAudio in a background thread to avoid blocking startup
        self._init_thread = threading.Thread(
            target=self._initialize_audio_interface, daemon=True
        )
        self._init_thread.start()

    def _initialize_audio_interface(self):
        """Initialize PyAudio interface in background."""
        try:
            with _suppress_native_stderr():
                self.audio_interface = pyaudio.PyAudio()
            logger.debug("PyAudio initialized successfully in background")
        except Exception as e:
            self._init_error = e
            logger.error(f"Failed to initialize PyAudio: {e}")

    def start_recording(
        self,
        chunk_callback: Callable[[bytes], None] | None = None,
        store_audio_data: bool = True,
    ) -> bool:
        """Start recording audio."""
        with self._state_lock:
            if self.is_recording:
                logger.warning("Recording is already in progress")
                return False

            # Ensure initialization is complete
            if not self.audio_interface:
                if self._init_thread and self._init_thread.is_alive():
                    logger.info("Waiting for audio interface initialization...")
                    self._init_thread.join(timeout=5.0)

                if self._init_error:
                    logger.error(
                        "Cannot start recording, initialization failed: "
                        f"{self._init_error}"
                    )
                    return False

                if not self.audio_interface:
                    logger.error("Audio interface not initialized")
                    return False

            try:
                with _suppress_native_stderr():
                    self.stream = self.audio_interface.open(
                        format=self.audio_format,
                        channels=self.channels,
                        rate=self.sample_rate,
                        input=True,
                        frames_per_buffer=self.chunk_size,
                    )

                self.is_recording = True
                self.audio_data = []
                self.chunk_callback = chunk_callback
                self.store_audio_data = store_audio_data

                self.recording_thread = threading.Thread(
                    target=self._record_audio,
                    name="AudioRecorder",
                )
                self.recording_thread.start()

                logger.info("Audio recording started")
                return True

            except Exception as e:
                logger.error(f"Failed to start recording: {e}")
                self.is_recording = False
                self.chunk_callback = None
                self.store_audio_data = True
                self._cleanup_stream()
                return False

    def stop_recording(self) -> Optional[str]:
        """
        Stop recording and save audio to a temporary file.

        Returns:
            Path to the temporary audio file, or None if recording failed
        """
        with self._state_lock:
            if not self.is_recording:
                logger.warning("No recording in progress")
                return None

            self.is_recording = False
            recording_thread = self.recording_thread

        # Wait for recording thread to finish
        if recording_thread:
            recording_thread.join(timeout=AUDIO_RECORDING_THREAD_TIMEOUT_SECONDS)

        with self._state_lock:
            self.chunk_callback = None
            self.store_audio_data = True

        # Get sample width before cleanup
        sample_width = None
        if self.audio_interface:
            try:
                sample_width = self.audio_interface.get_sample_size(self.audio_format)
            except Exception as e:
                logger.warning(f"Could not get sample size from audio interface: {e}")
                # Fallback: calculate sample width from format
                if self.audio_format == pyaudio.paInt16:
                    sample_width = 2
                elif self.audio_format == pyaudio.paInt32:
                    sample_width = 4
                elif self.audio_format == pyaudio.paFloat32:
                    sample_width = 4
                else:
                    sample_width = 2  # Default to 16-bit

        self._cleanup_stream()

        if not self.audio_data:
            logger.warning("No audio data recorded")
            return None

        if sample_width is None:
            logger.error("Could not determine sample width")
            return None

        # Save audio to temporary file
        try:
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
            temp_filename = temp_file.name
            temp_file.close()

            with wave.open(temp_filename, "wb") as wf:
                wf.setnchannels(self.channels)
                wf.setsampwidth(sample_width)
                wf.setframerate(self.sample_rate)
                wf.writeframes(b"".join(self.audio_data))

            logger.info(f"Audio saved to {temp_filename}")
            return temp_filename

        except Exception as e:
            logger.error(f"Failed to save audio: {e}")
            return None

    def _record_audio(self):
        """Internal method to record audio in a separate thread."""
        try:
            while True:
                with self._state_lock:
                    if not self.is_recording or not self.stream:
                        break
                    stream = self.stream
                    store_audio_data = self.store_audio_data
                    chunk_callback = self.chunk_callback

                data = stream.read(self.chunk_size, exception_on_overflow=False)
                if isinstance(data, (bytes, bytearray)):
                    chunk = bytes(data)
                    if store_audio_data:
                        with self._state_lock:
                            self.audio_data.append(chunk)
                    if chunk_callback:
                        try:
                            chunk_callback(chunk)
                        except Exception as callback_error:
                            logger.error(
                                f"Audio chunk callback failed: {callback_error}"
                            )
                else:  # pragma: no cover - defensive guard for mocked objects
                    logger.debug(
                        "Skipping non-bytes audio chunk during recording: %s",
                        type(data).__name__,
                    )
        except Exception as e:
            logger.error(f"Error during recording: {e}")
            with self._state_lock:
                self.is_recording = False

    def _cleanup_stream(self):
        """Clean up audio stream resources."""
        try:
            if self.stream:
                self.stream.stop_stream()
                self.stream.close()
                self.stream = None

        except Exception as e:
            logger.error(f"Error during stream cleanup: {e}")

    def shutdown(self):
        """Terminate audio interface."""
        with self._state_lock:
            self.is_recording = False
        self._cleanup_stream()

        # Ensure init thread is done if we are shutting down
        if self._init_thread and self._init_thread.is_alive():
            try:
                self._init_thread.join(timeout=1.0)
            except Exception:
                pass

        try:
            if self.audio_interface:
                self.audio_interface.terminate()
                self.audio_interface = None
        except Exception as e:
            logger.error(f"Error during audio interface shutdown: {e}")

    def __enter__(self):
        """Context manager entry - returns self for use in 'with' statements."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - ensures cleanup even if exceptions occur."""
        self.shutdown()
        return False  # Don't suppress exceptions
