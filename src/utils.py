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

# A single PyAudio instance is shared across all feedback beeps. Constructing
# PyAudio() calls Pa_Initialize(), which enumerates every audio device on the
# system (often 100ms+ and, on ALSA, contends with the capture stream being
# opened at record-start). Reusing one instance avoids paying that on every
# beep. Playback is serialized by the lock so concurrent beeps don't race on
# the shared interface.
_FEEDBACK_AUDIO_LOCK = threading.Lock()
_feedback_audio_interface = None


def _get_feedback_audio_interface():
    """Return the shared feedback PyAudio interface, creating it on first use."""
    global _feedback_audio_interface
    if _feedback_audio_interface is None:
        from src.audio_recorder import _suppress_native_stderr

        with _suppress_native_stderr():
            _feedback_audio_interface = pyaudio.PyAudio()
    return _feedback_audio_interface


def prewarm_feedback_audio():
    """Create the shared feedback interface ahead of the first recording.

    Constructing PyAudio enumerates audio devices; doing it at startup (when no
    capture stream is open) keeps the first beep fast and avoids contending with
    the capture device the moment the user starts their first recording.
    """

    def _warm():
        try:
            with _FEEDBACK_AUDIO_LOCK:
                _get_feedback_audio_interface()
        except Exception as e:
            logger.debug(f"Feedback audio pre-warm failed: {e}")

    threading.Thread(
        target=_warm, name="FeedbackAudioPrewarm", daemon=True
    ).start()


def shutdown_feedback_audio():
    """Terminate the shared feedback PyAudio interface during app shutdown."""
    global _feedback_audio_interface
    with _FEEDBACK_AUDIO_LOCK:
        interface = _feedback_audio_interface
        _feedback_audio_interface = None
    if interface:
        try:
            interface.terminate()
        except Exception as e:
            logger.debug(f"Error terminating feedback audio interface: {e}")


def play_start_feedback():
    """Play a high-pitched beep for recording start."""

    _play_feedback_sound(_START_SOUND_PATH, "start")


def play_stop_feedback():
    """Play a lower-pitched confirmation beep for recording stop."""

    _play_feedback_sound(_STOP_SOUND_PATH, "stop")


def _play_feedback_sound(sound_path: Path, sound_name: str):
    if not sound_path.exists():
        logger.warning(f"{sound_name.title()} feedback audio file not found: {sound_path}")
        return

    thread = threading.Thread(
        target=_play_wav_feedback,
        args=(sound_path, sound_name),
        name=f"{sound_name.title()}FeedbackSound",
        daemon=True,
    )
    thread.start()


def _play_wav_feedback(sound_path: Path, sound_name: str):
    stream = None
    try:
        from src.audio_recorder import _suppress_native_stderr

        with _FEEDBACK_AUDIO_LOCK:
            with wave.open(str(sound_path), "rb") as wav_file:
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
