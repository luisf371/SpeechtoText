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
