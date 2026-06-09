"""System tray recording indicator."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from functools import lru_cache
from typing import Any, Literal

from loguru import logger

# "idle" = started but not connected (gray)
# "ready" = connected but not recording (green)
# "push-to-talk" = recording while a key is held (red)
# "toggle" = recording locked on (red with a lock mark)
RecordingMode = Literal["idle", "ready", "push-to-talk", "toggle"]

ICON_SIZE = 64
_ICON_SCALE = 8
_NEUTRAL = (108, 116, 126, 255)
_READY = (39, 174, 96, 255)
_RECORDING = (220, 54, 62, 255)
_WHITE = (255, 255, 255, 255)
_SHADOW = (120, 20, 24, 255)

# Bursts of rapid mode changes (e.g. tapping a push-to-talk key quickly) are
# coalesced and rate-limited before touching the tray backend. Hammering the
# backend from a worker thread is what makes the icon flicker and, on some
# Linux tray backends, crash the process.
_UPDATE_DEBOUNCE = 0.06

_FILL_BY_MODE: dict[str, tuple[int, int, int, int]] = {
    "idle": _NEUTRAL,
    "ready": _READY,
    "push-to-talk": _RECORDING,
    "toggle": _RECORDING,
}


def make_tray_image(mode: RecordingMode) -> Any:
    """Create an in-memory tray icon for the requested recording mode."""
    return _make_tray_image(mode).copy()


@lru_cache(maxsize=8)
def _make_tray_image(mode: RecordingMode) -> Any:
    """Create a cached tray icon source image for the requested recording mode."""
    from PIL import ImageDraw

    fill = _FILL_BY_MODE.get(mode, _NEUTRAL)
    image = _make_circle_image(fill)

    if mode == "toggle":
        draw = ImageDraw.Draw(image)
        _draw_lock(draw)

    return image


def _make_circle_image(fill: tuple[int, int, int, int]) -> Any:
    """Create a round icon with an antialiased alpha mask for tray backends."""
    from PIL import Image, ImageDraw

    high_size = ICON_SIZE * _ICON_SCALE
    # A small even margin keeps the disc from touching the edges while still
    # filling the tray slot so it reads as a circle rather than a square.
    margin = 3 * _ICON_SCALE
    bounds = (margin, margin, high_size - margin - 1, high_size - margin - 1)

    mask = Image.new("L", (high_size, high_size), 0)
    ImageDraw.Draw(mask).ellipse(bounds, fill=255)
    mask = mask.resize((ICON_SIZE, ICON_SIZE), Image.Resampling.LANCZOS)

    image = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    solid = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), fill)
    image.paste(solid, (0, 0), mask)
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
        self._update_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._backend_lock = threading.Lock()
        self._update_event = threading.Event()
        self._stop_event = threading.Event()
        self._mode: RecordingMode = "idle"
        self._pending_mode: RecordingMode = "idle"
        self._displayed_mode: RecordingMode | None = None
        self._sni = None
        # SNI (StatusNotifierItem) is the correct backend for modern Linux
        # desktops (KDE Plasma, GNOME, Wayland). pystray's XEMBED fallback
        # renders a broken square there. Disabled in tests that exercise the
        # pystray path directly.
        self._enable_sni = True

    @property
    def mode(self) -> RecordingMode:
        return self._mode

    def start(self) -> bool:
        """Start the tray icon, preferring the native SNI backend on Linux."""
        if self._start_sni():
            return True
        return self._start_pystray()

    def _start_sni(self) -> bool:
        """Try the native StatusNotifierItem backend (Linux desktops)."""
        import sys

        if not self._enable_sni or sys.platform != "linux":
            return False
        with self._lock:
            if self._sni is not None or self._icon is not None:
                return True
        try:
            from src.sni_tray import SniTrayIcon, sni_host_available

            if not sni_host_available():
                return False
            image = make_tray_image(self._mode)
            sni = SniTrayIcon(
                app_id="PushToTalk",
                title=self._title_for_mode(self._mode),
                image=image,
                tooltip=self._title_for_mode(self._mode),
                on_activate=self._handle_show,
                menu_items=[
                    ("Show Configuration", self._handle_show),
                    ("Stop Application", self._handle_stop),
                    ("Quit", self._handle_quit),
                ],
            )
            if not sni.start():
                sni.stop()
                return False
        except Exception as error:
            logger.info(f"Native SNI tray unavailable: {error}")
            return False

        with self._lock:
            self._sni = sni
            self._displayed_mode = self._mode
        logger.info("Using native StatusNotifierItem tray backend")
        return True

    def _start_pystray(self) -> bool:
        """Start the tray icon if the optional pystray backend is available."""
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
            self._stop_event.clear()
            self._update_event.clear()
            self._displayed_mode = self._mode
            self._icon = pystray.Icon(
                "PushToTalk",
                image,
                self._title_for_mode(self._mode),
                menu,
            )
            self._update_thread = threading.Thread(
                target=self._update_loop,
                daemon=True,
                name="PushToTalkTrayUpdate",
            )
            self._thread = threading.Thread(
                target=self._run_icon,
                daemon=True,
                name="PushToTalkTray",
            )
            self._update_thread.start()
            self._thread.start()
            return True

    def stop(self) -> None:
        """Stop the tray icon if it is running."""
        with self._lock:
            sni = self._sni
            self._sni = None
            icon = self._icon
            thread = self._thread
            update_thread = self._update_thread
            self._icon = None
            self._thread = None
            self._update_thread = None

        if sni is not None:
            try:
                sni.stop()
            except Exception as error:
                logger.debug(f"Error stopping SNI tray: {error}")

        self._stop_event.set()
        self._update_event.set()
        current_thread = threading.current_thread()
        if (
            update_thread
            and update_thread.is_alive()
            and update_thread is not current_thread
        ):
            update_thread.join(timeout=1.0)

        if icon:
            try:
                with self._backend_lock:
                    icon.stop()
            except Exception as error:
                logger.debug(f"Error stopping tray icon: {error}")
        if thread and thread.is_alive() and thread is not current_thread:
            thread.join(timeout=1.0)

    def update(self, mode: RecordingMode) -> None:
        """Update the tray icon to idle, ready, push-to-talk, or toggle state."""
        with self._lock:
            self._mode = mode
            self._pending_mode = mode
            sni = self._sni
            icon = self._icon

        if sni is not None:
            if self._displayed_mode != mode:
                self._displayed_mode = mode
                try:
                    sni.update(
                        image=make_tray_image(mode),
                        title=self._title_for_mode(mode),
                        tooltip=self._title_for_mode(mode),
                    )
                except Exception as error:
                    logger.debug(f"Error updating SNI tray: {error}")
            return

        if not icon:
            return

        self._update_event.set()

    def _update_loop(self) -> None:
        while not self._stop_event.is_set():
            self._update_event.wait()
            if self._stop_event.is_set():
                break

            # Let a burst of changes settle, then apply only the final state.
            # This coalesces rapid toggles into a single backend update.
            self._update_event.clear()
            self._stop_event.wait(_UPDATE_DEBOUNCE)
            if self._stop_event.is_set():
                break
            self._update_event.clear()

            with self._lock:
                icon = self._icon
                mode = self._pending_mode
            if icon and self._wait_until_visible(icon):
                self._apply_update(icon, mode)

    def _wait_until_visible(self, icon: Any) -> bool:
        deadline = time.monotonic() + 2.0
        while not self._stop_event.is_set():
            with self._lock:
                if self._icon is not icon:
                    return False
            try:
                if getattr(icon, "visible", False):
                    return True
            except Exception:
                return False
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.01)
        return False

    def _apply_update(self, icon: Any, mode: RecordingMode) -> None:
        try:
            with self._backend_lock:
                with self._lock:
                    if self._icon is not icon:
                        return
                    if self._displayed_mode == mode:
                        # Nothing changed visually; skip the backend write so the
                        # tray icon does not flicker or churn.
                        return
                icon.icon = make_tray_image(mode)
                icon.title = self._title_for_mode(mode)
                with self._lock:
                    if self._icon is icon:
                        self._displayed_mode = mode
        except Exception as error:
            logger.debug(f"Error updating tray icon: {error}")

    def _run_icon(self) -> None:
        with self._lock:
            icon = self._icon
        if not icon:
            return
        try:
            icon.run(self._setup_icon)
        except Exception as error:
            logger.warning(f"System tray icon stopped unexpectedly: {error}")
        finally:
            with self._lock:
                if self._icon is icon:
                    self._icon = None
                    self._thread = None
                    self._update_thread = None
                    self._stop_event.set()
                    self._update_event.set()

    def _setup_icon(self, icon: Any) -> None:
        with self._backend_lock:
            with self._lock:
                if self._icon is not icon or self._stop_event.is_set():
                    return
            icon.visible = True
        self._update_event.set()

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
        if mode == "ready":
            return "PushToTalk - ready"
        return "PushToTalk - idle"
