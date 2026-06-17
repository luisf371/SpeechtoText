import os
import shutil
import subprocess
import time
from loguru import logger
import sys
import threading
from typing import Optional

import pyperclip
from pynput import keyboard

from src import platform_support
from src.config.constants import (
    TEXT_INSERTION_DELAY_AFTER_COPY_SECONDS,
    TEXT_INSERTION_DELAY_AFTER_PASTE_SECONDS,
)
from src.exceptions import TextInsertionError


class TextInserter:
    # Default insertion delay in seconds
    DEFAULT_INSERTION_DELAY = 0.005

    def __init__(self):
        """Initialize the text inserter."""
        self.insertion_delay = self.DEFAULT_INSERTION_DELAY
        self.keyboard = keyboard.Controller()
        self._insert_lock = threading.Lock()

    def insert_text(self, text: str) -> bool:
        """
        Insert text into the currently active window using clipboard method.

        Args:
            text: Text to insert

        Returns:
            True if insertion was successful, False otherwise
        """
        if not text:
            logger.warning("Empty text provided for insertion")
            return False

        with self._insert_lock:
            # Linux (Wayland/KDE) cannot reliably synthesize a paste keystroke
            # into other windows, so we stage the text on the clipboard and let
            # the user paste it manually (see platform_support.use_clipboard_handoff).
            if platform_support.use_clipboard_handoff():
                return self._copy_for_manual_paste(text)

            try:
                return self._insert_via_clipboard(text)
            except TextInsertionError as clipboard_error:
                logger.warning(
                    f"Clipboard insertion unavailable; falling back to direct typing: "
                    f"{clipboard_error}"
                )
                return self._insert_directly(text)
            except Exception as e:
                logger.error(f"Text insertion failed: {e}")
                raise TextInsertionError(f"Failed to insert text: {e}") from e

    def insert_space(self) -> bool:
        """Insert a single literal space with the keyboard."""
        with self._insert_lock:
            return self._insert_space_unlocked()

    def insert_space_then_text(self, text: str) -> bool:
        """Insert a space keypress followed by clipboard text as one operation."""
        if not text:
            logger.warning("Empty text provided for insertion")
            return False

        with self._insert_lock:
            # In manual-paste mode the user pastes the whole transcript at once,
            # so boundary spaces are irrelevant; just stage the text.
            if platform_support.use_clipboard_handoff():
                return self._copy_for_manual_paste(text)

            self._insert_space_unlocked()
            try:
                return self._insert_via_clipboard(text)
            except TextInsertionError as clipboard_error:
                logger.warning(
                    f"Clipboard insertion unavailable; falling back to direct typing: "
                    f"{clipboard_error}"
                )
                return self._insert_directly(text)

    def _insert_space_unlocked(self) -> bool:
        """Insert a single literal space; caller must hold the insertion lock."""
        try:
            from src.hotkey_service import suppress_hotkey_events

            with suppress_hotkey_events():
                self.keyboard.press(keyboard.Key.space)
                self.keyboard.release(keyboard.Key.space)
            time.sleep(TEXT_INSERTION_DELAY_AFTER_PASTE_SECONDS)
            logger.info("Space inserted via keyboard")
            return True
        except Exception as e:
            logger.error(f"Space insertion failed: {e}")
            raise TextInsertionError(f"Space insertion failed: {e}") from e

    def _insert_via_clipboard(self, text: str) -> bool:
        """Insert text by copying to clipboard and pasting."""
        original_clipboard = None
        try:
            # On Linux we intentionally leave the inserted text on the clipboard
            # (see _restore_clipboard), so reading the previous contents here is
            # pure overhead: pyperclip.paste() shells out to xclip/xsel, spawning
            # a subprocess before every paste (and every streaming segment).
            if not sys.platform.startswith("linux"):
                original_clipboard = pyperclip.paste()
            pyperclip.copy(text)

            time.sleep(TEXT_INSERTION_DELAY_AFTER_COPY_SECONDS)

            # Use platform-specific modifier key for paste
            modifier_key = (
                keyboard.Key.cmd if sys.platform == "darwin" else keyboard.Key.ctrl
            )

            from src.hotkey_service import suppress_hotkey_events

            # Press modifier+v to paste
            with suppress_hotkey_events():
                with self.keyboard.pressed(modifier_key):
                    self.keyboard.press("v")
                    self.keyboard.release("v")

            time.sleep(TEXT_INSERTION_DELAY_AFTER_PASTE_SECONDS)

            logger.info(f"Text inserted via clipboard: {len(text)} characters")
            return True

        except Exception as e:
            logger.error(f"Clipboard insertion failed: {e}")
            raise TextInsertionError(f"Clipboard insertion failed: {e}") from e
        finally:
            if original_clipboard is not None:
                self._restore_clipboard(original_clipboard)

    def _copy_for_manual_paste(self, text: str) -> bool:
        """Stage text on the clipboard for the user to paste manually (Linux handoff).

        No paste keystroke is synthesized. The caller is responsible for any
        "ready to paste" feedback. Caller must hold the insertion lock.
        """
        try:
            self._clipboard_copy(text)
        except Exception as e:
            logger.error(f"Clipboard copy for manual paste failed: {e}")
            raise TextInsertionError(
                f"Clipboard copy for manual paste failed: {e}"
            ) from e

        logger.info(
            f"Transcript copied to clipboard for manual paste: {len(text)} characters"
        )
        return True

    def _clipboard_copy(self, text: str) -> None:
        """Copy text to the system clipboard, preferring native Wayland tooling.

        On Wayland, ``wl-copy`` (wl-clipboard) is the correct tool; pyperclip's
        xclip/xsel path only reaches the XWayland clipboard. Falls back to
        pyperclip when wl-copy is unavailable or not on Wayland.
        """
        if self._try_wayland_copy(text):
            return
        pyperclip.copy(text)

    @staticmethod
    def _try_wayland_copy(text: str) -> bool:
        """Copy via wl-copy when running on Wayland; return False to fall back."""
        if not os.environ.get("WAYLAND_DISPLAY"):
            return False
        wl_copy = shutil.which("wl-copy")
        if not wl_copy:
            logger.debug("wl-copy not found on Wayland; falling back to pyperclip")
            return False
        try:
            subprocess.run(
                [wl_copy],
                input=text.encode("utf-8"),
                check=True,
                timeout=5,
            )
            return True
        except Exception as e:
            logger.debug(f"wl-copy failed; falling back to pyperclip: {e}")
            return False

    def _restore_clipboard(self, original_clipboard: str) -> None:
        if sys.platform.startswith("linux"):
            logger.debug("Leaving inserted text on Linux clipboard after paste")
            return

        self._copy_clipboard_for_restore(original_clipboard)

    def _copy_clipboard_for_restore(self, text: str) -> None:
        try:
            pyperclip.copy(text)
        except Exception as e:
            logger.debug(f"Could not restore previous clipboard contents: {e}")

    def _insert_directly(self, text: str) -> bool:
        """Insert text by typing it directly through pynput."""
        try:
            from src.hotkey_service import suppress_hotkey_events

            with suppress_hotkey_events():
                if hasattr(self.keyboard, "type"):
                    self.keyboard.type(text)
                else:  # pragma: no cover - compatibility fallback for unusual controllers
                    for char in text:
                        self.keyboard.press(char)
                        self.keyboard.release(char)
                        time.sleep(self.insertion_delay)

            time.sleep(TEXT_INSERTION_DELAY_AFTER_PASTE_SECONDS)
            logger.info(f"Text inserted via direct typing: {len(text)} characters")
            return True
        except Exception as e:
            logger.error(f"Direct text insertion failed: {e}")
            raise TextInsertionError(f"Direct text insertion failed: {e}") from e

    def _get_clipboard_text(self) -> Optional[str]:
        """Get current clipboard text content."""
        try:
            return pyperclip.paste()
        except Exception:
            return None

    def _set_clipboard_text(self, text: str) -> None:
        """Set clipboard text content."""
        pyperclip.copy(text)

    def get_active_window_title(self) -> Optional[str]:
        """
        Get the title of the currently active window.

        Note: This functionality is not available without pyautogui.
        Returns None for logging purposes.

        Returns:
            None (window title detection not implemented)
        """
        # Window title detection was removed to eliminate pyautogui dependency
        # This is only used for logging, so returning None is acceptable
        return None
