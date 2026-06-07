"""Custom refinement prompt section — CustomTkinter version."""

from tkinter import messagebox
from typing import Callable
import customtkinter as ctk

from src.config.prompts import (
    text_refiner_prompt_w_glossary,
    text_refiner_prompt_wo_glossary,
)

C_WIN     = "#131519"
C_INPUT   = "#0e1013"
C_SURFACE = "#1b1e24"
C_SURFACE2 = "#23272f"
C_BORDER  = "#2a2f38"
C_BORDER_S = "#21252c"
C_TEXT    = "#e7e9ec"
C_TEXT2   = "#9ba2ab"
C_TEXT3   = "#6a717b"
C_ACCENT  = "#f5a524"
C_ACCENT2 = "#ffb454"

FONT_SM   = ("Segoe UI", 13)
FONT_BOLD = ("Segoe UI", 14, "bold")
FONT_HEAD = ("Segoe UI", 11, "bold")
FONT_MONO = ("Consolas", 13)


def _subhead(parent, text: str):
    """Uppercase section label + divider line."""
    row = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0)
    row.pack(fill="x", pady=(0, 10))
    ctk.CTkLabel(
        row, text=text.upper(), font=FONT_HEAD, text_color=C_TEXT3,
        fg_color="transparent",
    ).pack(side="left")
    ctk.CTkFrame(row, fg_color=C_BORDER_S, height=1, corner_radius=0).pack(
        side="left", fill="x", expand=True, padx=10
    )
    return row


class PromptSection:
    """Custom refinement prompt editor with char counter and default helpers."""

    def __init__(
        self,
        parent,
        root,
        initial_prompt: str = "",
        on_change: Callable[[], None] | None = None,
    ):
        self.root = root
        self.on_change = on_change
        self._suspend = False

        self.frame = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0)
        self.frame.pack(fill="both", expand=True)

        self.prompt_text: ctk.CTkTextbox | None = None
        self.char_count_label: ctk.CTkLabel | None = None
        self._defaults_frame: ctk.CTkFrame | None = None
        self._defaults_visible = False
        self._show_defaults_btn: ctk.CTkButton | None = None

        self._create_widgets()
        if initial_prompt:
            self.set_prompt(initial_prompt)

    def _create_widgets(self):
        f = self.frame

        _subhead(f, "Custom Prompt")

        ctk.CTkLabel(
            f,
            text=(
                "Leave empty to use the default prompt. "
                "Use {custom_glossary} to inject your glossary terms."
            ),
            font=("Segoe UI", 12), text_color=C_TEXT3, fg_color="transparent",
            justify="left", anchor="w", wraplength=440,
        ).pack(anchor="w", pady=(0, 10))

        self._defaults_frame = ctk.CTkFrame(f, fg_color="transparent", corner_radius=0)

        # Prompt textarea
        self.prompt_text = ctk.CTkTextbox(
            f,
            height=140,
            font=FONT_MONO,
            fg_color=C_INPUT,
            border_color=C_BORDER,
            text_color=C_TEXT,
            scrollbar_button_color=C_SURFACE2,
            scrollbar_button_hover_color=C_TEXT3,
            corner_radius=7, border_width=1,
            wrap="word",
        )
        self.prompt_text.pack(fill="x", pady=(0, 4))
        self.prompt_text.bind("<<Modified>>", self._on_modified)
        # CTkTextbox fires <<Modified>> differently — also bind KeyRelease
        self.prompt_text.bind("<KeyRelease>", self._on_modified)

        self.char_count_label = ctk.CTkLabel(
            f, text="0 characters",
            font=("Segoe UI", 11), text_color=C_TEXT3, fg_color="transparent",
            anchor="w",
        )
        self.char_count_label.pack(anchor="w", pady=(0, 8))

        # Action buttons
        btn_row = ctk.CTkFrame(f, fg_color="transparent", corner_radius=0)
        btn_row.pack(anchor="w")
        self._btn_row = btn_row

        ghost = dict(fg_color=C_SURFACE, text_color=C_TEXT2, hover_color=C_SURFACE2,
                     border_width=1, border_color=C_BORDER)
        for text, cmd in [
            ("Show defaults",     self._toggle_defaults),
            ("Copy w/ Glossary",  self._copy_with_glossary),
            ("Copy no Glossary",  self._copy_without_glossary),
            ("Clear",             self._clear_prompt),
        ]:
            btn = ctk.CTkButton(
                btn_row, text=text, command=cmd,
                height=27, corner_radius=6, font=FONT_SM, **ghost,
            )
            btn.pack(side="left", padx=(0, 6))
            if text == "Show defaults":
                self._show_defaults_btn = btn

    def _toggle_defaults(self):
        self._defaults_visible = not self._defaults_visible
        if self._defaults_visible:
            if self._show_defaults_btn:
                self._show_defaults_btn.configure(text="Hide defaults")
            self._defaults_frame.pack(fill="x", pady=(0, 8),
                                      before=self.prompt_text)
            self._populate_defaults()
        else:
            if self._show_defaults_btn:
                self._show_defaults_btn.configure(text="Show defaults")
            self._defaults_frame.pack_forget()

    def _populate_defaults(self):
        for w in self._defaults_frame.winfo_children():
            w.destroy()
        for heading, text in [
            ("Default (with glossary):", text_refiner_prompt_w_glossary),
            ("Default (without glossary):", text_refiner_prompt_wo_glossary),
        ]:
            ctk.CTkLabel(
                self._defaults_frame, text=heading,
                font=FONT_BOLD, text_color=C_TEXT2, fg_color="transparent", anchor="w",
            ).pack(anchor="w", pady=(8 if heading.startswith("Default (without") else 0, 3))
            box = ctk.CTkTextbox(
                self._defaults_frame, height=80, font=FONT_MONO,
                fg_color=C_SURFACE, border_color=C_BORDER, text_color=C_TEXT2,
                corner_radius=7, border_width=1, state="normal",
            )
            box.pack(fill="x", pady=(0, 4))
            box.insert("1.0", text)
            box.configure(state="disabled")

    def _on_modified(self, _event=None):
        if self._suspend:
            return
        content = self._get_text()
        if self.char_count_label:
            self.char_count_label.configure(text=f"{len(content)} characters")
        if self.on_change:
            self.on_change()

    def _get_text(self) -> str:
        if not self.prompt_text:
            return ""
        return self.prompt_text.get("1.0", "end-1c").strip()

    def _set_text(self, text: str):
        if not self.prompt_text:
            return
        self.prompt_text.configure(state="normal")
        self.prompt_text.delete("1.0", "end")
        if text:
            self.prompt_text.insert("1.0", text)

    def _copy_with_glossary(self):
        self._suspend = True
        self._set_text(text_refiner_prompt_w_glossary)
        self._suspend = False
        self._on_modified()

    def _copy_without_glossary(self):
        self._suspend = True
        self._set_text(text_refiner_prompt_wo_glossary)
        self._suspend = False
        self._on_modified()

    def _clear_prompt(self):
        if self._get_text():
            if not messagebox.askyesno(
                "Clear Custom Prompt",
                "Clear the custom prompt? The default prompt will be used.",
            ):
                return
        self._suspend = True
        self._set_text("")
        self._suspend = False
        self._on_modified()

    def set_enabled(self, enabled: bool):
        """Gray out the prompt editor and its action buttons when disabled."""
        state = "normal" if enabled else "disabled"
        if self.prompt_text:
            self.prompt_text.configure(state=state)
        for btn in getattr(self, "_btn_row", None).winfo_children() if getattr(self, "_btn_row", None) else []:
            try:
                btn.configure(state=state)
            except Exception:
                pass

    # ── Public interface ───────────────────────────────────────────────────────

    def get_prompt(self) -> str:
        return self._get_text()

    def set_prompt(self, prompt: str):
        self._suspend = True
        self._set_text(prompt)
        self._suspend = False
        if self.char_count_label:
            self.char_count_label.configure(text=f"{len(prompt)} characters")
