"""Tests for text insertion fallback behavior."""

import sys
import types
from unittest.mock import MagicMock

from tests.test_helpers import create_keyboard_stub

keyboard_stub = create_keyboard_stub()
keyboard_stub.Controller = MagicMock
sys.modules.setdefault("pynput", types.SimpleNamespace(keyboard=keyboard_stub))
sys.modules["pynput.keyboard"] = keyboard_stub

from src.exceptions import TextInsertionError  # noqa: E402
from src.text_inserter import TextInserter  # noqa: E402


def test_insert_text_falls_back_to_direct_typing_when_clipboard_unavailable(mocker):
    inserter = TextInserter()
    inserter.keyboard = MagicMock()
    suppress_hotkeys = mocker.patch("src.hotkey_service.suppress_hotkey_events")
    mocker.patch.object(
        inserter,
        "_insert_via_clipboard",
        side_effect=TextInsertionError("clipboard unavailable"),
    )

    assert inserter.insert_text("hello") is True

    inserter.keyboard.type.assert_called_once_with("hello")
    suppress_hotkeys.assert_called_once()


def test_insert_space_then_text_falls_back_after_space(mocker):
    inserter = TextInserter()
    inserter.keyboard = MagicMock()
    suppress_hotkeys = mocker.patch("src.hotkey_service.suppress_hotkey_events")
    mocker.patch.object(
        inserter,
        "_insert_via_clipboard",
        side_effect=TextInsertionError("clipboard unavailable"),
    )

    assert inserter.insert_space_then_text("world") is True

    inserter.keyboard.press.assert_called_once_with(keyboard_stub.Key.space)
    inserter.keyboard.release.assert_called_once_with(keyboard_stub.Key.space)
    inserter.keyboard.type.assert_called_once_with("world")
    assert suppress_hotkeys.call_count == 2


def test_direct_typing_failure_raises_text_insertion_error():
    inserter = TextInserter()
    inserter.keyboard = MagicMock()
    inserter.keyboard.type.side_effect = RuntimeError("typing failed")

    try:
        inserter._insert_directly("hello")
    except TextInsertionError as error:
        assert "Direct text insertion failed" in str(error)
    else:
        raise AssertionError("Expected TextInsertionError")


def test_linux_clipboard_restore_leaves_inserted_text_available(monkeypatch, mocker):
    inserter = TextInserter()
    monkeypatch.setattr(sys, "platform", "linux")
    copy = mocker.patch("src.text_inserter.pyperclip.copy")

    inserter._restore_clipboard("previous")

    copy.assert_not_called()


def test_non_linux_clipboard_restore_restores_previous_clipboard(monkeypatch, mocker):
    inserter = TextInserter()
    monkeypatch.setattr(sys, "platform", "win32")
    copy = mocker.patch("src.text_inserter.pyperclip.copy")

    inserter._restore_clipboard("previous")

    copy.assert_called_once_with("previous")
