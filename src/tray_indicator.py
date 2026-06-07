"""System tray recording indicator."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, Literal

from loguru import logger

RecordingMode = Literal["idle", "push-to-talk", "toggle"]

ICON_SIZE = 64
_NEUTRAL = (108, 116, 126, 255)
_RECORDING = (220, 54, 62, 255)
_WHITE = (255, 255, 255, 255)
_SHADOW = (120, 20, 24, 255)


def make_tray_image(mode: RecordingMode) -> Any:
    """Create an in-memory tray icon for the requested recording mode."""
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    fill = _RECORDING if mode in {"push-to-talk", "toggle"} else _NEUTRAL
    draw.ellipse((6, 6, 58, 58), fill=fill)

    if mode == "toggle":
        _draw_lock(draw)

    return image


def _draw_lock(draw: Any) -> None:
    """Draw a small white lock over the red recording dot."""
    draw.rounded_rectangle((23, 30, 41, 46), radius=3, fill=_WHITE)
    draw.arc((24, 17, 40, 37), start=180, end=360, fill=_SHADOW, width=8)
    draw.arc((24, 17, 40, 37), start=180, end=360, fill=_WHITE, width=5)
    draw.rectangle((22, 28, 42, 34), fill=_RECORDING)
    draw.rounded_rectangle((23, 31, 41, 46), radius=3, fill=_WHITE)


class TrayIndicator:
    """Cross-platform tray indicator with optional pystray backend."""

    def __init__(
        self,
        *,
        on_show: Callable[[], None] | None = None,
        on_stop: Callable[[], None] | None = None,
        on_quit: Callable[[], None] | None = None,
    ):
        self.on_show = on_show
        self.on_stop = on_stop
        self.on_quit = on_quit
        self._icon = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._mode: RecordingMode = "idle"

    @property
    def mode(self) -> RecordingMode:
        return self._mode

    def start(self) -> bool:
        """Start the tray icon if the optional backend is available."""
        with self._lock:
            if self._icon is not None:
                return True

            try:
                import pystray

                image = make_tray_image(self._mode)
            except Exception as error:
                logger.info(f"System tray unavailable: {error}")
                return False

            menu = pystray.Menu(
                pystray.MenuItem("Show Configuration", self._handle_show),
                pystray.MenuItem("Stop Application", self._handle_stop),
                pystray.MenuItem("Quit", self._handle_quit),
            )
            self._icon = pystray.Icon(
                "PushToTalk",
                image,
                self._title_for_mode(self._mode),
                menu,
            )
            self._thread = threading.Thread(
                target=self._run_icon,
                daemon=True,
                name="PushToTalkTray",
            )
            self._thread.start()
            return True

    def stop(self) -> None:
        """Stop the tray icon if it is running."""
        with self._lock:
            icon = self._icon
            thread = self._thread
            self._icon = None
            self._thread = None

        if icon:
            try:
                icon.stop()
            except Exception as error:
                logger.debug(f"Error stopping tray icon: {error}")
        if thread and thread.is_alive():
            thread.join(timeout=1.0)

    def update(self, mode: RecordingMode) -> None:
        """Update the tray icon to idle, push-to-talk recording, or toggle recording."""
        self._mode = mode
        with self._lock:
            icon = self._icon

        if not icon:
            return

        try:
            icon.icon = make_tray_image(mode)
            icon.title = self._title_for_mode(mode)
        except Exception as error:
            logger.debug(f"Error updating tray icon: {error}")

    def _run_icon(self) -> None:
        with self._lock:
            icon = self._icon
        if not icon:
            return
        try:
            icon.run()
        except Exception as error:
            logger.warning(f"System tray icon stopped unexpectedly: {error}")
            with self._lock:
                if self._icon is icon:
                    self._icon = None
                    self._thread = None

    def _handle_show(self, _icon=None, _item=None) -> None:
        if self.on_show:
            self.on_show()

    def _handle_stop(self, _icon=None, _item=None) -> None:
        if self.on_stop:
            self.on_stop()

    def _handle_quit(self, _icon=None, _item=None) -> None:
        if self.on_quit:
            self.on_quit()

    @staticmethod
    def _title_for_mode(mode: RecordingMode) -> str:
        if mode == "toggle":
            return "PushToTalk - recording locked on"
        if mode == "push-to-talk":
            return "PushToTalk - recording"
        return "PushToTalk - idle"
