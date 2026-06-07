"""Hotkey configuration section — CustomTkinter version."""

import tkinter as tk
from tkinter import messagebox
from typing import Optional
import customtkinter as ctk

from src.gui.hotkey_recorder import HotkeyRecorder, SHIFT_NUMBER_TO_SYMBOL

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

        self._recording_target: Optional[str] = None
        self._previous_value: str = ""
        self._current_keys: set[str] = set()
        self._captured_keys: set[str] = set()
        self._capture_widget = None
        self._key_press_binding_id: str | None = None
        self._key_release_binding_id: str | None = None
        self._recording_timeout_job: str | None = None
        self._recording_stabilization_job: str | None = None

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
        if self._recording_target:
            return

        self._recording_target = target
        if target == "hotkey":
            var, record_btn, other_btn = self.hotkey_var, self._hotkey_record_btn, self._toggle_record_btn
            entry = self._hotkey_entry
        else:
            var, record_btn, other_btn = self.toggle_hotkey_var, self._toggle_record_btn, self._hotkey_record_btn
            entry = self._toggle_entry

        self._previous_value = var.get()
        self._current_keys.clear()
        self._captured_keys.clear()

        if record_btn:
            record_btn.configure(text="Recording…", fg_color=C_ACCENT, text_color="#1a1205")
        if other_btn:
            other_btn.configure(state="disabled")
        if entry:
            entry.configure(state="disabled")

        self._start_local_key_capture()

    def _start_local_key_capture(self):
        self._capture_widget = self.frame.winfo_toplevel()
        self._capture_widget.focus_force()
        self._key_press_binding_id = self._capture_widget.bind(
            "<KeyPress>", self._on_recording_key_press, add="+"
        )
        self._key_release_binding_id = self._capture_widget.bind(
            "<KeyRelease>", self._on_recording_key_release, add="+"
        )
        self._recording_timeout_job = self.frame.after(
            10000, self._on_recording_timeout
        )

    def _stop_local_key_capture(self):
        if self._capture_widget is not None and self._key_press_binding_id:
            self._capture_widget.unbind("<KeyPress>", self._key_press_binding_id)
            self._key_press_binding_id = None
        if self._capture_widget is not None and self._key_release_binding_id:
            self._capture_widget.unbind("<KeyRelease>", self._key_release_binding_id)
            self._key_release_binding_id = None
        self._capture_widget = None
        if self._recording_timeout_job:
            self.frame.after_cancel(self._recording_timeout_job)
            self._recording_timeout_job = None
        if self._recording_stabilization_job:
            self.frame.after_cancel(self._recording_stabilization_job)
            self._recording_stabilization_job = None

    def _on_recording_key_press(self, event):
        if not self._recording_target:
            return None

        if self._recording_stabilization_job:
            self.frame.after_cancel(self._recording_stabilization_job)
            self._recording_stabilization_job = None

        key_name = self._event_to_key_name(event)
        if not key_name:
            return "break"

        if key_name == "esc":
            self._on_recording_cancelled()
            return "break"

        self._current_keys.add(key_name)
        self._captured_keys.add(key_name)
        self._on_keys_changed(HotkeyRecorder._format_hotkey_string(self._current_keys))
        return "break"

    def _on_recording_key_release(self, event):
        if not self._recording_target:
            return None

        key_name = self._event_to_key_name(event)
        if key_name:
            if key_name not in self._current_keys and "shift" in self._captured_keys:
                key_name = SHIFT_NUMBER_TO_SYMBOL.get(key_name, key_name)
            self._current_keys.discard(key_name)

        if not self._current_keys and self._captured_keys:
            self._recording_stabilization_job = self.frame.after(
                int(HotkeyRecorder.STABILIZATION_DELAY * 1000),
                self._finalize_recording,
            )
        return "break"

    def _on_recording_timeout(self):
        self._recording_timeout_job = None
        self._on_recording_cancelled()

    def _finalize_recording(self):
        self._recording_stabilization_job = None
        hotkey_string = HotkeyRecorder._format_hotkey_string(self._captured_keys)
        if hotkey_string:
            self._on_recording_complete(hotkey_string)
        else:
            self._on_recording_cancelled()

    @staticmethod
    def _event_to_key_name(event) -> str | None:
        keysym = (getattr(event, "keysym", "") or "").lower()
        char = getattr(event, "char", "") or ""

        aliases = {
            "alt_l": "alt",
            "alt_r": "alt",
            "backspace": "backspace",
            "control_l": "ctrl",
            "control_r": "ctrl",
            "delete": "delete",
            "escape": "esc",
            "iso_left_tab": "tab",
            "meta_l": "cmd",
            "meta_r": "cmd",
            "minus": "-",
            "next": "page_down",
            "prior": "page_up",
            "return": "enter",
            "shift_l": "shift",
            "shift_r": "shift",
            "space": "space",
            "super_l": "cmd",
            "super_r": "cmd",
            "tab": "tab",
        }
        if keysym in aliases:
            return aliases[keysym]

        symbols = {
            "ampersand": "&",
            "asciicircum": "^",
            "asterisk": "*",
            "at": "@",
            "dollar": "$",
            "exclam": "!",
            "numbersign": "#",
            "parenleft": "(",
            "parenright": ")",
            "percent": "%",
        }
        if keysym in symbols:
            return symbols[keysym]

        if len(char) == 1 and char.isprintable():
            if char == " ":
                return "space"
            if char.isalnum():
                return char.lower()
            return char

        if keysym.startswith("f") and keysym[1:].isdigit():
            return keysym
        if keysym:
            return keysym.replace(" ", "_")
        return None

    def _on_recording_complete(self, hotkey_string: str):
        if self._recording_target == "hotkey":
            if hotkey_string == self.toggle_hotkey_var.get().strip():
                messagebox.showerror(
                    "Duplicate Hotkey",
                    "Push-to-talk and toggle hotkeys must be different.",
                    parent=self.frame.winfo_toplevel(),
                )
                self.hotkey_var.set(self._previous_value)
                self._reset_recording_state()
                return
            self.hotkey_var.set(hotkey_string)
        else:
            if hotkey_string == self.hotkey_var.get().strip():
                messagebox.showerror(
                    "Duplicate Hotkey",
                    "Push-to-talk and toggle hotkeys must be different.",
                    parent=self.frame.winfo_toplevel(),
                )
                self.toggle_hotkey_var.set(self._previous_value)
                self._reset_recording_state()
                return
            self.toggle_hotkey_var.set(hotkey_string)
        self._reset_recording_state()

    def _on_recording_cancelled(self):
        if self._recording_target == "hotkey":
            self.hotkey_var.set(self._previous_value)
        else:
            self.toggle_hotkey_var.set(self._previous_value)
        self._reset_recording_state()

    def _on_keys_changed(self, current_keys: str):
        val = f"Recording: {current_keys}" if current_keys else "Recording…"
        if self._recording_target == "hotkey":
            btn = self._hotkey_record_btn
        else:
            btn = self._toggle_record_btn
        if btn:
            btn.configure(text=val)

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
        self._stop_local_key_capture()
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
