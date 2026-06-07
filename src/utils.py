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
    audio = None
    stream = None
    try:
        from src.audio_recorder import _suppress_native_stderr

        with wave.open(str(sound_path), "rb") as wav_file:
            with _suppress_native_stderr():
                audio = pyaudio.PyAudio()
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
        if audio:
            try:
                audio.terminate()
            except Exception as cleanup_error:
                logger.debug(
                    f"Error terminating {sound_name} feedback audio: {cleanup_error}"
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
