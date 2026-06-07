# Project Memory

Append-only lessons learned log. Newest notes go at the bottom. Keep entries brief, actionable, and free of secrets/PII.

## 2026-06-04

- Initial repo review: PushToTalk is a Python 3.12+ Windows-first desktop dictation app managed with `uv`; run locally with `uv run python main.py` or `uv run python main.py --debug`.
- Core orchestration lives in `src/push_to_talk.py`: `PushToTalkConfig` is a Pydantic model with assignment validation, and `PushToTalkApp` wires audio recording, transcription, text refinement, text insertion, hotkeys, worker queue, and background processing threads.
- Threading is intentional: hotkey service enqueues commands, worker thread handles start/stop, audio recorder has its own recording/init threads, and transcription/refinement runs in daemon processing threads so API latency does not block new recordings.
- Provider extension path: implement the relevant base class (`transcription_base.py` or `text_refiner_base.py`), register in the matching factory, add config validation/fields, update GUI API controls, and add provider-focused tests.
- GUI is modular under `src/gui/`; config edits should usually touch `configuration_window.py` plus the specific section module, and config-field changes should update `requires_component_reinitialization()` when a component must be rebuilt.
- Logging uses `loguru` throughout (`from loguru import logger`); avoid introducing stdlib `logging` patterns.
- Quality tools: Ruff/pre-commit is configured in `.pre-commit-config.yaml`; pytest config is in `pyproject.toml`; coverage omits `src/text_inserter.py` and `src/gui/*` via `.coveragerc`.
- Useful verification commands: `uv run pytest tests/ -v`, `uv run pytest tests/ -v -m "not integration"`, `uv run pytest tests/ --cov=src --cov-report=term-missing`, and `uv run pre-commit run --all-files`.
- Custom/local AI provider uses the existing OpenAI SDK with endpoint fields as `base_url`; use `custom_stt_endpoint` for `stt_provider="custom"` and `custom_refinement_endpoint` for `refinement_provider="custom"`. Legacy `custom_endpoint` remains as a fallback for older saved configs. Blank `custom_api_key` becomes the local placeholder key `"local"` at runtime.
- Full-project `uv run ruff check src tests` currently reports pre-existing unused imports in `src/audio_recorder.py` and `tests/test_audio_recorder.py`; lint touched files directly when avoiding unrelated cleanup.

## 2026-06-05

- Shadowfita Parakeet FastAPI should be called through native `POST /transcribe` with multipart field `file`; send `should_chunk=false` for short PushToTalk clips to avoid the upstream chunker unpacking bug seen at `/audio/transcriptions`.
- Parakeet WebSocket streaming uses `ws://.../ws` with raw 16 kHz mono int16 PCM frames; runtime refinement is disabled for streaming without mutating the saved `enable_text_refinement` preference.
- Keep the Parakeet WebSocket open across recordings; upstream `stream_routes.py` has an infinite consumer that can log ASGI `websocket.send` after client close if a per-recording socket is closed before late results.
- Parakeet streaming should pre-warm its persistent WebSocket from `PushToTalkApp.start()` and read recorder chunks at `PARAKEET_STREAMING_FRAME_SAMPLES` (512 samples / 32 ms) so audio is sent in smoother VAD-native frames.
- A one-time upstream `RuntimeError: Unexpected ASGI message 'websocket.send', after sending 'websocket.close'` confirms the Parakeet service can race `consumer()` sending late text after client close; client normal recording stop should use `finish_recording()` and keep the socket open.
- If Parakeet responses stop after light usage, check for a stale streaming session: server/client close can end the receive thread without a request-time exception, so session reuse must require an active thread, not just `error is None`.
- Parakeet streaming latency knobs are client-configurable and sent as WebSocket query params: `vad_end_silence_ms` default 250, `vad_max_chunk_seconds` default 8.0, `transcription_batch_size` default 4, and `transcription_batch_window_ms` default 15; server must read these params for behavior to change.
- Parakeet REST recordings auto-stop gracefully after `parakeet_rest_auto_stop_seconds` (default 120.0) via the worker queue, then process the saved WAV normally; duplicate manual stop/release after timer stop is ignored.

## 2026-06-05 (settings redesign)

- `APISection` constructor changed from `(parent, on_change)` to `(stt_parent, refinement_parent, keys_parent, on_change)` — three separate frames let STT, Refinement, and API-keys content live in different tabs.
- `FeatureFlagsSection` constructor changed from `(parent)` to `(refinement_parent, advanced_parent)` — enable_text_refinement row goes in Refinement tab; logging/audio/debug go in Advanced tab.
- `configuration_window.py` fully rewritten as 720×520 dark tabbed window (clam base theme, design tokens from `design_handoff_settings_redesign/README.md`). Tab panels are plain `tk.Frame`s; only the active one is `pack()`ed. `_init_sections()` is guarded so it only runs once.
- `tests/conftest.py` updated: `APISection` mock now passes 3 `MagicMock()` parents; `FeatureFlagsSection` mock passes 2; unused `status_section` assignment removed.
- `StatusSection` is no longer instantiated in the new window — footer status pill is drawn inline in `configuration_window.py`.
- All GUI files ported to `customtkinter` (v5.2.2, dependency added to pyproject.toml). `ctk.CTk` root, `ctk.CTkScrollableFrame` per tab, `ctk.CTkSwitch` for toggles, `ctk.CTkComboBox` for selects, `ctk.CTkTextbox` for prompt editor, `ctk.CTkScrollbar` inside glossary listbox (which stays as `tk.Listbox` with dark colors — CTk has no native listbox widget).
- CTkComboBox `command` callback receives the selected value string as first arg; use `lambda _v: handler()` to bridge to zero-arg callers.
- CTkComboBox option-list update uses `.configure(values=...)` not `combo["values"] = ...`.
- CTkEntry show-toggle uses `entry.cget("show")` and `entry.configure(show=...)`, NOT `entry["show"]`.
- CTk test isolation: passing `MagicMock()` as parent to CTk widget constructors causes hangs (CTk calls `after()` on a mock, which recurses or blocks). Fix: pass a real withdrawn `tk.Tk()` root as parent.
- Only one `tk.Tk()` per process: creating a second `tk.Tk()` after destroying the first causes sporadic skip/failure. The `mock_tk_root` fixture is `scope="session"` so a single root is shared across all GUI tests.
- `_update_custom_endpoint_visibility()` uses `pack()`/`pack_forget()` (not `grid()`). Tests that mock visibility calls should mock `.pack`, not `.grid`.
- `APISection._custom_stt_section` has a `custom_stt_endpoint_frame` property alias for backward compatibility with existing tests.
- Empty-first-tab bug: `_build_sidebar` called `_switch_tab("stt")` before `_build_tabs` populated `_tab_frames`; fix is to call `_switch_tab` in `_build_layout` after `_build_tabs`.
- `"  ".join(text)` spreads each CHARACTER with 2-space gaps (i.e. it iterates characters, not words) — do NOT use this as a letter-spacing substitute; it produces "P  R  O  V  I  D  E  R".
- GUI fonts bumped to 11–12pt (was 9–10pt) across all section files for readability.

## 2026-06-06 (settings redesign — Opus fidelity pass)

- Added a reusable `_subhead(parent, text, pill=None)` helper (uppercase label + divider line + optional amber pill) in `api_section.py` and `prompt_section.py` to match the design's `.subhead`/`.cond` treatment. Used for STT "Parakeet" (with `● Provider: parakeet` pill), "Streaming tuning", and "Custom Prompt".
- API-key "SET" badges: keyed the badge dict by `str(var)` because `tk.StringVar` is unhashable — do NOT use the var itself as a dict key. Badges toggle via a `trace_add("write")` per key var + `_refresh_all_key_badges()` called from `set_values`.
- Implemented the README's "gray out refinement controls when master toggle is OFF" behavior: `ConfigurationWindow._update_effective_control_state()` now calls `APISection.set_refinement_enabled(bool)` and `PromptSection.set_enabled(bool)`. Combo re-enable state is `"readonly"` for provider, `"normal"` for model.
- Hotkeys: value entry is now a fixed-width (170px) centered mono "keycap" with a transparent expanding spacer pushing the Record button to the right edge (was full-width expanding entry).
- Glossary empty-state: `tk.Listbox` has no native placeholder; use a `CTkLabel` `.place(relx=.5, rely=.5, anchor=center)` over the listbox frame, toggled in `_update_empty_state()` via `listbox.size()`.
- Removed redundant prompt-section top "Show default prompts" button; the button-row "Show defaults" entry now doubles as the toggle (its ref is captured as `_show_defaults_btn`).
- Renamed Advanced toggle "Enable Audio Feedback" → "Audio Feedback" to match design.
- Design's "WINDOWS" section (Close to taskbar / Start with Windows) was intentionally NOT added — those fields don't exist in `PushToTalkConfig` and would be non-functional dead toggles.
- `api_section.py` shows 24 pre-existing ruff E701 (aligned one-line `elif provider == ...: self.x = ...` blocks) from the earlier uncommitted CTk rewrite — not from this pass; `ruff-format` (pre-commit) auto-splits them on commit, so left as-is to avoid unrelated churn.

## 2026-06-06 (settings redesign — review feedback fixes)

- **CTkFrame default height is 200px.** A `CTkFrame` with `pack_propagate(False)` and no `height=` set requests 200px and will inflate its parent. This caused the "Enable Text Refinement" master card to render ~200px tall: the 3px amber accent `bar` (in `settings_section._make_switch_row`, `pack_propagate(False)`, `fill="y"`, no height) forced the whole row to 200px. Fix: give the bar `height=1`; `fill="y"` still stretches it to the card's real height. Same trap applies to any empty spacer frame — avoid `CTkFrame(...).pack(fill="x", expand=True)` as a spacer; instead pack the button `side="right"` and the field `side="left"` (no spacer).
- Dropdowns switched from `CTkComboBox` → `CTkOptionMenu` (in `api_section._combo`). CTkComboBox renders as a split editable entry + separate arrow button ("abrupt arrow, not filled"); CTkOptionMenu is a single filled click-anywhere select that matches the design's `.control.select`. CTkOptionMenu has NO `border_width`/`border_color`, so wrap it in a 1px-bordered `CTkFrame` shell (`fg_color=C_INPUT`, `pack_propagate(False)`, `height=33`) to keep the inset border parity with text inputs. Set `button_color == fg_color` so the chevron segment blends into one box; `dynamic_resizing=False`, `anchor="w"`. State is `normal`/`disabled` only (no `readonly`) — updated `set_refinement_enabled` accordingly.
- Verified visually by screenshotting the live window: launch `_shot_window.py <tab>` as a background job (positions window topmost at +200+140), then capture with .NET `System.Drawing` `CopyFromScreen` → PNG (Pillow is NOT installed, so `pyautogui.screenshot`/PIL paths are unavailable). Remember to delete the temp `.py`/`.png` afterward.

## 2026-06-07

- Parakeet streaming insertion should add boundary separators as a leading space on the next finalized segment, not as a trailing space on the previous paste. Some target apps/clipboard paths can drop or normalize trailing whitespace, causing joins like `Shady.All`; `PushToTalkApp._format_streaming_insert_segment()` keeps the spacing in the pasted segment that needs it.
- Parakeet streaming can also return finalized text with missing spaces after sentence punctuation inside a single segment, e.g. `Shady.all` or `imitating.Will`. Repair `([.!?])([A-Za-z])` to `\1 \2` in the streaming insertion formatter before paste.
- Exact regression `The Real Slim Shady.Please stand up.` passes through the current streaming formatter; if the GUI still emits it without the space, first confirm the app process was restarted after code changes because the WebSocket insert path has no alternate bypass in `src/`.
- Sentence-punctuation spacing repair now uses shared `normalize_sentence_spacing()` in `src/push_to_talk.py` and is applied before both Parakeet streaming segment paste and the regular background final-text paste, covering WebSocket fallback/config mismatch cases. It skips internal single-letter acronym periods such as `U.S.A` but still repairs the final boundary in `U.S.A.Today`.
- After Claude Code review, `normalize_sentence_spacing()` was tightened to preserve lowercase dotted tokens like `example.com`, `config.py`, and `self.value`; lowercase repairs are limited to likely proper-name boundaries such as `Shady.are`, while uppercase sentence starts like `imitating.With` are still repaired.
- Parakeet WebSocket stability gotcha: duplicate streaming START/STOP commands must be ignored before touching the socket, and a new recording must wait for the prior `ParakeetStreamingDrain` to finish before reusing the long-lived connection. Closing/reusing during the drain can trigger upstream FastAPI/uvicorn `websocket.send` after close and delayed/missing first transcripts.
- Parakeet streaming reconnect gotcha: config reinitialization must close the old WebSocket during `_initialize_components(force_recreate=True)` and immediately prewarm a new session when the app is still running. Leaving the stale session open defers disconnect/reconnect to the next recording and can surface the upstream ASGI late-send trace at the worst time.
- Parakeet streaming finalized-segment spacing is encoded as a leading space on the next chunk via `streaming_segment_needs_leading_space()`; incoming chunks are stripped first, and only punctuation-continuation chunks like `.`, `,`, `)`, `]`, `}` avoid the added boundary.
- Do not reset `streaming_insert_has_text` in `_start_parakeet_streaming_recording()`: short pause/restart cycles can split adjacent words across recordings (`The` then `Wheels`), and clearing the state loses the required leading-space boundary.
- Parakeet streaming spacing now tracks `streaming_insert_last_char` instead of a boolean and preserves incoming leading whitespace from WebSocket payloads; debug logs show raw/formatted streaming segments plus previous/next boundary chars.
- Parakeet streaming boundary spaces are now sent as a real `Space` keypress via `TextInserter.insert_space()` before pasting the next chunk, because some target apps appear to drop/ignore leading spaces embedded in clipboard payloads.
- `streaming_boundary_space_keypress` is an Advanced runtime toggle: ON sends chunk boundaries as `Space` keypress + paste chunk, OFF uses the more efficient single clipboard paste with leading whitespace (`" chunk"`) for text fields that preserve it.
- Do not gate Parakeet streaming text insertion on the physical PTT key state: live WebSocket chunks must paste while PTT is held. Flush issues belong in `ParakeetStreamingSession.finish_recording()` by making sure queued final audio/silence frames are sent before the final-text drain window.

## 2026-06-07 (improvement pass)

- Provider/model lists are centralized in src/provider_registry.py; config validation, factories, and GUI provider/model dropdowns should read from that registry instead of duplicating hard-coded provider arrays.

- Streaming text insertion now lives in src/streaming_text_inserter.py, final audio processing/debug export in src/audio_processing_service.py, and sentence spacing helpers in src/text_formatting.py; PushToTalkApp keeps wrapper methods for compatibility.
- Queue workers should call 	ask_done() for every successful get(), including sentinel commands; _worker_loop() uses a inally path for that invariant.
- Config writes use src/config/file_io.py::write_json_atomic() (NamedTemporaryFile in target dir + os.replace) to avoid partially written JSON during async saves.

- Removed stale tracked coverage.xml and added it to .gitignore; regenerate coverage locally instead of relying on a checked-in report that can drift from src/. README provider/config docs now include Parakeet, custom STT/refinement endpoints, streaming knobs, and current hotkey defaults.
