"""Tests for system tray recording indicator assets."""

import pytest

pytest.importorskip("PIL")

from src.tray_indicator import make_tray_image


def test_tray_images_have_expected_modes():
    idle = make_tray_image("idle")
    push = make_tray_image("push-to-talk")
    toggle = make_tray_image("toggle")

    assert idle.size == (64, 64)
    assert push.size == (64, 64)
    assert toggle.size == (64, 64)
    assert idle.getpixel((32, 32)) != push.getpixel((32, 32))
    assert push.tobytes() != toggle.tobytes()


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
