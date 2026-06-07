"""Hotkey configuration section — CustomTkinter version."""

import tkinter as tk
from typing import Optional
import customtkinter as ctk

from src.gui.hotkey_recorder import HotkeyRecorder

C_WIN     = "#131519"
C_INPUT   = "#0e1013"
C_SURFACE = "#1b1e24"
C_SURFACE2 = "#23272f"
C_BORDER  = "#2a2f38"
C_TEXT    = "#e7e9ec"
C_TEXT2   = "#9ba2ab"
C_TEXT3   = "#6a717b"
C_ACCENT  = "#f5a524"

FONT_SM   = ("Segoe UI", 13)
FONT_BOLD = ("Segoe UI", 14, "bold")
FONT_MONO = ("Consolas", 15)


class HotkeySection:
    """Hotkey configuration — two rows with Record buttons."""

    def __init__(self, parent):
        self.frame = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0)
        self.frame.pack(fill="x")

        self.hotkey_var = tk.StringVar()
        self.toggle_hotkey_var = tk.StringVar()

        self._recorder: Optional[HotkeyRecorder] = None
        self._recording_target: Optional[str] = None
        self._previous_value: str = ""

        self._hotkey_entry: Optional[ctk.CTkEntry] = None
        self._toggle_entry: Optional[ctk.CTkEntry] = None
        self._hotkey_record_btn: Optional[ctk.CTkButton] = None
        self._toggle_record_btn: Optional[ctk.CTkButton] = None

        self._create_widgets()

    def _create_widgets(self):
        f = self.frame

        # Push-to-talk row
        self._hotkey_entry, self._hotkey_record_btn = self._hotkey_row(
            f,
            label="Push-to-Talk Hotkey",
            variable=self.hotkey_var,
            target="hotkey",
        )

        # Toggle row
        self._toggle_entry, self._toggle_record_btn = self._hotkey_row(
            f,
            label="Toggle Recording Hotkey",
            variable=self.toggle_hotkey_var,
            target="toggle_hotkey",
        )

        ctk.CTkLabel(
            f,
            text="⚑  Press keys one at a time. Press Escape to cancel.",
            font=FONT_SM,
            text_color=C_TEXT3,
            fg_color="transparent",
            anchor="w",
        ).pack(anchor="w", pady=(8, 0))

    def _hotkey_row(self, parent, label, variable, target) -> tuple:
        row = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0)
        row.pack(fill="x", pady=(0, 8))

        ctk.CTkLabel(row, text=label, font=FONT_SM, text_color=C_TEXT2,
                     fg_color="transparent", anchor="w").pack(anchor="w", pady=(0, 4))

        controls = ctk.CTkFrame(row, fg_color="transparent", corner_radius=0)
        controls.pack(fill="x")

        # Keycap-style entry — compact, centered, mono
        entry = ctk.CTkEntry(
            controls, textvariable=variable,
            font=FONT_MONO, fg_color=C_INPUT,
            border_color=C_BORDER, text_color=C_TEXT,
            height=33, width=170, corner_radius=7, justify="center",
        )
        entry.pack(side="left")

        # Record button pinned to the right edge (no spacer needed).
        btn = ctk.CTkButton(
            controls, text="Record",
            width=80, height=33, corner_radius=7, font=FONT_SM,
            fg_color=C_SURFACE, text_color=C_TEXT2,
            hover_color=C_SURFACE2, border_width=1, border_color=C_BORDER,
            command=lambda t=target: self._start_recording(t),
        )
        btn.pack(side="right")
        return entry, btn

    def _start_recording(self, target: str):
        if self._recorder and self._recorder.is_recording:
            return

        self._recording_target = target
        if target == "hotkey":
            var, record_btn, other_btn = self.hotkey_var, self._hotkey_record_btn, self._toggle_record_btn
            entry = self._hotkey_entry
        else:
            var, record_btn, other_btn = self.toggle_hotkey_var, self._toggle_record_btn, self._hotkey_record_btn
            entry = self._toggle_entry

        self._previous_value = var.get()

        if record_btn:
            record_btn.configure(text="Recording…", fg_color=C_ACCENT, text_color="#1a1205")
        if other_btn:
            other_btn.configure(state="disabled")
        if entry:
            entry.configure(state="disabled")

        var.set("Press keys…")

        self._recorder = HotkeyRecorder(
            on_recording_complete=self._on_recording_complete,
            on_recording_cancelled=self._on_recording_cancelled,
            on_keys_changed=self._on_keys_changed,
        )
        self._recorder.start_recording(timeout_seconds=10.0)

    def _on_recording_complete(self, hotkey_string: str):
        if self._recording_target == "hotkey":
            self.hotkey_var.set(hotkey_string)
        else:
            self.toggle_hotkey_var.set(hotkey_string)
        self._reset_recording_state()

    def _on_recording_cancelled(self):
        if self._recording_target == "hotkey":
            self.hotkey_var.set(self._previous_value)
        else:
            self.toggle_hotkey_var.set(self._previous_value)
        self._reset_recording_state()

    def _on_keys_changed(self, current_keys: str):
        val = current_keys if current_keys else "Press keys…"
        if self._recording_target == "hotkey":
            self.hotkey_var.set(val)
        else:
            self.toggle_hotkey_var.set(val)

    def _reset_recording_state(self):
        for btn in (self._hotkey_record_btn, self._toggle_record_btn):
            if btn:
                btn.configure(
                    text="Record", state="normal",
                    fg_color=C_SURFACE, text_color=C_TEXT2,
                )
        for entry in (self._hotkey_entry, self._toggle_entry):
            if entry:
                entry.configure(state="normal")
        self._recorder = None
        self._recording_target = None
        self._previous_value = ""

    # ── Public interface ───────────────────────────────────────────────────────

    def get_values(self) -> dict[str, str]:
        return {
            "hotkey": self.hotkey_var.get().strip(),
            "toggle_hotkey": self.toggle_hotkey_var.get().strip(),
        }

    def set_values(self, hotkey: str, toggle_hotkey: str):
        self.hotkey_var.set(hotkey)
        self.toggle_hotkey_var.set(toggle_hotkey)
