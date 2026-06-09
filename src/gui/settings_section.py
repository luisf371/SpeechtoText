"""Feature-flags sections — CustomTkinter version."""

import tkinter as tk
import customtkinter as ctk

C_WIN      = "#131519"
C_SURFACE  = "#1b1e24"
C_SURFACE2 = "#23272f"
C_BORDER_S = "#21252c"
C_TEXT     = "#e7e9ec"
C_TEXT2    = "#9ba2ab"
C_TEXT3    = "#6a717b"
C_ACCENT   = "#f5a524"
C_BORDER   = "#2a2f38"

FONT_SM    = ("Segoe UI", 13)
FONT_BOLD  = ("Segoe UI", 14, "bold")
FONT_TITLE = ("Segoe UI", 13, "bold")
FONT_DESC  = ("Segoe UI", 12)


class FeatureFlagsSection:
    """Feature flags split across Refinement tab (toggle) and Advanced tab (rest)."""

    def __init__(self, refinement_parent, advanced_parent):
        self.refinement_toggle_frame = ctk.CTkFrame(
            refinement_parent, fg_color="transparent", corner_radius=0
        )
        self.refinement_toggle_frame.pack(fill="x", pady=(0, 8))

        self.advanced_frame = ctk.CTkFrame(
            advanced_parent, fg_color="transparent", corner_radius=0
        )
        self.advanced_frame.pack(fill="x")

        # Legacy alias
        self.frame = self.advanced_frame

        self.enable_text_refinement_var = tk.BooleanVar()
        self.enable_logging_var = tk.BooleanVar()
        self.enable_audio_feedback_var = tk.BooleanVar()
        self.streaming_boundary_space_keypress_var = tk.BooleanVar()
        self.debug_mode_var = tk.BooleanVar()
        self._refinement_switch: ctk.CTkSwitch | None = None

        self._create_refinement_widgets()
        self._create_advanced_widgets()

    def _make_switch_row(self, parent, variable, label, desc, is_master=False):
        border_color = "#2c2410" if is_master else C_BORDER_S
        row = ctk.CTkFrame(parent, fg_color=C_SURFACE, corner_radius=9,
                           border_width=1, border_color=border_color)
        row.pack(fill="x", pady=(0, 10 if is_master else 4))

        if is_master:
            # height=1 so the bar's requested height doesn't inflate the row;
            # fill="y" then stretches it to the card's natural height.
            bar = ctk.CTkFrame(row, fg_color=C_ACCENT, width=3, height=1, corner_radius=0)
            bar.pack(side="left", fill="y")
            bar.pack_propagate(False)

        inner = ctk.CTkFrame(row, fg_color="transparent", corner_radius=0)
        inner.pack(side="left", fill="x", expand=True, padx=(13, 12), pady=9)

        header = ctk.CTkFrame(inner, fg_color="transparent", corner_radius=0)
        header.pack(fill="x")
        ctk.CTkLabel(
            header, text=label, font=FONT_TITLE,
            text_color=C_TEXT, fg_color="transparent", anchor="w",
        ).pack(side="left")
        sw = ctk.CTkSwitch(
            header, variable=variable, text="",
            onvalue=True, offvalue=False,
            progress_color=C_ACCENT, fg_color=C_SURFACE2,
            button_color=C_TEXT, button_hover_color=C_TEXT,
            switch_width=38, switch_height=20,
        )
        sw.pack(side="right")
        if desc:
            ctk.CTkLabel(
                inner, text=desc, font=FONT_DESC,
                text_color=C_TEXT3, fg_color="transparent", anchor="w",
            ).pack(anchor="w", pady=(2, 0))
        return sw

    def _create_refinement_widgets(self):
        self._refinement_switch = self._make_switch_row(
            self.refinement_toggle_frame,
            self.enable_text_refinement_var,
            "Enable Text Refinement",
            "Run AI cleanup on transcribed text for better quality",
            is_master=True,
        )

    def _create_advanced_widgets(self):
        flags = [
            (self.enable_logging_var,       "Enable Logging",       "Write events to push_to_talk.log"),
            (self.enable_audio_feedback_var, "Audio Feedback", "Play a sound when recording starts and stops"),
            (
                self.streaming_boundary_space_keypress_var,
                "Streaming Space Keypress",
                "Press Space between streamed text chunks",
            ),
            (self.debug_mode_var,            "Debug Mode",           "Save recorded audio files to debug directories"),
        ]
        for var, label, desc in flags:
            self._make_switch_row(self.advanced_frame, var, label, desc)

    # ── Public interface ───────────────────────────────────────────────────────

    def get_values(self) -> dict[str, bool]:
        return {
            "enable_text_refinement": self.enable_text_refinement_var.get(),
            "enable_logging": self.enable_logging_var.get(),
            "enable_audio_feedback": self.enable_audio_feedback_var.get(),
            "streaming_boundary_space_keypress": (
                self.streaming_boundary_space_keypress_var.get()
            ),
            "debug_mode": self.debug_mode_var.get(),
        }

    def set_values(
        self,
        enable_text_refinement,
        enable_logging,
        enable_audio_feedback,
        debug_mode,
        streaming_boundary_space_keypress=True,
    ):
        self.enable_text_refinement_var.set(enable_text_refinement)
        self.enable_logging_var.set(enable_logging)
        self.enable_audio_feedback_var.set(enable_audio_feedback)
        self.streaming_boundary_space_keypress_var.set(
            streaming_boundary_space_keypress
        )
        self.debug_mode_var.set(debug_mode)
