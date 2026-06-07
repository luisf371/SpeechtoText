from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src import utils


@pytest.fixture
def mock_logger(monkeypatch):
    """Provide a mock logger to capture warnings and errors."""

    logger = MagicMock()
    monkeypatch.setattr(utils, "logger", logger)
    return logger


def test_play_start_feedback_plays_audio(tmp_path, monkeypatch, mock_logger):
    """The start feedback should play when the audio file exists."""

    audio_path = tmp_path / "start.wav"
    audio_path.write_bytes(b"data")

    thread = MagicMock()
    monkeypatch.setattr(utils, "_START_SOUND_PATH", audio_path)
    monkeypatch.setattr(utils.threading, "Thread", thread)

    utils.play_start_feedback()

    thread.assert_called_once()
    assert thread.call_args.kwargs["target"] == utils._play_wav_feedback
    assert thread.call_args.kwargs["args"] == (audio_path, "start")
    thread.return_value.start.assert_called_once()
    mock_logger.warning.assert_not_called()


def test_play_start_feedback_missing_file_warns(monkeypatch, mock_logger):
    """A missing audio file should emit a warning and not play anything."""

    missing_path = Path("/does/not/exist/start.wav")
    thread = MagicMock()

    monkeypatch.setattr(utils, "_START_SOUND_PATH", missing_path)
    monkeypatch.setattr(utils.threading, "Thread", thread)

    utils.play_start_feedback()

    thread.assert_not_called()
    mock_logger.warning.assert_called_once()


def test_play_wav_feedback_logs_error_on_failure(tmp_path, monkeypatch, mock_logger):
    """Exceptions raised while playing feedback should be logged as errors."""

    audio_path = tmp_path / "stop.wav"
    audio_path.write_bytes(b"data")

    def failing_wave_open(*_, **__):
        raise RuntimeError("boom")

    monkeypatch.setattr(utils.wave, "open", failing_wave_open)

    utils._play_wav_feedback(audio_path, "stop")

    mock_logger.error.assert_called_once()


def test_play_wav_feedback_streams_wav_audio(tmp_path, monkeypatch, mock_logger):
    """Feedback playback should stream WAV frames through PyAudio."""

    audio_path = tmp_path / "start.wav"
    audio_path.write_bytes(b"data")

    wav_file = MagicMock()
    wav_file.__enter__.return_value = wav_file
    wav_file.getsampwidth.return_value = 2
    wav_file.getnchannels.return_value = 1
    wav_file.getframerate.return_value = 16000
    wav_file.readframes.side_effect = [b"chunk", b""]

    audio = MagicMock()
    audio.get_format_from_width.return_value = "format"
    stream = audio.open.return_value

    monkeypatch.setattr(utils.wave, "open", MagicMock(return_value=wav_file))
    monkeypatch.setattr(utils.pyaudio, "PyAudio", MagicMock(return_value=audio))

    utils._play_wav_feedback(audio_path, "start")

    audio.open.assert_called_once_with(
        format="format",
        channels=1,
        rate=16000,
        output=True,
    )
    stream.write.assert_called_once_with(b"chunk")
    stream.stop_stream.assert_called_once()
    stream.close.assert_called_once()
    audio.terminate.assert_called_once()
    mock_logger.error.assert_not_called()


def test_play_stop_feedback_plays_audio(tmp_path, monkeypatch, mock_logger):
    """The stop feedback should play when the audio file exists."""

    audio_path = tmp_path / "stop.wav"
    audio_path.write_bytes(b"data")

    thread = MagicMock()
    monkeypatch.setattr(utils, "_STOP_SOUND_PATH", audio_path)
    monkeypatch.setattr(utils.threading, "Thread", thread)

    utils.play_stop_feedback()

    thread.assert_called_once()
    assert thread.call_args.kwargs["target"] == utils._play_wav_feedback
    assert thread.call_args.kwargs["args"] == (audio_path, "stop")
    thread.return_value.start.assert_called_once()
    mock_logger.warning.assert_not_called()


def test_play_stop_feedback_missing_file_warns(monkeypatch, mock_logger):
    """A missing audio file for stop feedback should emit a warning."""

    missing_path = Path("/does/not/exist/stop.wav")
    thread = MagicMock()

    monkeypatch.setattr(utils, "_STOP_SOUND_PATH", missing_path)
    monkeypatch.setattr(utils.threading, "Thread", thread)

    utils.play_stop_feedback()

    thread.assert_not_called()
    mock_logger.warning.assert_called_once()


def test_validate_audio_file_exists_returns_true(tmp_path, mock_logger):
    """validate_audio_file_exists should return True when file exists."""

    audio_file = tmp_path / "test.wav"
    audio_file.write_bytes(b"audio data")

    result = utils.validate_audio_file_exists(str(audio_file))

    assert result is True
    mock_logger.error.assert_not_called()


def test_validate_audio_file_exists_returns_false(mock_logger):
    """validate_audio_file_exists should return False when file doesn't exist."""

    result = utils.validate_audio_file_exists("/nonexistent/path/audio.wav")

    assert result is False
    mock_logger.error.assert_called_once()


def test_validate_audio_duration_accepts_valid_duration(tmp_path, mock_logger):
    """validate_audio_duration should return True for audio longer than threshold."""
    import wave
    import struct

    # Create a valid WAV file with sufficient duration (1 second)
    audio_file = tmp_path / "valid.wav"
    sample_rate = 16000
    duration_seconds = 1.0
    num_samples = int(sample_rate * duration_seconds)

    with wave.open(str(audio_file), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        # Write silent audio data
        wf.writeframes(struct.pack("<" + "h" * num_samples, *([0] * num_samples)))

    result = utils.validate_audio_duration(str(audio_file))

    assert result is True


def test_validate_audio_duration_rejects_short_audio(tmp_path, mock_logger):
    """validate_audio_duration should return False for audio shorter than threshold."""
    import wave
    import struct

    # Create a valid WAV file with very short duration (0.05 seconds < 0.3 threshold)
    audio_file = tmp_path / "short.wav"
    sample_rate = 16000
    duration_seconds = 0.05
    num_samples = int(sample_rate * duration_seconds)

    with wave.open(str(audio_file), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack("<" + "h" * num_samples, *([0] * num_samples)))

    result = utils.validate_audio_duration(str(audio_file))

    assert result is False
    mock_logger.info.assert_called_once()


def test_validate_audio_duration_returns_true_on_invalid_file(tmp_path, mock_logger):
    """validate_audio_duration should return True when file cannot be read as WAV."""

    # Create an invalid WAV file (just some random bytes)
    invalid_file = tmp_path / "invalid.wav"
    invalid_file.write_bytes(b"this is not a valid wav file")

    result = utils.validate_audio_duration(str(invalid_file))

    # Should return True to allow transcription attempt
    assert result is True
    mock_logger.debug.assert_called_once()


def test_validate_audio_duration_with_custom_threshold(tmp_path):
    """validate_audio_duration should respect custom min_duration parameter."""
    import wave
    import struct

    # Create a 0.5 second audio file
    audio_file = tmp_path / "medium.wav"
    sample_rate = 16000
    duration_seconds = 0.5
    num_samples = int(sample_rate * duration_seconds)

    with wave.open(str(audio_file), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack("<" + "h" * num_samples, *([0] * num_samples)))

    # Should pass with lower threshold
    assert utils.validate_audio_duration(str(audio_file), min_duration=0.3) is True

    # Should fail with higher threshold
    assert utils.validate_audio_duration(str(audio_file), min_duration=1.0) is False
