import time
from loguru import logger
import sys
import threading
from typing import Optional

import pyperclip
from pynput import keyboard

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
