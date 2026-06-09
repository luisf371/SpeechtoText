"""API configuration section — CustomTkinter version."""

import tkinter as tk
from typing import Callable
import customtkinter as ctk

from src.transcription_parakeet_streaming import (
    PARAKEET_STREAMING_DEFAULT_BATCH_SIZE,
    PARAKEET_STREAMING_DEFAULT_BATCH_WINDOW_MS,
    PARAKEET_STREAMING_DEFAULT_MAX_CHUNK_SECONDS,
    PARAKEET_STREAMING_DEFAULT_VAD_END_SILENCE_MS,
    PARAKEET_STREAMING_MAX_BATCH_SIZE,
    PARAKEET_STREAMING_MAX_BATCH_WINDOW_MS,
    PARAKEET_STREAMING_MAX_MAX_CHUNK_SECONDS,
    PARAKEET_STREAMING_MAX_VAD_END_SILENCE_MS,
    PARAKEET_STREAMING_MIN_BATCH_SIZE,
    PARAKEET_STREAMING_MIN_BATCH_WINDOW_MS,
    PARAKEET_STREAMING_MIN_MAX_CHUNK_SECONDS,
    PARAKEET_STREAMING_MIN_VAD_END_SILENCE_MS,
)
from src.gui.validators import (
    validate_openai_api_key,
    validate_deepgram_api_key,
    validate_cerebras_api_key,
    validate_gemini_api_key,
)
from src.provider_registry import (
    default_refinement_model,
    default_stt_model,
    refinement_models,
    refinement_provider_names,
    stt_models,
    stt_provider_names,
)

C_WIN = "#131519"
C_INPUT = "#0e1013"
C_BORDER = "#2a2f38"
C_BORDER_S = "#21252c"
C_TEXT = "#e7e9ec"
C_TEXT2 = "#9ba2ab"
C_TEXT3 = "#6a717b"
C_SURFACE = "#1b1e24"
C_SURFACE2 = "#23272f"
C_ACCENT = "#f5a524"
C_ACCENT2 = "#ffb454"
C_ACCENT_DIM = "#1d1a0d"

FONT_SM = ("Segoe UI", 13)
FONT_MONO = ("Consolas", 14)
FONT_HEAD = ("Segoe UI", 11, "bold")

STREAMING_TUNING_FIELDS = [
    {
        "label": "Speech End Silence (ms)",
        "description": "How long Parakeet waits after you stop speaking before it sends that speech for transcription.",
        "default": PARAKEET_STREAMING_DEFAULT_VAD_END_SILENCE_MS,
        "minimum": PARAKEET_STREAMING_MIN_VAD_END_SILENCE_MS,
        "maximum": PARAKEET_STREAMING_MAX_VAD_END_SILENCE_MS,
        "step": 50,
        "decimals": 0,
    },
    {
        "label": "Max Chunk (s)",
        "description": "Longest single speech segment Parakeet will collect before forcing a transcription.",
        "default": PARAKEET_STREAMING_DEFAULT_MAX_CHUNK_SECONDS,
        "minimum": PARAKEET_STREAMING_MIN_MAX_CHUNK_SECONDS,
        "maximum": PARAKEET_STREAMING_MAX_MAX_CHUNK_SECONDS,
        "step": 0.5,
        "decimals": 1,
    },
    {
        "label": "Batch Size",
        "description": "Maximum number of speech segments the server can process together at once.",
        "default": PARAKEET_STREAMING_DEFAULT_BATCH_SIZE,
        "minimum": PARAKEET_STREAMING_MIN_BATCH_SIZE,
        "maximum": PARAKEET_STREAMING_MAX_BATCH_SIZE,
        "step": 1,
        "decimals": 0,
    },
    {
        "label": "Batch Window (ms)",
        "description": "How long the server may briefly wait to group nearby speech segments together.",
        "default": PARAKEET_STREAMING_DEFAULT_BATCH_WINDOW_MS,
        "minimum": PARAKEET_STREAMING_MIN_BATCH_WINDOW_MS,
        "maximum": PARAKEET_STREAMING_MAX_BATCH_WINDOW_MS,
        "step": 5.0,
        "decimals": 1,
    },
]


class _Tooltip:
    def __init__(self, widget, text: str):
        self.widget = widget
        self.text = text
        self._window: tk.Toplevel | None = None
        self._after_id: str | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._cancel()
        self._after_id = self.widget.after(250, self._show)

    def _cancel(self):
        if self._after_id:
            self.widget.after_cancel(self._after_id)
            self._after_id = None

    def _show(self):
        self._after_id = None
        if self._window or not self.widget.winfo_exists():
            return

        x = self.widget.winfo_rootx() + 18
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        self._window = tk.Toplevel(self.widget)
        self._window.wm_overrideredirect(True)
        self._window.wm_geometry(f"+{x}+{y}")
        self._window.configure(bg=C_BORDER)

        label = tk.Label(
            self._window,
            text=self.text,
            justify="left",
            bg=C_SURFACE,
            fg=C_TEXT,
            padx=10,
            pady=8,
            font=("Segoe UI", 10),
            wraplength=280,
            relief="flat",
            borderwidth=0,
        )
        label.pack(padx=1, pady=1)

    def _hide(self, _event=None):
        self._cancel()
        if self._window:
            self._window.destroy()
            self._window = None


class _NumericSpinbox(ctk.CTkFrame):
    def __init__(
        self,
        parent,
        *,
        variable: tk.StringVar,
        minimum: float,
        maximum: float,
        step: float,
        default: float,
        decimals: int,
    ):
        super().__init__(
            parent,
            fg_color=C_INPUT,
            border_color=C_BORDER,
            border_width=1,
            corner_radius=7,
            height=33,
        )
        self.variable = variable
        self.minimum = minimum
        self.maximum = maximum
        self.step = step
        self.default = default
        self.decimals = decimals
        self.pack_propagate(False)

        spinbox_format = f"%.{decimals}f" if decimals > 0 else None
        spinbox_options = {
            "from_": minimum,
            "to": maximum,
            "increment": step,
            "textvariable": variable,
            "command": self.clamp_current,
            "font": FONT_MONO,
            "bg": C_INPUT,
            "fg": C_TEXT,
            "insertbackground": C_TEXT,
            "buttonbackground": C_SURFACE2,
            "selectbackground": C_ACCENT_DIM,
            "selectforeground": C_TEXT,
            "relief": "flat",
            "borderwidth": 0,
            "highlightthickness": 0,
            "justify": "left",
            "wrap": False,
        }
        if spinbox_format:
            spinbox_options["format"] = spinbox_format

        self.entry = tk.Spinbox(
            self,
            **spinbox_options,
        )
        self.entry.pack(fill="both", expand=True, padx=8, pady=4)
        self.entry.bind("<FocusOut>", lambda _event: self.clamp_current(), add="+")
        self.entry.bind(
            "<Return>",
            lambda _event: (self.clamp_current(), "break")[1],
            add="+",
        )

    def _parse(self) -> float:
        try:
            return float(self.variable.get())
        except ValueError:
            return float(self.default)

    def _format(self, value: float) -> str:
        if self.decimals == 0:
            return str(int(round(value)))
        return f"{value:.{self.decimals}f}"

    def clamp_current(self):
        value = min(max(self._parse(), self.minimum), self.maximum)
        self.variable.set(self._format(value))


def _subhead(parent, text: str, pill: str | None = None):
    """Uppercase section label + divider line, with an optional amber pill."""
    row = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0)
    row.pack(fill="x", pady=(16, 10))
    ctk.CTkLabel(
        row,
        text=text.upper(),
        font=FONT_HEAD,
        text_color=C_TEXT3,
        fg_color="transparent",
    ).pack(side="left")
    if pill:
        ctk.CTkLabel(
            row,
            text=f"  {pill}  ",
            font=("Segoe UI", 11),
            text_color=C_ACCENT2,
            fg_color=C_ACCENT_DIM,
            corner_radius=999,
        ).pack(side="right")
    ctk.CTkFrame(row, fg_color=C_BORDER_S, height=1, corner_radius=0).pack(
        side="left", fill="x", expand=True, padx=10
    )
    return row


def _field(
    parent, label: str, variable: tk.StringVar, mono=False, show="", placeholder=""
) -> ctk.CTkEntry:
    row = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0)
    row.pack(fill="x", pady=(0, 10))
    ctk.CTkLabel(
        row,
        text=label,
        font=FONT_SM,
        text_color=C_TEXT2,
        fg_color="transparent",
        anchor="w",
    ).pack(anchor="w", pady=(0, 4))
    font = ("Consolas", 10) if mono else FONT_SM
    entry = ctk.CTkEntry(
        row,
        textvariable=variable,
        font=font,
        fg_color=C_INPUT,
        border_color=C_BORDER,
        text_color=C_TEXT,
        placeholder_text=placeholder,
        show=show,
        height=33,
        corner_radius=7,
    )
    entry.pack(fill="x")
    return entry


def _hint(parent, text: str):
    ctk.CTkLabel(
        parent,
        text=text,
        font=("Segoe UI", 12),
        text_color=C_TEXT3,
        fg_color="transparent",
        anchor="w",
    ).pack(anchor="w", pady=(2, 0))


def _combo(
    parent,
    label: str,
    variable: tk.StringVar,
    values: list[str],
    command=None,
    state="readonly",
) -> ctk.CTkOptionMenu:
    row = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0)
    row.pack(fill="x", pady=(0, 10))
    ctk.CTkLabel(
        row,
        text=label,
        font=FONT_SM,
        text_color=C_TEXT2,
        fg_color="transparent",
        anchor="w",
    ).pack(anchor="w", pady=(0, 4))

    # Bordered shell gives the OptionMenu the same 1px inset border as the
    # text inputs; the menu fills it so the whole control reads as one box.
    shell = ctk.CTkFrame(
        row,
        fg_color=C_INPUT,
        border_color=C_BORDER,
        border_width=1,
        corner_radius=7,
        height=33,
    )
    shell.pack(fill="x")
    shell.pack_propagate(False)

    combo = ctk.CTkOptionMenu(
        shell,
        variable=variable,
        values=values,
        command=command,
        font=FONT_SM,
        corner_radius=6,
        anchor="w",
        dynamic_resizing=False,
        fg_color=C_INPUT,
        button_color=C_INPUT,
        button_hover_color=C_SURFACE,
        text_color=C_TEXT,
        dropdown_fg_color=C_SURFACE,
        dropdown_text_color=C_TEXT,
        dropdown_hover_color=C_SURFACE2,
        dropdown_font=FONT_SM,
    )
    if state == "disabled":
        combo.configure(state="disabled")
    combo.pack(fill="both", expand=True, padx=1, pady=1)
    return combo


class APISection:
    """API configuration — split across STT, Refinement, and API-Keys tabs."""

    def __init__(
        self,
        stt_parent,
        refinement_parent,
        keys_parent,
        on_change: Callable[[], None] | None = None,
    ):
        self.on_change = on_change

        self.stt_frame = ctk.CTkFrame(
            stt_parent, fg_color="transparent", corner_radius=0
        )
        self.stt_frame.pack(fill="x")

        self.refinement_frame = ctk.CTkFrame(
            refinement_parent, fg_color="transparent", corner_radius=0
        )
        self.refinement_frame.pack(fill="x")

        self.api_keys_frame = ctk.CTkFrame(
            keys_parent, fg_color="transparent", corner_radius=0
        )
        self.api_keys_frame.pack(fill="x")

        # Variables
        self.stt_provider_var = tk.StringVar()
        self.openai_api_key_var = tk.StringVar()
        self.deepgram_api_key_var = tk.StringVar()
        self.cerebras_api_key_var = tk.StringVar()
        self.gemini_api_key_var = tk.StringVar()
        self.custom_api_key_var = tk.StringVar()
        self.stt_model_var = tk.StringVar()
        self.refinement_provider_var = tk.StringVar()
        self.refinement_model_var = tk.StringVar()
        self.custom_endpoint_var = tk.StringVar()
        self.custom_stt_endpoint_var = tk.StringVar()
        self.custom_refinement_endpoint_var = tk.StringVar()
        self.parakeet_endpoint_var = tk.StringVar()
        self.parakeet_streaming_enabled_var = tk.BooleanVar()
        self.parakeet_streaming_vad_end_silence_ms_var = tk.StringVar()
        self.parakeet_streaming_max_chunk_seconds_var = tk.StringVar()
        self.parakeet_streaming_batch_size_var = tk.StringVar()
        self.parakeet_streaming_batch_window_ms_var = tk.StringVar()

        self.stt_model_combo: ctk.CTkOptionMenu | None = None
        self.refinement_model_combo: ctk.CTkOptionMenu | None = None
        self._streaming_tuning_spinboxes: list[_NumericSpinbox] = []
        self._parakeet_section: ctk.CTkFrame | None = None
        self._custom_stt_section: ctk.CTkFrame | None = None
        self._custom_ref_section: ctk.CTkFrame | None = None

        # Per-provider remembered models
        self.openai_stt_model = default_stt_model("openai")
        self.deepgram_stt_model = default_stt_model("deepgram")
        self.parakeet_stt_model = default_stt_model("parakeet")
        self.custom_stt_model = default_stt_model("custom")

        self.openai_refinement_model = default_refinement_model("openai")
        self.cerebras_refinement_model = default_refinement_model("cerebras")
        self.gemini_refinement_model = default_refinement_model("gemini")
        self.custom_refinement_model = default_refinement_model("custom")

        self._create_stt_widgets()
        self._create_refinement_widgets()
        self._create_keys_widgets()

    # ── Widget builders ────────────────────────────────────────────────────────

    def _create_stt_widgets(self):
        f = self.stt_frame

        self.stt_provider_combo = _combo(
            f,
            "STT Provider",
            self.stt_provider_var,
            values=stt_provider_names(),
            command=lambda _v: self._on_provider_changed(),
        )
        self.stt_model_combo = _combo(
            f,
            "STT Model",
            self.stt_model_var,
            values=stt_models("openai"),
            command=lambda _v: self._on_stt_model_changed(),
            state="normal",
        )

        # ── Parakeet section ──────────────────────────────────────────────
        self._parakeet_section = ctk.CTkFrame(
            f, fg_color="transparent", corner_radius=0
        )
        self._parakeet_section.pack(fill="x")

        _subhead(self._parakeet_section, "Parakeet", pill="● Provider: parakeet")

        _field(
            self._parakeet_section,
            "Endpoint",
            self.parakeet_endpoint_var,
            mono=True,
            placeholder="http://192.168.1.234:8678",
        )
        _hint(self._parakeet_section, "Base URL, e.g. http://192.168.1.234:8678")

        # "Use WebSocket streaming" — trow card style
        stream_card = ctk.CTkFrame(
            self._parakeet_section,
            fg_color=C_SURFACE,
            corner_radius=9,
            border_width=1,
            border_color=C_BORDER_S,
        )
        stream_card.pack(fill="x", pady=(8, 4))
        sc_inner = ctk.CTkFrame(stream_card, fg_color="transparent", corner_radius=0)
        sc_inner.pack(fill="x", padx=(13, 12), pady=9)
        sc_header = ctk.CTkFrame(sc_inner, fg_color="transparent", corner_radius=0)
        sc_header.pack(fill="x")
        ctk.CTkLabel(
            sc_header,
            text="Use WebSocket streaming",
            font=("Segoe UI", 13, "bold"),
            text_color=C_TEXT,
            fg_color="transparent",
            anchor="w",
        ).pack(side="left")
        ctk.CTkSwitch(
            sc_header,
            variable=self.parakeet_streaming_enabled_var,
            text="",
            onvalue=True,
            offvalue=False,
            command=lambda: self.on_change() if self.on_change else None,
            progress_color=C_ACCENT,
            fg_color=C_SURFACE2,
            button_color=C_TEXT,
            button_hover_color=C_TEXT,
            switch_width=38,
            switch_height=20,
        ).pack(side="right")
        ctk.CTkLabel(
            sc_inner,
            text="Stream audio live for lower latency",
            font=("Segoe UI", 11),
            text_color=C_TEXT3,
            fg_color="transparent",
            anchor="w",
        ).pack(anchor="w", pady=(2, 0))

        # Streaming tuning
        _subhead(self._parakeet_section, "Streaming tuning")

        grid = ctk.CTkFrame(
            self._parakeet_section, fg_color="transparent", corner_radius=0
        )
        grid.pack(fill="x")
        grid.columnconfigure((0, 1), weight=1)

        tuning = [
            (
                self.parakeet_streaming_vad_end_silence_ms_var,
                STREAMING_TUNING_FIELDS[0],
            ),
            (
                self.parakeet_streaming_max_chunk_seconds_var,
                STREAMING_TUNING_FIELDS[1],
            ),
            (self.parakeet_streaming_batch_size_var, STREAMING_TUNING_FIELDS[2]),
            (
                self.parakeet_streaming_batch_window_ms_var,
                STREAMING_TUNING_FIELDS[3],
            ),
        ]
        for index, (var, meta) in enumerate(tuning):
            row = index // 2
            col = index % 2
            cell = ctk.CTkFrame(grid, fg_color="transparent", corner_radius=0)
            cell.grid(
                row=row,
                column=col,
                sticky="ew",
                padx=(0, 10 if col == 0 else 0),
                pady=(0, 10),
            )
            label_row = ctk.CTkFrame(cell, fg_color="transparent", corner_radius=0)
            label_row.pack(fill="x", pady=(0, 3))
            ctk.CTkLabel(
                label_row,
                text=meta["label"],
                font=("Segoe UI", 11),
                text_color=C_TEXT3,
                fg_color="transparent",
                anchor="w",
            ).pack(side="left", anchor="w")
            help_button = ctk.CTkButton(
                label_row,
                text="?",
                width=18,
                height=18,
                font=("Segoe UI", 10, "bold"),
                fg_color=C_SURFACE2,
                hover_color=C_ACCENT_DIM,
                text_color=C_TEXT2,
                corner_radius=999,
            )
            help_button.pack(side="left", padx=(6, 0))
            _Tooltip(
                help_button,
                "\n".join(
                    [
                        meta["description"],
                        f"Minimum: {meta['minimum']}",
                        f"Maximum: {meta['maximum']}",
                        f"Default: {meta['default']}",
                    ]
                ),
            )
            spinbox = _NumericSpinbox(
                cell,
                variable=var,
                minimum=meta["minimum"],
                maximum=meta["maximum"],
                step=meta["step"],
                default=meta["default"],
                decimals=meta["decimals"],
            )
            spinbox.pack(fill="x")
            self._streaming_tuning_spinboxes.append(spinbox)

        # ── Custom STT section ────────────────────────────────────────────
        self._custom_stt_section = ctk.CTkFrame(
            f, fg_color="transparent", corner_radius=0
        )
        self._custom_stt_section.pack(fill="x")
        _field(
            self._custom_stt_section,
            "Custom STT Endpoint",
            self.custom_stt_endpoint_var,
            mono=True,
        )
        _hint(self._custom_stt_section, "Required when STT provider is custom")

        self._update_custom_endpoint_visibility()

    def _create_refinement_widgets(self):
        f = self.refinement_frame

        self.refinement_provider_combo = _combo(
            f,
            "Refinement Provider",
            self.refinement_provider_var,
            values=refinement_provider_names(),
            command=lambda _v: self._on_refinement_provider_changed(),
        )
        self.refinement_model_combo = _combo(
            f,
            "Refinement Model",
            self.refinement_model_var,
            values=refinement_models("openai"),
            command=lambda _v: self._on_refinement_model_changed(),
            state="normal",
        )

        self._custom_ref_section = ctk.CTkFrame(
            f, fg_color="transparent", corner_radius=0
        )
        self._custom_ref_section.pack(fill="x")
        _field(
            self._custom_ref_section,
            "Custom Refinement Endpoint",
            self.custom_refinement_endpoint_var,
            mono=True,
        )
        _hint(self._custom_ref_section, "Required when refinement provider is custom")

    def _create_keys_widgets(self):
        f = self.api_keys_frame
        self._key_badges: dict[str, tuple[tk.StringVar, ctk.CTkLabel]] = {}
        rows = [
            ("OpenAI", self.openai_api_key_var, "sk-…"),
            ("Deepgram", self.deepgram_api_key_var, "Not set"),
            ("Cerebras", self.cerebras_api_key_var, "Not set"),
            ("Gemini", self.gemini_api_key_var, "Not set"),
            ("Custom", self.custom_api_key_var, "Not set"),
        ]
        for label, var, placeholder in rows:
            row = ctk.CTkFrame(f, fg_color="transparent", corner_radius=0)
            row.pack(fill="x", pady=(0, 8))

            label_row = ctk.CTkFrame(row, fg_color="transparent", corner_radius=0)
            label_row.pack(fill="x", pady=(0, 4))
            ctk.CTkLabel(
                label_row,
                text=label,
                font=FONT_SM,
                text_color=C_TEXT2,
                fg_color="transparent",
                anchor="w",
            ).pack(side="left")
            badge = ctk.CTkLabel(
                label_row,
                text=" SET ",
                font=("Segoe UI", 10, "bold"),
                text_color=C_ACCENT2,
                fg_color=C_ACCENT_DIM,
                corner_radius=999,
            )
            self._key_badges[str(var)] = (var, badge)
            var.trace_add("write", lambda *_a, v=var: self._refresh_key_badge(v))

            entry_row = ctk.CTkFrame(row, fg_color="transparent", corner_radius=0)
            entry_row.pack(fill="x")
            entry = ctk.CTkEntry(
                entry_row,
                textvariable=var,
                show="*",
                font=("Consolas", 10),
                placeholder_text=placeholder,
                fg_color=C_INPUT,
                border_color=C_BORDER,
                text_color=C_TEXT,
                height=33,
                corner_radius=7,
            )
            entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

            def _make_toggle(e=entry):
                def toggle():
                    e.configure(show="" if e.cget("show") == "*" else "*")

                return toggle

            ctk.CTkButton(
                entry_row,
                text="Show",
                command=_make_toggle(),
                width=56,
                height=33,
                corner_radius=7,
                font=FONT_SM,
                fg_color=C_SURFACE,
                text_color=C_TEXT2,
                hover_color=C_SURFACE2,
                border_width=1,
                border_color=C_BORDER,
            ).pack(side="left")

    @property
    def custom_stt_endpoint_frame(self):
        return self._custom_stt_section

    # ── API-key "SET" badges ──────────────────────────────────────────────────

    def _refresh_key_badge(self, var: tk.StringVar):
        entry = getattr(self, "_key_badges", {}).get(str(var))
        if not entry:
            return
        _var, badge = entry
        if var.get().strip():
            badge.pack(side="left", padx=(7, 0))
        else:
            badge.pack_forget()

    def _refresh_all_key_badges(self):
        for var, _badge in getattr(self, "_key_badges", {}).values():
            self._refresh_key_badge(var)

    # ── Refinement enable/disable (driven by master toggle) ───────────────────

    def set_refinement_enabled(self, enabled: bool):
        """Gray out / re-enable the refinement provider, model and endpoint."""
        state = "normal" if enabled else "disabled"
        if self.refinement_provider_combo:
            self.refinement_provider_combo.configure(state=state)
        if self.refinement_model_combo:
            self.refinement_model_combo.configure(state=state)
        for child in (
            self._custom_ref_section.winfo_children()
            if self._custom_ref_section
            else []
        ):
            if isinstance(child, ctk.CTkEntry):
                child.configure(state="normal" if enabled else "disabled")

    # ── Visibility helpers ────────────────────────────────────────────────────

    def _update_custom_endpoint_visibility(self):
        if self._parakeet_section:
            if self.stt_provider_var.get() == "parakeet":
                self._parakeet_section.pack(fill="x")
            else:
                self._parakeet_section.pack_forget()

        if self._custom_stt_section:
            if self.stt_provider_var.get() == "custom":
                self._custom_stt_section.pack(fill="x")
            else:
                self._custom_stt_section.pack_forget()

        if self._custom_ref_section:
            if self.refinement_provider_var.get() == "custom":
                self._custom_ref_section.pack(fill="x")
            else:
                self._custom_ref_section.pack_forget()

    # ── Event handlers ────────────────────────────────────────────────────────

    def _on_provider_changed(self):
        self._update_stt_model_options()
        self._update_custom_endpoint_visibility()
        if self.on_change:
            self.on_change()

    def _on_stt_model_changed(self):
        p = self.stt_provider_var.get()
        m = self.stt_model_var.get()
        if p == "openai":
            self.openai_stt_model = m
        elif p == "deepgram":
            self.deepgram_stt_model = m
        elif p == "parakeet":
            self.parakeet_stt_model = m
        elif p == "custom":
            self.custom_stt_model = m

    def _on_refinement_provider_changed(self):
        self._update_refinement_model_options()
        self._update_custom_endpoint_visibility()
        if self.on_change:
            self.on_change()

    def _on_refinement_model_changed(self):
        p = self.refinement_provider_var.get()
        m = self.refinement_model_var.get()
        if p == "openai":
            self.openai_refinement_model = m
        elif p == "cerebras":
            self.cerebras_refinement_model = m
        elif p == "gemini":
            self.gemini_refinement_model = m
        elif p == "custom":
            self.custom_refinement_model = m

    def _update_stt_model_options(self):
        if not self.stt_model_combo:
            return
        p = self.stt_provider_var.get()
        cur = self.stt_model_var.get()

        if cur in stt_models("openai"):
            self.openai_stt_model = cur
        elif cur in stt_models("deepgram"):
            self.deepgram_stt_model = cur
        elif cur in stt_models("parakeet"):
            self.parakeet_stt_model = cur
        elif cur in stt_models("custom"):
            self.custom_stt_model = cur

        models_map = {
            "openai": (stt_models("openai"), self.openai_stt_model),
            "deepgram": (stt_models("deepgram"), self.deepgram_stt_model),
            "parakeet": (stt_models("parakeet"), self.parakeet_stt_model),
            "custom": (stt_models("custom"), self.custom_stt_model),
        }
        models, remembered = models_map.get(p, ([], ""))
        self.stt_model_combo.configure(values=models)
        self.stt_model_var.set(
            remembered if remembered in models else (models[0] if models else "")
        )

    def _update_refinement_model_options(self):
        if not self.refinement_model_combo:
            return
        p = self.refinement_provider_var.get()
        cur = self.refinement_model_var.get()

        if cur in refinement_models("openai"):
            self.openai_refinement_model = cur
        elif cur in refinement_models("cerebras"):
            self.cerebras_refinement_model = cur
        elif cur in refinement_models("gemini"):
            self.gemini_refinement_model = cur
        elif cur in refinement_models("custom"):
            self.custom_refinement_model = cur

        models_map = {
            "openai": (refinement_models("openai"), self.openai_refinement_model),
            "cerebras": (
                refinement_models("cerebras"),
                self.cerebras_refinement_model,
            ),
            "gemini": (refinement_models("gemini"), self.gemini_refinement_model),
            "custom": (refinement_models("custom"), self.custom_refinement_model),
        }
        models, remembered = models_map.get(p, ([], ""))
        self.refinement_model_combo.configure(values=models)
        self.refinement_model_var.set(
            remembered if remembered in models else (models[0] if models else "")
        )

    # ── Combobox options only (used by set_values) ────────────────────────────

    def _update_combobox_options_only(self):
        if self.stt_model_combo:
            p = self.stt_provider_var.get()
            self.stt_model_combo.configure(values=stt_models(p))

        if self.refinement_model_combo:
            p = self.refinement_provider_var.get()
            self.refinement_model_combo.configure(values=refinement_models(p))

    # ── Public interface ───────────────────────────────────────────────────────

    def get_values(self) -> dict:
        for spinbox in self._streaming_tuning_spinboxes:
            spinbox.clamp_current()

        return {
            "stt_provider": self.stt_provider_var.get(),
            "openai_api_key": self.openai_api_key_var.get().strip(),
            "deepgram_api_key": self.deepgram_api_key_var.get().strip(),
            "cerebras_api_key": self.cerebras_api_key_var.get().strip(),
            "gemini_api_key": self.gemini_api_key_var.get().strip(),
            "custom_api_key": self.custom_api_key_var.get().strip(),
            "stt_model": self.stt_model_var.get(),
            "refinement_provider": self.refinement_provider_var.get(),
            "refinement_model": self.refinement_model_var.get(),
            "custom_endpoint": self.custom_endpoint_var.get().strip(),
            "custom_stt_endpoint": self.custom_stt_endpoint_var.get().strip(),
            "custom_refinement_endpoint": self.custom_refinement_endpoint_var.get().strip(),
            "parakeet_endpoint": self.parakeet_endpoint_var.get().strip(),
            "parakeet_streaming_enabled": self.parakeet_streaming_enabled_var.get(),
            "parakeet_streaming_vad_end_silence_ms": self.parakeet_streaming_vad_end_silence_ms_var.get().strip(),
            "parakeet_streaming_max_chunk_seconds": self.parakeet_streaming_max_chunk_seconds_var.get().strip(),
            "parakeet_streaming_batch_size": self.parakeet_streaming_batch_size_var.get().strip(),
            "parakeet_streaming_batch_window_ms": self.parakeet_streaming_batch_window_ms_var.get().strip(),
        }

    def set_values(
        self,
        stt_provider,
        openai_api_key,
        deepgram_api_key,
        cerebras_api_key,
        gemini_api_key,
        custom_api_key,
        stt_model,
        refinement_provider,
        refinement_model,
        custom_endpoint="",
        custom_stt_endpoint="",
        custom_refinement_endpoint="",
        parakeet_endpoint="http://localhost:8000",
        parakeet_streaming_enabled=False,
        parakeet_streaming_vad_end_silence_ms=(
            PARAKEET_STREAMING_DEFAULT_VAD_END_SILENCE_MS
        ),
        parakeet_streaming_max_chunk_seconds=(
            PARAKEET_STREAMING_DEFAULT_MAX_CHUNK_SECONDS
        ),
        parakeet_streaming_batch_size=PARAKEET_STREAMING_DEFAULT_BATCH_SIZE,
        parakeet_streaming_batch_window_ms=(
            PARAKEET_STREAMING_DEFAULT_BATCH_WINDOW_MS
        ),
    ):
        self.openai_api_key_var.set(openai_api_key)
        self.deepgram_api_key_var.set(deepgram_api_key)
        self.cerebras_api_key_var.set(cerebras_api_key)
        self.gemini_api_key_var.set(gemini_api_key)
        self.custom_api_key_var.set(custom_api_key)
        self.custom_endpoint_var.set(custom_endpoint)
        self.custom_stt_endpoint_var.set(custom_stt_endpoint or custom_endpoint)
        self.custom_refinement_endpoint_var.set(
            custom_refinement_endpoint or custom_endpoint
        )
        self.parakeet_endpoint_var.set(parakeet_endpoint or "http://localhost:8000")
        self.parakeet_streaming_enabled_var.set(parakeet_streaming_enabled)
        self.parakeet_streaming_vad_end_silence_ms_var.set(
            str(parakeet_streaming_vad_end_silence_ms)
        )
        self.parakeet_streaming_max_chunk_seconds_var.set(
            str(parakeet_streaming_max_chunk_seconds)
        )
        self.parakeet_streaming_batch_size_var.set(str(parakeet_streaming_batch_size))
        self.parakeet_streaming_batch_window_ms_var.set(
            str(parakeet_streaming_batch_window_ms)
        )

        if stt_provider == "openai":
            self.openai_stt_model = stt_model
        elif stt_provider == "deepgram":
            self.deepgram_stt_model = stt_model
        elif stt_provider == "parakeet":
            self.parakeet_stt_model = stt_model
        elif stt_provider == "custom":
            self.custom_stt_model = stt_model

        if refinement_provider == "openai":
            self.openai_refinement_model = refinement_model
        elif refinement_provider == "cerebras":
            self.cerebras_refinement_model = refinement_model
        elif refinement_provider == "gemini":
            self.gemini_refinement_model = refinement_model
        elif refinement_provider == "custom":
            self.custom_refinement_model = refinement_model

        self.stt_provider_var.set(stt_provider)
        self.refinement_provider_var.set(refinement_provider)
        self._update_custom_endpoint_visibility()
        self._update_combobox_options_only()
        self.stt_model_var.set(stt_model)
        self.refinement_model_var.set(refinement_model)
        self._refresh_all_key_badges()

    def test_api_keys(self) -> str:
        v = self.get_values()
        lines = ["API Key Validation Status:\n"]

        def _check(key, validator, name, selected_ctx):
            marker, status = "[ ]", "Not configured"
            if key:
                try:
                    validator(key)
                    marker, status = "[OK]", "VALID"
                except Exception as e:
                    marker, status = "[X]", str(e)
            sel = f" ({selected_ctx})" if selected_ctx else ""
            lines.append(f"\n{marker} {name}{sel}:")
            lines.append(f"  Status: {status}")

        _check(
            v["openai_api_key"],
            validate_openai_api_key,
            "OpenAI",
            "Selected STT" if v["stt_provider"] == "openai" else "",
        )
        _check(
            v["deepgram_api_key"],
            validate_deepgram_api_key,
            "Deepgram",
            "Selected STT" if v["stt_provider"] == "deepgram" else "",
        )
        _check(
            v["cerebras_api_key"],
            validate_cerebras_api_key,
            "Cerebras",
            "Selected Refinement" if v["refinement_provider"] == "cerebras" else "",
        )
        _check(
            v["gemini_api_key"],
            validate_gemini_api_key,
            "Gemini",
            "Selected Refinement" if v["refinement_provider"] == "gemini" else "",
        )

        if (
            v["custom_api_key"]
            or v["custom_stt_endpoint"]
            or v["custom_refinement_endpoint"]
        ):
            lines.append("\n[ ] Custom:\n  Status: Configured (not validated)")
        else:
            lines.append("\n[ ] Custom:\n  Status: Not configured")

        lines += [
            "\n" + "-" * 40,
            f"\nSTT Provider: {v['stt_provider']}",
            f"STT Model: {v['stt_model']}",
            f"Refinement Provider: {v['refinement_provider']}",
            f"Refinement Model: {v['refinement_model']}",
        ]
        return "\n".join(lines)
