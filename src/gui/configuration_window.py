"""Main configuration window — CustomTkinter dark tabbed redesign."""

import os
import threading
import tkinter as tk
from tkinter import messagebox
from typing import Callable
from loguru import logger
import customtkinter as ctk

from src.push_to_talk import PushToTalkConfig
from src.config.constants import CONFIG_CHANGE_DEBOUNCE_DELAY_MS
from src.gui.api_section import APISection
from src.gui.hotkey_section import HotkeySection
from src.gui.settings_section import FeatureFlagsSection
from src.gui.glossary_section import GlossarySection
from src.gui.prompt_section import PromptSection
from src.gui.validators import validate_configuration
from src.gui.config_persistence import ConfigurationPersistence

ctk.set_appearance_mode("dark")

# ── Design tokens ──────────────────────────────────────────────────────────────
C_WIN        = "#131519"
C_CHROME     = "#181b20"
C_SURFACE    = "#1b1e24"
C_SURFACE2   = "#23272f"
C_INPUT      = "#0e1013"
C_BORDER     = "#2a2f38"
C_BORDER_S   = "#21252c"
C_TEXT       = "#e7e9ec"
C_TEXT2      = "#9ba2ab"
C_TEXT3      = "#6a717b"
C_ACCENT     = "#f5a524"
C_ACCENT2    = "#ffb454"
C_ACCENT_DIM = "#1d1a0d"
C_DANGER     = "#e5616a"

APP_VERSION = "v0.6.0"

_TABS = [
    ("stt",        "Speech-to-Text", "🎙"),
    ("refinement", "Refinement",     "✨"),
    ("glossary",   "Glossary",       "📖"),
    ("hotkeys",    "Hotkeys",        "⌨"),
    ("apikeys",    "API Keys",       "🔑"),
    ("advanced",   "Advanced",       "⚙"),
]

FONT_UI    = ("Segoe UI", 14)
FONT_BOLD  = ("Segoe UI", 14, "bold")
FONT_SM    = ("Segoe UI", 13)
FONT_MONO  = ("Consolas", 14)


# ══════════════════════════════════════════════════════════════════════════════
class ConfigurationWindow:
    """720×520 dark tabbed settings window built with CustomTkinter."""

    def __init__(
        self,
        config: PushToTalkConfig,
        on_config_changed: Callable[[PushToTalkConfig], None] | None = None,
        config_file_path: str = "push_to_talk_config.json",
    ):
        self.config = config
        self.on_config_changed = on_config_changed
        self.config_file_path = config_file_path
        self.root: ctk.CTk | None = None
        self.result: str | None = None

        self.app_instance = None
        self.app_thread = None
        self.is_running = False

        self.api_section: APISection | None = None
        self.hotkey_section: HotkeySection | None = None
        self.feature_flags_section: FeatureFlagsSection | None = None
        self.glossary_section: GlossarySection | None = None
        self.prompt_section: PromptSection | None = None

        self._variable_traces: list[tuple[tk.Variable, str]] = []
        self._suspend_change_events = False
        self._pending_update_job: str | None = None
        self._initialization_complete = False

        self._config_persistence = ConfigurationPersistence()

        self._active_tab: str = "stt"
        self._tab_frames: dict[str, ctk.CTkScrollableFrame] = {}
        self._nav_btns: dict[str, ctk.CTkButton] = {}
        self._nav_bars: dict[str, ctk.CTkFrame] = {}

        self._status_dot: tk.Canvas | None = None
        self._status_label: ctk.CTkLabel | None = None
        self._start_btn: ctk.CTkButton | None = None

        # Tab content anchor frames (filled during _build_tabs)
        self._stt_tab_content: ctk.CTkScrollableFrame | None = None
        self._ref_toggle_content: ctk.CTkFrame | None = None
        self._ref_provider_content: ctk.CTkFrame | None = None
        self._ref_prompt_content: ctk.CTkFrame | None = None
        self._glossary_content: ctk.CTkScrollableFrame | None = None
        self._hotkeys_content: ctk.CTkScrollableFrame | None = None
        self._apikeys_content: ctk.CTkScrollableFrame | None = None
        self._advanced_content: ctk.CTkScrollableFrame | None = None

    # ── Window shell ───────────────────────────────────────────────────────────
    def create_gui(self) -> ctk.CTk:
        self.root = ctk.CTk()
        self.root.title("PushToTalk — Configuration")
        self.root.geometry("720x520")
        self.root.resizable(False, False)
        self.root.configure(fg_color=C_WIN)

        try:
            if os.path.exists("icon.ico"):
                self.root.iconbitmap("icon.ico")
        except Exception:
            pass

        self._build_layout()
        self._update_sections_from_config(self.config)
        self._setup_variable_traces()
        self._initialization_complete = True

        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() // 2) - 360
        y = (self.root.winfo_screenheight() // 2) - 260
        self.root.geometry(f"+{x}+{y}")
        return self.root

    # ── Layout ─────────────────────────────────────────────────────────────────
    def _build_layout(self):
        # Body: sidebar | divider | content
        body = ctk.CTkFrame(self.root, fg_color=C_WIN, corner_radius=0)
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(2, weight=1)
        body.grid_rowconfigure(0, weight=1)

        sidebar = ctk.CTkFrame(body, fg_color=C_CHROME, width=178, corner_radius=0)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)

        divider = ctk.CTkFrame(body, fg_color=C_BORDER_S, width=1, corner_radius=0)
        divider.grid(row=0, column=1, sticky="nsew")

        content_host = ctk.CTkFrame(body, fg_color=C_WIN, corner_radius=0)
        content_host.grid(row=0, column=2, sticky="nsew")

        self._build_sidebar(sidebar)
        self._build_tabs(content_host)
        self._switch_tab("stt")

        # Footer separator + footer
        ctk.CTkFrame(self.root, fg_color=C_BORDER_S, height=1, corner_radius=0).pack(
            fill="x"
        )
        footer = ctk.CTkFrame(self.root, fg_color=C_CHROME, height=56, corner_radius=0)
        footer.pack(fill="x")
        footer.pack_propagate(False)
        self._build_footer(footer)

    # ── Sidebar ────────────────────────────────────────────────────────────────
    def _build_sidebar(self, parent: ctk.CTkFrame):
        nav = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0)
        nav.pack(fill="both", expand=True, padx=9, pady=10)

        for tab_id, label, icon in _TABS:
            self._make_nav_item(nav, tab_id, label, icon)

        ctk.CTkLabel(
            parent,
            text=APP_VERSION,
            font=("Consolas", 11),
            text_color=C_TEXT3,
            fg_color="transparent",
        ).pack(side="bottom", anchor="w", padx=14, pady=8)

    def _make_nav_item(self, parent: ctk.CTkFrame, tab_id: str, label: str, icon: str):
        row = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0, height=35)
        row.pack(fill="x", pady=1)
        row.pack_propagate(False)

        bar = ctk.CTkFrame(row, fg_color="transparent", width=3, corner_radius=0)
        bar.pack(side="left", fill="y")
        bar.pack_propagate(False)
        self._nav_bars[tab_id] = bar

        btn = ctk.CTkButton(
            row,
            text=f"{icon}  {label}",
            anchor="w",
            fg_color="transparent",
            text_color=C_TEXT2,
            hover_color=C_SURFACE,
            corner_radius=7,
            height=35,
            font=FONT_BOLD,
            command=lambda tid=tab_id: self._switch_tab(tid),
        )
        btn.pack(side="left", fill="both", expand=True)
        self._nav_btns[tab_id] = btn

    def _switch_tab(self, tab_id: str):
        if self._active_tab in self._nav_btns:
            self._nav_btns[self._active_tab].configure(
                fg_color="transparent", text_color=C_TEXT2
            )
            self._nav_bars[self._active_tab].configure(fg_color="transparent")

        self._active_tab = tab_id
        self._nav_btns[tab_id].configure(fg_color=C_ACCENT_DIM, text_color=C_ACCENT2)
        self._nav_bars[tab_id].configure(fg_color=C_ACCENT)

        for tid, frame in self._tab_frames.items():
            if tid == tab_id:
                frame.pack(fill="both", expand=True)
            else:
                frame.pack_forget()

    # ── Tab frames ─────────────────────────────────────────────────────────────
    def _build_tabs(self, parent: ctk.CTkFrame):
        for tab_id, _label, _icon in _TABS:
            sf = ctk.CTkScrollableFrame(
                parent,
                fg_color=C_WIN,
                corner_radius=0,
                scrollbar_button_color=C_SURFACE2,
                scrollbar_button_hover_color=C_TEXT3,
            )
            self._tab_frames[tab_id] = sf

        self._build_tab_stt(self._tab_frames["stt"])
        self._build_tab_refinement(self._tab_frames["refinement"])
        self._build_tab_glossary(self._tab_frames["glossary"])
        self._build_tab_hotkeys(self._tab_frames["hotkeys"])
        self._build_tab_apikeys(self._tab_frames["apikeys"])
        self._build_tab_advanced(self._tab_frames["advanced"])

    def _padded(self, parent: ctk.CTkScrollableFrame) -> ctk.CTkFrame:
        f = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0)
        f.pack(fill="both", expand=True, padx=20, pady=10)
        return f

    def _build_tab_stt(self, host: ctk.CTkScrollableFrame):
        pad = self._padded(host)
        self._stt_tab_content = pad

    def _build_tab_refinement(self, host: ctk.CTkScrollableFrame):
        pad = self._padded(host)
        self._ref_toggle_content = ctk.CTkFrame(pad, fg_color="transparent", corner_radius=0)
        self._ref_toggle_content.pack(fill="x")

        self._ref_provider_content = ctk.CTkFrame(pad, fg_color="transparent", corner_radius=0)
        self._ref_provider_content.pack(fill="x")

        self._ref_prompt_content = ctk.CTkFrame(pad, fg_color="transparent", corner_radius=0)
        self._ref_prompt_content.pack(fill="both", expand=True, pady=(8, 0))

    def _build_tab_glossary(self, host: ctk.CTkScrollableFrame):
        pad = self._padded(host)
        ctk.CTkLabel(
            pad,
            text=(
                "Add domain-specific terms, acronyms, and technical words so they're\n"
                "transcribed correctly. Pulled into prompts via {custom_glossary}."
            ),
            font=FONT_SM,
            text_color=C_TEXT2,
            fg_color="transparent",
            justify="left",
            anchor="w",
            wraplength=440,
        ).pack(anchor="w", pady=(0, 8))
        self._glossary_content = pad

    def _build_tab_hotkeys(self, host: ctk.CTkScrollableFrame):
        pad = self._padded(host)
        self._hotkeys_content = pad

    def _build_tab_apikeys(self, host: ctk.CTkScrollableFrame):
        pad = self._padded(host)
        ctk.CTkLabel(
            pad,
            text="Keys are stored locally. Only the providers you actually use need a key.",
            font=FONT_SM,
            text_color=C_TEXT2,
            fg_color="transparent",
            anchor="w",
        ).pack(anchor="w", pady=(0, 8))
        self._apikeys_content = pad

    def _build_tab_advanced(self, host: ctk.CTkScrollableFrame):
        pad = self._padded(host)
        self._advanced_content = pad

    # ── Footer ─────────────────────────────────────────────────────────────────
    def _build_footer(self, parent: ctk.CTkFrame):
        inner = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0)
        inner.pack(fill="both", expand=True, padx=16)

        # Status pill (left)
        pill = ctk.CTkFrame(inner, fg_color="transparent", corner_radius=0)
        pill.pack(side="left", fill="y")

        self._status_dot = tk.Canvas(
            pill, width=10, height=10,
            bg=C_CHROME, highlightthickness=0,
        )
        self._status_dot.pack(side="left", anchor="center", pady=(0, 0))
        self._status_label = ctk.CTkLabel(
            pill, text="Ready to start",
            font=FONT_SM, text_color=C_TEXT2, fg_color="transparent",
        )
        self._status_label.pack(side="left", padx=(6, 0))
        self._draw_status_dot(False)

        # Buttons (right)
        btns = ctk.CTkFrame(inner, fg_color="transparent", corner_radius=0)
        btns.pack(side="right", fill="y", pady=10)

        ctk.CTkButton(
            btns, text="Reset",
            height=31, font=FONT_SM,
            fg_color="transparent", text_color=C_TEXT2,
            hover_color=C_SURFACE, border_width=1, border_color=C_BORDER,
            corner_radius=6, command=self._reset_to_defaults,
        ).pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            btns, text="Test Configuration",
            height=31, font=FONT_SM,
            fg_color=C_SURFACE, text_color=C_TEXT2,
            hover_color=C_SURFACE2, border_width=1, border_color=C_BORDER,
            corner_radius=6, command=self._test_configuration,
        ).pack(side="left", padx=(0, 6))

        self._start_btn = ctk.CTkButton(
            btns, text="Start Application",
            height=31, font=FONT_BOLD,
            fg_color=C_ACCENT, text_color="#1a1205",
            hover_color=C_ACCENT2, corner_radius=6,
            command=self._toggle_application,
        )
        self._start_btn.pack(side="left")

    def _draw_status_dot(self, running: bool):
        if not self._status_dot:
            return
        self._status_dot.delete("all")
        color = "#27ae60" if running else C_TEXT3
        self._status_dot.create_oval(1, 1, 9, 9, fill=color, outline="")

    def _update_status(self, running: bool):
        self._draw_status_dot(running)
        if self._status_label:
            if running:
                self._status_label.configure(text="Running", text_color="#27ae60")
            else:
                self._status_label.configure(text="Ready to start", text_color=C_TEXT2)
        if self._start_btn:
            if running:
                self._start_btn.configure(
                    text="Stop Application",
                    fg_color=C_SURFACE, text_color=C_TEXT,
                    hover_color=C_SURFACE2,
                )
            else:
                self._start_btn.configure(
                    text="Start Application",
                    fg_color=C_ACCENT, text_color="#1a1205",
                    hover_color=C_ACCENT2,
                )

    # ── Sections ───────────────────────────────────────────────────────────────
    def _init_sections(self):
        if self.api_section is not None:
            return

        self.api_section = APISection(
            stt_parent=self._stt_tab_content,
            refinement_parent=self._ref_provider_content,
            keys_parent=self._apikeys_content,
            on_change=self._on_config_changed,
        )
        self.feature_flags_section = FeatureFlagsSection(
            refinement_parent=self._ref_toggle_content,
            advanced_parent=self._advanced_content,
        )
        self.hotkey_section = HotkeySection(self._hotkeys_content)
        self.glossary_section = GlossarySection(
            self._glossary_content,
            self.root,
            self.config.custom_glossary,
            on_change=self._on_config_changed,
        )
        self.prompt_section = PromptSection(
            self._ref_prompt_content,
            self.root,
            self.config.custom_refinement_prompt,
            on_change=self._on_config_changed,
        )

    # ── Variable traces ────────────────────────────────────────────────────────
    def _setup_variable_traces(self):
        if self._variable_traces:
            return
        self._suspend_change_events = True
        try:
            all_vars = []
            if self.api_section:
                all_vars.extend([
                    self.api_section.stt_provider_var,
                    self.api_section.openai_api_key_var,
                    self.api_section.deepgram_api_key_var,
                    self.api_section.cerebras_api_key_var,
                    self.api_section.gemini_api_key_var,
                    self.api_section.custom_api_key_var,
                    self.api_section.stt_model_var,
                    self.api_section.refinement_provider_var,
                    self.api_section.refinement_model_var,
                    self.api_section.custom_endpoint_var,
                    self.api_section.custom_stt_endpoint_var,
                    self.api_section.custom_refinement_endpoint_var,
                    self.api_section.parakeet_endpoint_var,
                    self.api_section.parakeet_streaming_enabled_var,
                    self.api_section.parakeet_streaming_vad_end_silence_ms_var,
                    self.api_section.parakeet_streaming_max_chunk_seconds_var,
                    self.api_section.parakeet_streaming_batch_size_var,
                    self.api_section.parakeet_streaming_batch_window_ms_var,
                ])
            if self.hotkey_section:
                all_vars.extend([
                    self.hotkey_section.hotkey_var,
                    self.hotkey_section.toggle_hotkey_var,
                ])
            if self.feature_flags_section:
                all_vars.extend([
                    self.feature_flags_section.enable_text_refinement_var,
                    self.feature_flags_section.enable_logging_var,
                    self.feature_flags_section.enable_audio_feedback_var,
                    self.feature_flags_section.debug_mode_var,
                ])
            for var in all_vars:
                tid = var.trace_add("write", self._on_config_changed)
                self._variable_traces.append((var, tid))
        finally:
            self._suspend_change_events = False

    # ── Config change pipeline ─────────────────────────────────────────────────
    def _on_config_changed(self, *args):
        self._update_effective_control_state()
        if self._suspend_change_events:
            return
        if self.root and self.root.winfo_exists():
            if self._pending_update_job:
                try:
                    self.root.after_cancel(self._pending_update_job)
                except Exception:
                    pass
            self._pending_update_job = self.root.after(
                CONFIG_CHANGE_DEBOUNCE_DELAY_MS, self._apply_config_changes
            )
        else:
            self._apply_config_changes()

    def _apply_config_changes(self, force: bool = False):
        self._pending_update_job = None
        self._notify_config_changed(force=force)

    def _notify_config_changed(self, *, force: bool = False):
        new_config = self._get_config_from_sections()
        if not force and new_config == self.config:
            return
        self.config = new_config
        if self.on_config_changed:
            try:
                self.on_config_changed(new_config)
            except Exception as error:
                logger.error(f"Error in configuration change callback: {error}")
        if self.is_running and self.app_instance:
            try:
                self.app_instance.update_configuration(new_config)
            except Exception as error:
                logger.error(f"Failed to update running app configuration: {error}")
        if self._initialization_complete:
            self._config_persistence.save_async(new_config, self.config_file_path)

    def _get_config_from_sections(self) -> PushToTalkConfig:
        api = self.api_section.get_values()
        hk = self.hotkey_section.get_values()
        ff = self.feature_flags_section.get_values()
        return PushToTalkConfig(
            stt_provider=api["stt_provider"],
            openai_api_key=api["openai_api_key"],
            deepgram_api_key=api["deepgram_api_key"],
            cerebras_api_key=api["cerebras_api_key"],
            gemini_api_key=api["gemini_api_key"],
            custom_api_key=api["custom_api_key"],
            stt_model=api["stt_model"],
            refinement_provider=api["refinement_provider"],
            refinement_model=api["refinement_model"],
            custom_endpoint=api["custom_endpoint"],
            custom_stt_endpoint=api["custom_stt_endpoint"],
            custom_refinement_endpoint=api["custom_refinement_endpoint"],
            parakeet_endpoint=api["parakeet_endpoint"],
            parakeet_streaming_enabled=api["parakeet_streaming_enabled"],
            parakeet_streaming_vad_end_silence_ms=api["parakeet_streaming_vad_end_silence_ms"],
            parakeet_streaming_max_chunk_seconds=api["parakeet_streaming_max_chunk_seconds"],
            parakeet_streaming_batch_size=api["parakeet_streaming_batch_size"],
            parakeet_streaming_batch_window_ms=api["parakeet_streaming_batch_window_ms"],
            hotkey=hk["hotkey"],
            toggle_hotkey=hk["toggle_hotkey"],
            enable_text_refinement=ff["enable_text_refinement"],
            enable_logging=ff["enable_logging"],
            enable_audio_feedback=ff["enable_audio_feedback"],
            debug_mode=ff["debug_mode"],
            custom_glossary=self.glossary_section.get_terms(),
            custom_refinement_prompt=self.prompt_section.get_prompt(),
        )

    def _update_sections_from_config(self, config: PushToTalkConfig):
        self._init_sections()
        self._suspend_change_events = True
        try:
            self.api_section.set_values(
                config.stt_provider,
                config.openai_api_key,
                config.deepgram_api_key,
                config.cerebras_api_key,
                config.gemini_api_key,
                config.custom_api_key,
                config.stt_model,
                config.refinement_provider,
                config.refinement_model,
                config.custom_endpoint,
                config.custom_stt_endpoint,
                config.custom_refinement_endpoint,
                config.parakeet_endpoint,
                config.parakeet_streaming_enabled,
                config.parakeet_streaming_vad_end_silence_ms,
                config.parakeet_streaming_max_chunk_seconds,
                config.parakeet_streaming_batch_size,
                config.parakeet_streaming_batch_window_ms,
            )
            self.hotkey_section.set_values(config.hotkey, config.toggle_hotkey)
            self.feature_flags_section.set_values(
                config.enable_text_refinement,
                config.enable_logging,
                config.enable_audio_feedback,
                config.debug_mode,
            )
            self.glossary_section.set_terms(config.custom_glossary)
            self.prompt_section.set_prompt(config.custom_refinement_prompt)
            self._update_effective_control_state()
        finally:
            self._suspend_change_events = False

    def _update_effective_control_state(self):
        # Gray out refinement controls when the master toggle is off.
        if not (self.feature_flags_section and self.api_section):
            return
        enabled = bool(self.feature_flags_section.enable_text_refinement_var.get())
        try:
            self.api_section.set_refinement_enabled(enabled)
            if self.prompt_section:
                self.prompt_section.set_enabled(enabled)
        except Exception:
            pass

    # ── Actions ────────────────────────────────────────────────────────────────
    def _test_configuration(self):
        report = self.api_section.test_api_keys()
        messagebox.showinfo("Configuration Test Results", report)

    def _reset_to_defaults(self):
        if messagebox.askyesno("Reset Configuration", "Reset all settings to defaults?"):
            self._update_sections_from_config(PushToTalkConfig())
            self._notify_config_changed(force=True)

    def _toggle_application(self):
        if not self.is_running:
            self._start_application()
        else:
            self._stop_application()

    def _start_application(self):
        config = self._get_config_from_sections()
        is_valid, error_msg = validate_configuration(config)
        if not is_valid:
            messagebox.showerror("Validation Error", error_msg)
            return
        try:
            self.config = config
            self._config_persistence.save_sync(config, self.config_file_path)
            from src.push_to_talk import PushToTalkApp
            self.app_instance = PushToTalkApp(self.config)
            self.app_thread = threading.Thread(
                target=self._run_application_thread, daemon=True
            )
            self.app_thread.start()
            self.is_running = True
            self._update_status(True)
            logger.info("Application started successfully")
        except Exception as e:
            logger.error(f"Failed to start application: {e}")
            messagebox.showerror("Error", f"Failed to start application:\n\n{e}")

    def _run_application_thread(self):
        try:
            if self.app_instance:
                self.app_instance.start(setup_signals=False)
                while self.app_instance and self.app_instance.is_running:
                    import time
                    time.sleep(0.1)
        except Exception as error:
            logger.error(f"Application thread error: {error}")
            self.root.after(0, lambda err=error: self._handle_app_error(err))

    def _handle_app_error(self, error):
        self.is_running = False
        self._update_status(False)
        messagebox.showerror(
            "Application Error",
            f"The application encountered an error:\n\n{error}\n\n"
            "Check push_to_talk.log for details.",
        )

    def _stop_application(self):
        try:
            if self.app_instance:
                self.app_instance.stop()
                logger.info("Application stopped by user")
            self.is_running = False
            self._update_status(False)
            if self.app_thread and self.app_thread.is_alive():
                self.app_thread.join(timeout=1)
            self.app_instance = None
            self.app_thread = None
        except Exception as e:
            logger.error(f"Error stopping application: {e}")
            messagebox.showerror("Error", f"Error stopping application:\n\n{e}")

    def _close_application(self):
        if self.is_running:
            if messagebox.askyesno(
                "Application Running",
                "The push-to-talk application is running.\nStop it and close?",
            ):
                self._stop_application()
                self.result = "close"
                self.root.quit()
        else:
            self.result = "close"
            self.root.quit()

    # ── Entry point ────────────────────────────────────────────────────────────
    def show_modal(self) -> str:
        self.create_gui()
        self.root.protocol("WM_DELETE_WINDOW", self._close_application)
        self.root.mainloop()
        if self.is_running:
            self._stop_application()
        if self.root:
            self.root.destroy()
        return self.result or "close"


def show_configuration_gui(
    config: PushToTalkConfig | None = None,
) -> tuple[str, PushToTalkConfig]:
    if config is None:
        config = PushToTalkConfig()
    gui = ConfigurationWindow(config)
    result = gui.show_modal()
    return result, gui.config
