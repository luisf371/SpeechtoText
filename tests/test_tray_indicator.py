"""Tests for system tray recording indicator assets."""

import sys
import threading
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("PIL")

from src.tray_indicator import TrayIndicator, make_tray_image


def test_tray_images_have_expected_modes():
    idle = make_tray_image("idle")
    ready = make_tray_image("ready")
    push = make_tray_image("push-to-talk")
    toggle = make_tray_image("toggle")

    assert idle.size == (64, 64)
    assert ready.size == (64, 64)
    assert push.size == (64, 64)
    assert toggle.size == (64, 64)
    assert idle.getpixel((32, 32)) != push.getpixel((32, 32))
    assert idle.getpixel((32, 32)) != ready.getpixel((32, 32))
    assert ready.getpixel((32, 32)) != push.getpixel((32, 32))
    assert push.tobytes() != toggle.tobytes()


def test_ready_tray_image_is_green():
    r, g, b, a = make_tray_image("ready").getpixel((32, 32))
    assert g > r and g > b and a == 255


def test_push_to_talk_tray_image_is_red():
    r, g, b, a = make_tray_image("push-to-talk").getpixel((32, 32))
    assert r > g and r > b and a == 255


def test_tray_image_has_circular_alpha_mask():
    image = make_tray_image("push-to-talk")

    assert image.getpixel((0, 0)) == (0, 0, 0, 0)
    assert image.getpixel((63, 0)) == (0, 0, 0, 0)
    assert image.getpixel((0, 63)) == (0, 0, 0, 0)
    assert image.getpixel((63, 63)) == (0, 0, 0, 0)
    assert image.getpixel((32, 32))[3] == 255
    assert image.getpixel((5, 32))[3] > 100
    assert image.getpixel((32, 5))[3] > 100


def test_toggle_tray_image_contains_lock_mark():
    toggle = make_tray_image("toggle")
    pixels = (
        toggle.get_flattened_data()
        if hasattr(toggle, "get_flattened_data")
        else toggle.getdata()
    )

    white_pixels = sum(
        1
        for pixel in pixels
        if pixel[0] > 240 and pixel[1] > 240 and pixel[2] > 240 and pixel[3] > 240
    )

    assert white_pixels > 50


def test_sni_pixmap_conversion_is_argb_big_endian():
    from src.sni_tray import _argb_bytes

    image = make_tray_image("ready")
    width, height, data = _argb_bytes(image)
    assert (width, height) == (64, 64)
    assert len(data) == 64 * 64 * 4
    # Centre pixel is opaque green; ARGB byte order => A,R,G,B.
    offset = (32 * 64 + 32) * 4
    a, r, g, b = data[offset : offset + 4]
    assert a == 255 and g > r and g > b


@pytest.mark.integration
def test_sni_backend_registers_with_live_watcher():
    sni_mod = pytest.importorskip("src.sni_tray")
    if not sni_mod.sni_host_available():
        pytest.skip("No StatusNotifierWatcher host on the session bus")

    from jeepney import DBusAddress, Properties
    from jeepney.io.blocking import open_dbus_connection

    indicator = TrayIndicator()
    assert indicator.start() is True
    assert indicator._sni is not None

    conn = open_dbus_connection(bus="SESSION")
    try:
        unique = indicator._sni._conn.unique_name
        item = DBusAddress(
            "/StatusNotifierItem",
            bus_name=unique,
            interface="org.kde.StatusNotifierItem",
        )
        title = conn.send_and_get_reply(
            Properties(item).get("Title"), timeout=2
        ).body[0][1]
        assert title == "PushToTalk - idle"

        indicator.update("push-to-talk")
        deadline = time.monotonic() + 2
        title2 = title
        while title2 != "PushToTalk - recording" and time.monotonic() < deadline:
            title2 = conn.send_and_get_reply(
                Properties(item).get("Title"), timeout=2
            ).body[0][1]
            time.sleep(0.02)
        assert title2 == "PushToTalk - recording"
    finally:
        indicator.stop()
        conn.close()


class _FakeIcon:
    def __init__(self):
        self.icon_updates = []
        self.title_updates = []
        self.stopped = False
        self.visible = True

    @property
    def icon(self):
        return self.icon_updates[-1][1] if self.icon_updates else None

    @icon.setter
    def icon(self, value):
        self.icon_updates.append((threading.current_thread().name, value))

    @property
    def title(self):
        return self.title_updates[-1][1] if self.title_updates else None

    @title.setter
    def title(self, value):
        self.title_updates.append((threading.current_thread().name, value))

    def stop(self):
        self.stopped = True


def test_tray_updates_are_serialized_and_coalesced_to_latest_mode():
    indicator = TrayIndicator()
    fake_icon = _FakeIcon()
    indicator._icon = fake_icon
    indicator._stop_event.clear()
    indicator._update_thread = threading.Thread(
        target=indicator._update_loop,
        daemon=True,
        name="PushToTalkTrayUpdate",
    )
    indicator._update_thread.start()

    for mode in ["push-to-talk", "idle", "toggle", "idle", "push-to-talk"]:
        indicator.update(mode)

    deadline = time.monotonic() + 2
    while fake_icon.title != "PushToTalk - recording" and time.monotonic() < deadline:
        time.sleep(0.01)

    indicator.stop()

    assert fake_icon.title == "PushToTalk - recording"
    assert fake_icon.stopped is True
    assert {name for name, _value in fake_icon.icon_updates} == {
        "PushToTalkTrayUpdate"
    }
    assert {name for name, _value in fake_icon.title_updates} == {
        "PushToTalkTrayUpdate"
    }


def test_redundant_updates_do_not_touch_backend():
    indicator = TrayIndicator()
    fake_icon = _FakeIcon()
    indicator._icon = fake_icon
    indicator._displayed_mode = None
    indicator._stop_event.clear()
    indicator._update_thread = threading.Thread(
        target=indicator._update_loop,
        daemon=True,
        name="PushToTalkTrayUpdate",
    )
    indicator._update_thread.start()

    indicator.update("ready")
    deadline = time.monotonic() + 2
    while fake_icon.title != "PushToTalk - ready" and time.monotonic() < deadline:
        time.sleep(0.01)
    updates_after_first = len(fake_icon.icon_updates)

    # Re-sending the same mode must not write to the backend again.
    for _ in range(5):
        indicator.update("ready")
    time.sleep(0.3)

    indicator.stop()

    assert fake_icon.title == "PushToTalk - ready"
    assert len(fake_icon.icon_updates) == updates_after_first


def test_tray_start_handles_immediate_idle_update(monkeypatch):
    created_icons = []

    class FakeMenu:
        def __init__(self, *items):
            self.items = items

    class FakeMenuItem:
        def __init__(self, text, action):
            self.text = text
            self.action = action

    class FakePystrayIcon(_FakeIcon):
        def __init__(self, name, image, title, menu):
            super().__init__()
            self.name = name
            self.icon = image
            self.title = title
            self.menu = menu
            self.visible = False
            self._stopped = threading.Event()
            created_icons.append(self)

        def run(self, setup=None):
            if setup:
                setup(self)
            self._stopped.wait(timeout=2)

        def stop(self):
            super().stop()
            self._stopped.set()

    fake_pystray = SimpleNamespace(
        Icon=FakePystrayIcon,
        Menu=FakeMenu,
        MenuItem=FakeMenuItem,
    )
    monkeypatch.setitem(sys.modules, "pystray", fake_pystray)

    indicator = TrayIndicator()
    indicator._enable_sni = False  # exercise the pystray fallback path
    assert indicator.start() is True
    indicator.update("idle")

    deadline = time.monotonic() + 2
    while created_icons[0].title != "PushToTalk - idle" and time.monotonic() < deadline:
        time.sleep(0.01)

    indicator.stop()

    assert created_icons[0].title == "PushToTalk - idle"
    assert created_icons[0].stopped is True
