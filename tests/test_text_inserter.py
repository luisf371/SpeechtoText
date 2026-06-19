"""Tests for text insertion behavior (auto-paste and Linux clipboard handoff)."""

import sys
import types
from unittest.mock import MagicMock

import pytest

from tests.test_helpers import create_keyboard_stub

keyboard_stub = create_keyboard_stub()
keyboard_stub.Controller = MagicMock
sys.modules.setdefault("pynput", types.SimpleNamespace(keyboard=keyboard_stub))
sys.modules["pynput.keyboard"] = keyboard_stub

from src import platform_support  # noqa: E402
from src import text_inserter as text_inserter_module  # noqa: E402
from src.exceptions import TextInsertionError  # noqa: E402
from src.text_inserter import TextInserter  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_text_inserter(monkeypatch):
    """Keep TextInserter construction and platform behavior deterministic.

    - Guarantee ``keyboard.Controller`` exists even if another test module
      replaced the shared pynput stub in ``sys.modules`` (``__init__`` calls it).
    - Default to the auto-paste platform; clipboard-handoff tests opt in.
    """
    monkeypatch.setattr(
        text_inserter_module.keyboard, "Controller", MagicMock, raising=False
    )
    monkeypatch.setattr(platform_support, "use_clipboard_handoff", lambda: False)


def _set_handoff(monkeypatch, enabled: bool) -> None:
    monkeypatch.setattr(platform_support, "use_clipboard_handoff", lambda: enabled)


# --- auto-paste path (Windows/macOS) --------------------------------------


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


# --- clipboard-handoff path (Linux) ---------------------------------------


def test_insert_text_uses_clipboard_handoff_without_paste(monkeypatch, mocker):
    _set_handoff(monkeypatch, True)
    inserter = TextInserter()
    inserter.keyboard = MagicMock()
    copy = mocker.patch.object(inserter, "_clipboard_copy")
    via_clipboard = mocker.patch.object(inserter, "_insert_via_clipboard")

    assert inserter.insert_text("hello world") is True

    copy.assert_called_once_with("hello world")
    via_clipboard.assert_not_called()
    inserter.keyboard.press.assert_not_called()  # no synthetic Ctrl+V


def test_insert_space_then_text_uses_handoff_without_space(monkeypatch, mocker):
    _set_handoff(monkeypatch, True)
    inserter = TextInserter()
    inserter.keyboard = MagicMock()
    copy = mocker.patch.object(inserter, "_clipboard_copy")

    assert inserter.insert_space_then_text("world") is True

    copy.assert_called_once_with("world")
    inserter.keyboard.press.assert_not_called()


def test_handoff_copy_failure_raises_text_insertion_error(monkeypatch, mocker):
    _set_handoff(monkeypatch, True)
    inserter = TextInserter()
    mocker.patch.object(
        inserter, "_clipboard_copy", side_effect=RuntimeError("no clipboard tool")
    )

    with pytest.raises(TextInsertionError):
        inserter.insert_text("hello")


def test_clipboard_copy_prefers_wl_copy_on_wayland(monkeypatch, mocker):
    inserter = TextInserter()
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    mocker.patch("src.text_inserter.shutil.which", return_value="/usr/bin/wl-copy")
    run = mocker.patch("src.text_inserter.subprocess.run")
    copy = mocker.patch("src.text_inserter.pyperclip.copy")

    inserter._clipboard_copy("hello")

    run.assert_called_once()
    assert run.call_args.args[0] == ["/usr/bin/wl-copy"]
    assert run.call_args.kwargs["input"] == b"hello"
    copy.assert_not_called()


def test_clipboard_copy_falls_back_to_pyperclip_without_wayland(monkeypatch, mocker):
    inserter = TextInserter()
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    run = mocker.patch("src.text_inserter.subprocess.run")
    copy = mocker.patch("src.text_inserter.pyperclip.copy")

    inserter._clipboard_copy("hello")

    run.assert_not_called()
    copy.assert_called_once_with("hello")


def test_clipboard_copy_falls_back_when_wl_copy_missing(monkeypatch, mocker):
    inserter = TextInserter()
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    mocker.patch("src.text_inserter.shutil.which", return_value=None)
    copy = mocker.patch("src.text_inserter.pyperclip.copy")

    inserter._clipboard_copy("hello")

    copy.assert_called_once_with("hello")


def test_clipboard_copy_falls_back_when_wl_copy_errors(monkeypatch, mocker):
    inserter = TextInserter()
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    mocker.patch("src.text_inserter.shutil.which", return_value="/usr/bin/wl-copy")
    mocker.patch("src.text_inserter.subprocess.run", side_effect=RuntimeError("boom"))
    copy = mocker.patch("src.text_inserter.pyperclip.copy")

    inserter._clipboard_copy("hello")

    copy.assert_called_once_with("hello")
