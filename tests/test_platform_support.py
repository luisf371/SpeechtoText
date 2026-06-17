"""Tests for the platform-policy helpers that drive clipboard handoff."""

import sys

from src import platform_support


def test_is_linux_true(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert platform_support.is_linux() is True


def test_is_linux_false_on_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert platform_support.is_linux() is False


def test_use_clipboard_handoff_on_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert platform_support.use_clipboard_handoff() is True


def test_use_clipboard_handoff_off_on_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert platform_support.use_clipboard_handoff() is False


def test_use_clipboard_handoff_off_on_macos(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert platform_support.use_clipboard_handoff() is False
