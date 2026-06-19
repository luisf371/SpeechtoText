"""Platform/session helpers for OS-specific text-insertion behavior.

These helpers centralize the policy decision of *how* transcribed text reaches
the user's active window so the rest of the codebase does not scatter
``sys.platform`` checks. Call the functions through the module
(``platform_support.use_clipboard_handoff()``) so the behavior stays easy to
override in tests.
"""

import sys


def is_linux() -> bool:
    """Return True when running on Linux."""
    return sys.platform.startswith("linux")


def use_clipboard_handoff() -> bool:
    """Return True when transcribed text should be handed off via the clipboard.

    On Linux (notably Wayland, e.g. KDE Plasma) the app cannot reliably
    synthesize a Ctrl+V paste or type characters into other windows: the
    compositor blocks synthetic input injection from arbitrary clients. Instead
    of silently failing, Linux copies the finalized transcript to the clipboard,
    plays a distinct "ready" sound, and lets the user paste it manually.

    Windows and macOS keep the automatic clipboard-paste behavior, so this
    returns False there.

    Because the clipboard can only be pasted once, this mode also forces the
    complete (non-streaming) transcription path; see
    ``PushToTalkConfig.is_parakeet_streaming_active``.
    """
    return is_linux()
