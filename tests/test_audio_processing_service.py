"""Tests for clipboard-handoff feedback in the audio processing service."""

import sys
import types
from unittest.mock import MagicMock

from tests.test_helpers import create_keyboard_stub

# Stub pynput before importing src modules so importing TextInserter does not
# require a real X display on headless CI.
keyboard_stub = create_keyboard_stub()
keyboard_stub.Controller = MagicMock
sys.modules.setdefault("pynput", types.SimpleNamespace(keyboard=keyboard_stub))
sys.modules["pynput.keyboard"] = keyboard_stub

from src import audio_processing_service as aps_module  # noqa: E402
from src.audio_processing_service import AudioProcessingService  # noqa: E402


class _StubInserter:
    def __init__(self, success=True):
        self.success = success
        self.inserted = []

    def get_active_window_title(self):
        return None

    def insert_text(self, text):
        self.inserted.append(text)
        return self.success


class _StubTranscriber:
    def __init__(self, text="hello world"):
        self.text = text

    def transcribe_audio(self, audio_path):
        return self.text


def _make_service(config, inserter, transcriber):
    return AudioProcessingService(
        get_config=lambda: config,
        get_transcriber=lambda: transcriber,
        get_text_refiner=lambda: None,
        get_text_inserter=lambda: inserter,
    )


def _config(**overrides):
    base = dict(
        debug_mode=False,
        enable_audio_feedback=True,
        is_text_refinement_effective=lambda: False,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


def test_handoff_plays_ready_sound_after_copy(tmp_path, monkeypatch):
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"x")
    inserter = _StubInserter(success=True)
    monkeypatch.setattr(
        aps_module.platform_support, "use_clipboard_handoff", lambda: True
    )
    calls = []
    monkeypatch.setattr(
        "src.utils.play_clipboard_ready_feedback", lambda: calls.append(1)
    )

    _make_service(_config(), inserter, _StubTranscriber("hello world")).process_audio(
        str(audio)
    )

    assert inserter.inserted == ["hello world"]
    assert calls == [1]


def test_handoff_skips_ready_sound_when_audio_feedback_disabled(tmp_path, monkeypatch):
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"x")
    monkeypatch.setattr(
        aps_module.platform_support, "use_clipboard_handoff", lambda: True
    )
    calls = []
    monkeypatch.setattr(
        "src.utils.play_clipboard_ready_feedback", lambda: calls.append(1)
    )

    _make_service(
        _config(enable_audio_feedback=False), _StubInserter(), _StubTranscriber()
    ).process_audio(str(audio))

    assert calls == []


def test_auto_paste_platform_does_not_play_ready_sound(tmp_path, monkeypatch):
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"x")
    monkeypatch.setattr(
        aps_module.platform_support, "use_clipboard_handoff", lambda: False
    )
    calls = []
    monkeypatch.setattr(
        "src.utils.play_clipboard_ready_feedback", lambda: calls.append(1)
    )

    _make_service(_config(), _StubInserter(), _StubTranscriber()).process_audio(
        str(audio)
    )

    assert calls == []
