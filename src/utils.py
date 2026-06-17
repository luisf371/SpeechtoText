import queue
import os
import threading
import wave
from loguru import logger
from pathlib import Path
import pyaudio

from src.config.constants import AUDIO_DURATION_MIN_THRESHOLD_SECONDS


# Audio file paths
_ASSETS_DIR = Path(__file__).parent / "assets" / "audio"
_START_SOUND_PATH = _ASSETS_DIR / "start_feedback.wav"
_STOP_SOUND_PATH = _ASSETS_DIR / "stop_feedback.wav"
# Distinct rising chime signaling the transcript is on the clipboard and ready
# to be pasted manually (Linux clipboard-handoff mode; see platform_support).
_CLIPBOARD_READY_SOUND_PATH = _ASSETS_DIR / "clipboard_ready.wav"

# Feedback beeps share ONE persistent PyAudio interface and are always played
# serially on a single dedicated worker thread. This design is deliberate and
# fixes two separate Linux bugs:
#
#   1. Crash (segfault): constructing/terminating a PyAudio interface per beep
#      churns Pa_Initialize/Pa_Terminate, which re-installs alsa-lib's global
#      error handler (snd_lib_error_set_handler) on each call. The capture
#      stream lives for the whole session and constantly emits ALSA underrun
#      errors; once a terminate leaves a dangling error-handler pointer, the
#      next capture-side ALSA error invokes freed code and the process dies.
#      Never terminating mid-session (only at shutdown, after capture is gone)
#      eliminates that churn.
#   2. Freeze: an earlier shared-interface attempt held a lock across the whole
#      blocking write loop, so overlapping start/stop beeps serialized on that
#      lock and stalled. Routing every beep through one consumer thread makes
#      the producer (the hotkey/worker path) non-blocking — it only enqueues —
#      while keeping PortAudio access single-threaded.
#
# The output stream is still opened and closed per beep so the playback device
# is released promptly between beeps (some ALSA stacks refuse to keep a playback
# device open while the capture stream is opened at record-start).
_FEEDBACK_LOCK = threading.Lock()
_feedback_audio_interface = None
_FEEDBACK_QUEUE_MAXSIZE = 8
_feedback_queue: "queue.Queue" = queue.Queue(maxsize=_FEEDBACK_QUEUE_MAXSIZE)
_feedback_worker: threading.Thread | None = None
_feedback_worker_lock = threading.Lock()


def _get_feedback_audio_interface():
    """Return the shared feedback PyAudio interface, creating it on first use."""
    global _feedback_audio_interface
    if _feedback_audio_interface is None:
        from src.audio_recorder import _suppress_native_stderr

        with _suppress_native_stderr():
            _feedback_audio_interface = pyaudio.PyAudio()
    return _feedback_audio_interface


def _ensure_feedback_worker() -> None:
    """Start the single feedback playback consumer thread if it isn't running."""
    global _feedback_worker
    with _feedback_worker_lock:
        if _feedback_worker is not None and _feedback_worker.is_alive():
            return
        _feedback_worker = threading.Thread(
            target=_feedback_worker_loop, name="FeedbackAudio", daemon=True
        )
        _feedback_worker.start()


def _feedback_worker_loop() -> None:
    """Play queued feedback beeps one at a time on a single thread."""
    while True:
        item = _feedback_queue.get()
        try:
            if item is None:
                break
            sound_path, sound_name = item
            _play_wav_feedback(sound_path, sound_name)
        except Exception as e:  # pragma: no cover - defensive guard
            logger.debug(f"Feedback audio worker error: {e}")
        finally:
            _feedback_queue.task_done()


def prewarm_feedback_audio():
    """Create the shared feedback interface and worker ahead of the first beep.

    Constructing PyAudio enumerates audio devices; doing it at startup (when no
    capture stream is open) keeps the first beep fast and avoids contending with
    the capture device the moment the user starts their first recording.
    """

    def _warm():
        try:
            with _FEEDBACK_LOCK:
                _get_feedback_audio_interface()
            _ensure_feedback_worker()
        except Exception as e:
            logger.debug(f"Feedback audio pre-warm failed: {e}")

    threading.Thread(target=_warm, name="FeedbackAudioPrewarm", daemon=True).start()


def shutdown_feedback_audio():
    """Stop the worker and terminate the shared interface during app shutdown.

    Must be called AFTER the capture stream/interface is torn down so no active
    ALSA capture is using the global error handler when Pa_Terminate resets it.
    """
    global _feedback_worker, _feedback_audio_interface

    with _feedback_worker_lock:
        worker = _feedback_worker
        _feedback_worker = None
    if worker and worker.is_alive():
        _feedback_queue.put(None)
        worker.join(timeout=2.0)

    with _FEEDBACK_LOCK:
        interface = _feedback_audio_interface
        _feedback_audio_interface = None
    if interface:
        try:
            interface.terminate()
        except Exception as e:
            logger.debug(f"Error terminating feedback audio interface: {e}")


def play_start_feedback():
    """Play a high-pitched beep for recording start."""

    _enqueue_feedback_sound(_START_SOUND_PATH, "start")


def play_stop_feedback():
    """Play a lower-pitched confirmation beep for recording stop."""

    _enqueue_feedback_sound(_STOP_SOUND_PATH, "stop")


def play_clipboard_ready_feedback():
    """Play a distinct rising chime: transcript is on the clipboard, ready to paste."""

    _enqueue_feedback_sound(_CLIPBOARD_READY_SOUND_PATH, "clipboard-ready")


def _enqueue_feedback_sound(sound_path: Path, sound_name: str):
    if not sound_path.exists():
        logger.warning(
            f"{sound_name.title()} feedback audio file not found: {sound_path}"
        )
        return

    _ensure_feedback_worker()
    try:
        _feedback_queue.put_nowait((sound_path, sound_name))
    except queue.Full:
        # Under rapid toggling, drop the beep rather than letting playback lag
        # behind the user's recordings.
        logger.debug(f"Feedback audio queue full; dropping {sound_name} beep")


def _play_wav_feedback(sound_path: Path, sound_name: str):
    """Play one beep on the shared interface. Runs on the feedback worker thread.

    Opens and closes the output stream per beep but never terminates the shared
    interface (see the module comment on the Pa_Terminate/ALSA crash).
    """
    stream = None
    try:
        from src.audio_recorder import _suppress_native_stderr

        with wave.open(str(sound_path), "rb") as wav_file:
            with _FEEDBACK_LOCK:
                audio = _get_feedback_audio_interface()
            with _suppress_native_stderr():
                stream = audio.open(
                    format=audio.get_format_from_width(wav_file.getsampwidth()),
                    channels=wav_file.getnchannels(),
                    rate=wav_file.getframerate(),
                    output=True,
                )

            data = wav_file.readframes(1024)
            while data:
                stream.write(data)
                data = wav_file.readframes(1024)
    except Exception as e:
        logger.error(f"Failed to play {sound_name} feedback sound: {e}")
    finally:
        if stream:
            try:
                stream.stop_stream()
                stream.close()
            except Exception as cleanup_error:
                logger.debug(
                    f"Error cleaning up {sound_name} feedback stream: {cleanup_error}"
                )


def validate_audio_file_exists(file_path: str) -> bool:
    """
    Check if audio file exists at the given path.

    Args:
        file_path: Path to the audio file

    Returns:
        True if file exists, False otherwise
    """
    if not os.path.exists(file_path):
        logger.error(f"Audio file not found: {file_path}")
        return False
    return True


def validate_audio_duration(
    file_path: str, min_duration: float = AUDIO_DURATION_MIN_THRESHOLD_SECONDS
) -> bool:
    """
    Validate audio file duration is acceptable for transcription.

    Skips very short audio clips to avoid unnecessary API calls.
    If duration cannot be determined, allows transcription to proceed.

    Args:
        file_path: Path to the audio file
        min_duration: Minimum required duration in seconds

    Returns:
        True if audio should be transcribed, False if too short
    """
    try:
        with wave.open(file_path, "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate() or 0
            duration_seconds = frames / float(rate) if rate else 0.0

        if duration_seconds < min_duration:
            logger.info(
                f"Audio too short ({duration_seconds:.3f}s); skipping transcription"
            )
            return False

        return True

    except Exception as e:
        # If duration cannot be determined (e.g., not a valid WAV), allow transcription
        logger.debug(f"Could not determine audio duration for {file_path}: {e}")
        return True
