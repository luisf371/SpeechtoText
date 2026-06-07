import os
import re
import sys
import time
from loguru import logger
import threading
import signal
import queue
from typing import Optional, Dict, Any
import json

from pydantic import BaseModel, Field, field_validator, model_validator, ConfigDict

from src.audio_recorder import AudioRecorder
from src.transcriber_factory import TranscriberFactory
from src.transcription_base import TranscriberBase
from src.transcription_parakeet_streaming import (
    PARAKEET_STREAMING_CHANNELS,
    PARAKEET_STREAMING_DEFAULT_BATCH_SIZE,
    PARAKEET_STREAMING_DEFAULT_BATCH_WINDOW_MS,
    PARAKEET_STREAMING_DEFAULT_MAX_CHUNK_SECONDS,
    PARAKEET_STREAMING_DEFAULT_VAD_END_SILENCE_MS,
    PARAKEET_STREAMING_FRAME_BYTES,
    PARAKEET_STREAMING_FRAME_SAMPLES,
    PARAKEET_STREAMING_SAMPLE_RATE,
    ParakeetStreamingSession,
    build_parakeet_ws_url,
)
from src.text_refiner_base import TextRefinerBase
from src.text_refiner_factory import TextRefinerFactory
from src.text_inserter import TextInserter
from src.hotkey_service import HotkeyService
from src.utils import play_start_feedback, play_stop_feedback
from src.exceptions import (
    ConfigurationError,
    TranscriptionError,
    TextRefinementError,
    TextInsertionError,
    APIError,
)

def _get_default_hotkey() -> str:
    """Get platform-specific default hotkey."""
    return f"{'cmd' if sys.platform == 'darwin' else 'ctrl'}+space+^"


def _get_default_toggle_hotkey() -> str:
    """Get platform-specific default toggle hotkey."""
    return f"{'cmd' if sys.platform == 'darwin' else 'ctrl'}+cmd"


def normalize_sentence_spacing(text: str) -> str:
    """Repair common transcription joins after sentence punctuation."""

    def previous_token_starts_lowercase(index: int) -> bool:
        token_match = re.search(r"[A-Za-z][A-Za-z0-9_-]*$", text[:index])
        return bool(token_match and token_match.group(0)[0].islower())

    def add_space(match: re.Match[str]) -> str:
        punctuation = match.group(1)
        letter = match.group(2)
        previous_index = match.start() - 1
        is_single_letter_period = (
            punctuation == "."
            and previous_index >= 0
            and text[previous_index].isalpha()
            and (previous_index == 0 or not text[previous_index - 1].isalnum())
            and match.end() < len(text)
            and text[match.end()] == "."
        )
        if is_single_letter_period:
            return match.group(0)
        if (
            punctuation == "."
            and letter.islower()
            and previous_token_starts_lowercase(match.start())
        ):
            return match.group(0)
        return f"{punctuation} {letter}"

    return re.sub(r"([.!?])([A-Za-z])", add_space, text)


def streaming_segment_separator(previous_char: str | None, text: str) -> str:
    """Return the separator to paste before a finalized streaming segment."""
    if previous_char is None or not text:
        return ""
    if previous_char.isspace():
        return ""
    if text[0] in ".,;:!?)]}'":
        return ""
    if previous_char in "([{/'-":
        return ""
    return " "


def last_streaming_text_char(text: str) -> str | None:
    """Return the last meaningful character from text inserted by streaming."""
    stripped = text.rstrip()
    if not stripped:
        return None
    return stripped[-1]


def normalize_streaming_segment_text(text: str) -> str:
    """Normalize a finalized streaming segment for insertion."""
    return normalize_sentence_spacing(text.strip())


def format_streaming_insert_segment(
    text: str, previous_char: str | None
) -> tuple[str, str, str | None]:
    """Format a finalized streaming segment and return updated boundary state."""
    text = normalize_streaming_segment_text(text)
    if not text:
        return "", "", previous_char

    separator = streaming_segment_separator(previous_char, text)
    return separator, text, last_streaming_text_char(text)


class PushToTalkConfig(BaseModel):
    """Configuration class for PushToTalk application with Pydantic validation."""

    model_config = ConfigDict(validate_assignment=True)

    # Transcription provider settings
    stt_provider: str = Field(
        default="deepgram",
        description="STT provider: 'openai', 'deepgram', 'parakeet', or 'custom'",
    )
    openai_api_key: str = Field(default="", description="OpenAI API key")
    deepgram_api_key: str = Field(default="", description="Deepgram API key")
    stt_model: str = Field(default="nova-3", description="STT model name")

    # Text refinement settings
    refinement_provider: str = Field(
        default="cerebras",
        description="Text refinement provider: 'openai', 'cerebras', 'gemini', or 'custom'",
    )
    refinement_model: str = Field(
        default="llama-3.3-70b", description="Model for text refinement"
    )
    cerebras_api_key: str = Field(default="", description="Cerebras API key")
    gemini_api_key: str = Field(default="", description="Gemini API key")
    custom_api_key: str = Field(default="", description="Custom API key")
    custom_endpoint: str = Field(
        default="",
        description="Legacy custom API endpoint URL for OpenAI-compatible APIs",
    )
    custom_stt_endpoint: str = Field(
        default="",
        description="Custom STT endpoint URL for OpenAI-compatible APIs",
    )
    parakeet_endpoint: str = Field(
        default="http://localhost:8000",
        description="Parakeet FastAPI service base URL or /transcribe URL",
    )
    parakeet_streaming_enabled: bool = Field(
        default=False,
        description="Use Parakeet WebSocket streaming instead of REST transcription",
    )
    parakeet_streaming_vad_end_silence_ms: int = Field(
        default=PARAKEET_STREAMING_DEFAULT_VAD_END_SILENCE_MS,
        gt=0,
        description="Parakeet WebSocket VAD trailing silence threshold in milliseconds",
    )
    parakeet_streaming_max_chunk_seconds: float = Field(
        default=PARAKEET_STREAMING_DEFAULT_MAX_CHUNK_SECONDS,
        gt=0,
        description="Parakeet WebSocket maximum VAD chunk duration in seconds",
    )
    parakeet_streaming_batch_size: int = Field(
        default=PARAKEET_STREAMING_DEFAULT_BATCH_SIZE,
        gt=0,
        description="Parakeet WebSocket transcription micro-batch size",
    )
    parakeet_streaming_batch_window_ms: int = Field(
        default=PARAKEET_STREAMING_DEFAULT_BATCH_WINDOW_MS,
        ge=0,
        description="Parakeet WebSocket transcription micro-batch gather window in milliseconds",
    )
    parakeet_rest_auto_stop_seconds: float = Field(
        default=120.0,
        gt=0,
        description="Maximum Parakeet REST recording duration before graceful auto-stop",
    )
    custom_refinement_endpoint: str = Field(
        default="",
        description="Custom refinement endpoint URL for OpenAI-compatible APIs",
    )

    # Audio settings
    sample_rate: int = Field(default=16000, gt=0, description="Audio sample rate in Hz")
    chunk_size: int = Field(default=1024, gt=0, description="Audio chunk size")
    channels: int = Field(default=1, gt=0, le=2, description="Audio channels (1 or 2)")

    # Hotkey settings - will use platform-specific defaults
    hotkey: str = Field(
        default_factory=_get_default_hotkey, description="Push-to-talk hotkey"
    )
    toggle_hotkey: str = Field(
        default_factory=_get_default_toggle_hotkey, description="Toggle hotkey"
    )

    # Feature flags
    enable_text_refinement: bool = Field(
        default=True, description="Enable text refinement"
    )
    enable_logging: bool = Field(default=True, description="Enable logging")
    enable_audio_feedback: bool = Field(
        default=True, description="Enable audio feedback"
    )
    streaming_boundary_space_keypress: bool = Field(
        default=True,
        description="Press Space for streaming text boundaries instead of pasting leading whitespace",
    )
    debug_mode: bool = Field(default=False, description="Enable debug mode")

    # Custom glossary for transcription refinement
    custom_glossary: list[str] = Field(
        default_factory=list, description="Custom glossary terms"
    )

    # Custom refinement prompt
    custom_refinement_prompt: str = Field(
        default="",
        description="Custom text refinement prompt. Use {custom_glossary} placeholder for glossary terms.",
    )

    @field_validator("stt_provider")
    @classmethod
    def validate_stt_provider(cls, v: str) -> str:
        """Validate STT provider."""
        if v not in ["openai", "deepgram", "parakeet", "custom"]:
            raise ValueError(
                f"stt_provider must be 'openai', 'deepgram', 'parakeet' or 'custom', got '{v}'"
            )
        return v

    @field_validator("refinement_provider")
    @classmethod
    def validate_refinement_provider(cls, v: str) -> str:
        """Validate refinement provider."""
        if v not in ["openai", "cerebras", "gemini", "custom"]:
            raise ValueError(
                f"refinement_provider must be 'openai', 'cerebras', 'gemini' or 'custom', got '{v}'"
            )
        return v

    @model_validator(mode="after")
    def validate_hotkeys_different(self) -> "PushToTalkConfig":
        """Validate that push-to-talk and toggle hotkeys are different."""
        if self.hotkey == self.toggle_hotkey:
            raise ValueError("Push-to-talk and toggle hotkeys must be different")
        return self

    def get_custom_stt_endpoint(self) -> str:
        """Get custom STT endpoint, falling back to legacy custom_endpoint."""
        return self.custom_stt_endpoint or self.custom_endpoint

    def get_custom_refinement_endpoint(self) -> str:
        """Get custom refinement endpoint, falling back to legacy custom_endpoint."""
        return self.custom_refinement_endpoint or self.custom_endpoint

    def is_parakeet_streaming_active(self) -> bool:
        """Return True when Parakeet WebSocket streaming should be used."""
        return self.stt_provider == "parakeet" and self.parakeet_streaming_enabled

    def is_text_refinement_effective(self) -> bool:
        """Return True when text refinement can run for the active STT mode."""
        return self.enable_text_refinement and not self.is_parakeet_streaming_active()

    def save_to_file(self, filepath: str):
        """Save configuration to JSON file."""
        with open(filepath, "w") as f:
            f.write(self.model_dump_json(indent=2))

    @classmethod
    def load_from_file(cls, filepath: str) -> "PushToTalkConfig":
        """Load configuration from JSON file."""
        try:
            with open(filepath, "r") as f:
                data = json.load(f)
            return cls(**data)
        except Exception as e:
            logger.warning(f"Failed to load config from {filepath}: {e}")
            return cls()

    def requires_component_reinitialization(self, other: "PushToTalkConfig") -> bool:
        """
        Check if component reinitialization is required when comparing with another config.

        This method implements smart component reinitialization by categorizing fields
        as either "critical" (requiring expensive component recreation) or "non-critical"
        (runtime-only settings that can be updated without reinitialization).

        Critical Fields (require reinitialization):
        - API keys, model settings (transcriber/refiner must be recreated)
        - Audio parameters (audio recorder must be recreated)
        - Hotkeys (hotkey service must be recreated)
        - Processing settings (audio processor must be recreated)
        - Custom glossary (text refiner must be updated)

        Non-Critical Fields (runtime-only changes):
        - enable_logging: Runtime logging toggle
        - enable_audio_feedback: Runtime audio feedback toggle
        - streaming_boundary_space_keypress: Runtime text insertion behavior

        Args:
            other: The other configuration to compare against

        Returns:
            True if component reinitialization is needed, False otherwise
        """
        # Fields that do NOT require component reinitialization when changed
        # These are UI-only or runtime-only settings that don't affect core components
        non_critical_fields = {
            "enable_logging",  # Logging toggle (runtime setting)
            "enable_audio_feedback",  # Audio feedback toggle (runtime setting)
            "streaming_boundary_space_keypress",  # Streaming insertion behavior
        }

        # Get all fields from the Pydantic model
        all_fields = set(self.__class__.model_fields.keys())

        # Compare all fields except the non-critical ones
        critical_fields = all_fields - non_critical_fields

        for field_name in critical_fields:
            if getattr(self, field_name) != getattr(other, field_name):
                return True

        return False


class PushToTalkApp:
    def __init__(
        self,
        config: Optional[PushToTalkConfig] = None,
        audio_recorder: Optional[AudioRecorder] = None,
        transcriber: Optional["TranscriberBase"] = None,
        text_refiner: Optional[TextRefinerBase] = None,
        text_inserter: Optional[TextInserter] = None,
        hotkey_service: Optional[HotkeyService] = None,
    ):
        """
        Initialize the PushToTalk application.

        Supports dependency injection for testing and customization. If dependencies
        are not provided, default instances will be created based on configuration.

        Args:
            config: Configuration object. If None, default config is used.
            audio_recorder: Optional AudioRecorder instance. If None, created from config.
            transcriber: Optional TranscriberBase instance. If None, created from config.
            text_refiner: Optional TextRefiner instance. If None, created from config.
            text_inserter: Optional TextInserter instance. If None, created from config.
            hotkey_service: Optional HotkeyService instance. If None, created from config.
        """
        self.config = config or PushToTalkConfig()

        # Validate API key based on selected provider
        if self.config.stt_provider == "openai":
            if not self.config.openai_api_key:
                self.config.openai_api_key = os.getenv("OPENAI_API_KEY")
                if not self.config.openai_api_key:
                    raise ConfigurationError(
                        "OpenAI API key is required. Set OPENAI_API_KEY environment variable or provide in config."
                    )
        elif self.config.stt_provider == "deepgram":
            if not self.config.deepgram_api_key:
                self.config.deepgram_api_key = os.getenv("DEEPGRAM_API_KEY")
                if not self.config.deepgram_api_key:
                    raise ConfigurationError(
                        "Deepgram API key is required. Set DEEPGRAM_API_KEY environment variable or provide in config."
                    )
        elif self.config.stt_provider == "parakeet":
            if not self.config.parakeet_endpoint:
                raise ConfigurationError(
                    "Parakeet STT provider requires a Parakeet endpoint URL."
                )
            if self.config.parakeet_streaming_enabled:
                self._validate_parakeet_streaming_audio_settings()
        elif self.config.stt_provider == "custom":
            if not self.config.get_custom_stt_endpoint():
                raise ConfigurationError(
                    "Custom STT provider requires a custom STT endpoint URL."
                )
            if not self.config.custom_api_key:
                self.config.custom_api_key = os.getenv("OPENAI_API_KEY") or "local"
        else:
            raise ConfigurationError(
                f"Unknown STT provider: {self.config.stt_provider}"
            )

        # Use injected dependencies or initialize to None (will be created in _initialize_components)
        self.audio_recorder = audio_recorder
        self.transcriber = transcriber
        self.text_refiner = text_refiner
        self.text_inserter = text_inserter
        self.hotkey_service = hotkey_service

        # Track which components were injected (to preserve them during reinitialization)
        self._injected_audio_recorder = audio_recorder is not None
        self._injected_transcriber = transcriber is not None
        self._injected_text_refiner = text_refiner is not None
        self._injected_text_inserter = text_inserter is not None
        self._injected_hotkey_service = hotkey_service is not None

        # State management
        self.is_running = False

        # Command queue for handling hotkey events
        self.command_queue = queue.Queue()
        self.worker_thread = None

        # Track background processing threads (one per recording)
        self.processing_threads = []
        self.processing_threads_lock = threading.Lock()
        self.streaming_session: Optional[ParakeetStreamingSession] = None
        self.streaming_insert_queue: queue.Queue[str | None] = queue.Queue()
        self.streaming_insert_thread: Optional[threading.Thread] = None
        self.streaming_insert_lock = threading.Lock()
        self.streaming_insert_last_char: str | None = None
        self.streaming_frame_buffer = bytearray()
        self.streaming_drain_thread: Optional[threading.Thread] = None
        self.rest_auto_stop_timer: Optional[threading.Timer] = None

        # Initialize all components (only creates components that are None)
        self._initialize_components()

        logger.info("PushToTalk application initialized")

    def _initialize_components(self, force_recreate: bool = False):
        """Initialize or reinitialize all components with current configuration.

        Args:
            force_recreate: If True, recreate all components even if they exist.
                           If False, only create components that are None (injected dependencies).

        When force_recreate=False, injected dependencies are preserved (for testing).
        When force_recreate=True, all components are recreated (for configuration updates).
        """
        # Store whether hotkey service was running before cleanup
        hotkey_service_was_running = (
            self.hotkey_service and self.hotkey_service.is_service_running()
        )

        # Clean up existing components if they exist
        if self.hotkey_service:
            self.hotkey_service.stop_service()

        if force_recreate:
            self._close_streaming_session()

        # Clean up audio recorder before recreating (PyAudio resources must be explicitly released)
        if self.audio_recorder and not self._injected_audio_recorder and force_recreate:
            self.audio_recorder.shutdown()

        # Determine which components to recreate
        # Never recreate injected components (preserves mocks for testing)
        # For non-injected components: recreate if force_recreate=True or if None
        recreate_audio_recorder = not self._injected_audio_recorder and (
            force_recreate or self.audio_recorder is None
        )
        recreate_transcriber = not self._injected_transcriber and (
            force_recreate or self.transcriber is None
        )
        recreate_text_refiner = not self._injected_text_refiner and (
            force_recreate or self.text_refiner is None
        )
        recreate_text_inserter = not self._injected_text_inserter and (
            force_recreate or self.text_inserter is None
        )
        recreate_hotkey_service = not self._injected_hotkey_service and (
            force_recreate or self.hotkey_service is None
        )

        # Initialize audio recorder
        if recreate_audio_recorder:
            self.audio_recorder = self._create_default_audio_recorder()

        # Initialize transcriber
        if recreate_transcriber:
            self.transcriber = self._create_default_transcriber()

        # Initialize text refiner
        if recreate_text_refiner:
            self.text_refiner = self._create_default_text_refiner()

        # Set glossary and custom prompt if text refiner is enabled
        if self.text_refiner:
            if self.config.custom_glossary:
                self.text_refiner.set_glossary(self.config.custom_glossary)
            if self.config.custom_refinement_prompt:
                self.text_refiner.set_custom_prompt(
                    self.config.custom_refinement_prompt
                )

        # Set glossary for transcriber if enabled
        if self.transcriber:
            self.transcriber.set_glossary(self.config.custom_glossary)

        # Initialize text inserter
        if recreate_text_inserter:
            self.text_inserter = self._create_default_text_inserter()

        # Initialize hotkey service
        if recreate_hotkey_service:
            self.hotkey_service = self._create_default_hotkey_service()

        # Setup hotkey callbacks
        self.hotkey_service.set_callbacks(
            on_start_recording=self._on_start_recording,
            on_stop_recording=self._on_stop_recording,
        )

        # Restart hotkey service if it was running before and application is still running
        if hotkey_service_was_running and self.is_running:
            self.hotkey_service.start_service()

    def _create_default_audio_recorder(self) -> AudioRecorder:
        """Create default AudioRecorder instance from configuration."""
        chunk_size = (
            PARAKEET_STREAMING_FRAME_SAMPLES
            if self.config.is_parakeet_streaming_active()
            else self.config.chunk_size
        )
        return AudioRecorder(
            sample_rate=self.config.sample_rate,
            chunk_size=chunk_size,
            channels=self.config.channels,
        )

    def _validate_parakeet_streaming_audio_settings(self):
        """Validate audio settings required by Parakeet WebSocket streaming."""
        if (
            self.config.sample_rate != PARAKEET_STREAMING_SAMPLE_RATE
            or self.config.channels != PARAKEET_STREAMING_CHANNELS
        ):
            raise ConfigurationError(
                "Parakeet WebSocket streaming requires 16 kHz mono audio "
                "(sample_rate=16000, channels=1)."
            )

    def _create_default_transcriber(self) -> TranscriberBase:
        """Create default TranscriberBase instance from configuration."""
        # Get the appropriate API key based on provider
        if self.config.stt_provider == "openai":
            api_key = self.config.openai_api_key or os.getenv("OPENAI_API_KEY")
        elif self.config.stt_provider == "deepgram":
            api_key = self.config.deepgram_api_key or os.getenv("DEEPGRAM_API_KEY")
        elif self.config.stt_provider == "parakeet":
            if not self.config.parakeet_endpoint:
                raise ConfigurationError(
                    "Parakeet STT provider requires a Parakeet endpoint URL."
                )
            api_key = "local"
        elif self.config.stt_provider == "custom":
            if not self.config.get_custom_stt_endpoint():
                raise ConfigurationError(
                    "Custom STT provider requires a custom STT endpoint URL."
                )
            api_key = self.config.custom_api_key or "local"
        else:
            raise ConfigurationError(
                f"Unknown STT provider: {self.config.stt_provider}"
            )

        base_url = (
            self.config.parakeet_endpoint
            if self.config.stt_provider == "parakeet"
            else (
                self.config.get_custom_stt_endpoint()
                if self.config.stt_provider == "custom"
                else None
            )
        )

        # Create transcriber using factory with glossary
        return TranscriberFactory.create_transcriber(
            provider=self.config.stt_provider,
            api_key=api_key,
            model=self.config.stt_model,
            glossary=self.config.custom_glossary,
            base_url=base_url,
        )

    def _create_default_text_refiner(self) -> Optional[TextRefinerBase]:
        """Create default TextRefiner instance from configuration."""
        if self.config.is_text_refinement_effective():
            # Get the appropriate API key based on provider
            if self.config.refinement_provider == "openai":
                api_key = self.config.openai_api_key or None
            elif self.config.refinement_provider == "cerebras":
                api_key = self.config.cerebras_api_key or None
            elif self.config.refinement_provider == "gemini":
                api_key = self.config.gemini_api_key or None
            elif self.config.refinement_provider == "custom":
                if not self.config.get_custom_refinement_endpoint():
                    raise ConfigurationError(
                        "Custom refinement provider requires a custom refinement endpoint URL."
                    )
                api_key = self.config.custom_api_key or "local"
            else:
                raise ConfigurationError(
                    f"Unknown refinement provider: {self.config.refinement_provider}"
                )

            if not api_key:
                raise ConfigurationError(
                    f"{self.config.refinement_provider} API key is required for text refinement. "
                    f"Provide in config."
                )

            # Only use custom endpoint if provider is custom
            base_url = (
                self.config.get_custom_refinement_endpoint()
                if self.config.refinement_provider == "custom"
                else None
            )

            return TextRefinerFactory.create_refiner(
                provider=self.config.refinement_provider,
                api_key=api_key,
                model=self.config.refinement_model,
                glossary=self.config.custom_glossary,
                base_url=base_url,
            )
        return None

    def _create_default_text_inserter(self) -> TextInserter:
        """Create default TextInserter instance from configuration."""
        return TextInserter()

    def _create_default_hotkey_service(self) -> HotkeyService:
        """Create default HotkeyService instance from configuration."""
        return HotkeyService(
            hotkey=self.config.hotkey or None,
            toggle_hotkey=self.config.toggle_hotkey or None,
        )

    def update_configuration(self, new_config: PushToTalkConfig):
        """
        Update the application configuration and reinitialize components.

        Args:
            new_config: New configuration object
        """
        logger.info("Updating application configuration")

        # Store old config for comparison
        old_config = self.config
        self.config = new_config

        # Check if we need to reinitialize components
        if new_config.requires_component_reinitialization(old_config):
            logger.info("Configuration changes require component reinitialization")
            self._initialize_components(force_recreate=True)
            if self.is_running and self.config.is_parakeet_streaming_active():
                self._ensure_streaming_insert_worker()
                self._ensure_streaming_session()
        else:
            logger.info("Configuration updated without requiring component changes")

    def get_configuration(self) -> PushToTalkConfig:
        """Get the current configuration."""
        return self.config

    def save_configuration(self, filepath: str = "push_to_talk_config.json"):
        """Save current configuration to file."""
        self.config.save_to_file(filepath)
        logger.info(f"Configuration saved to {filepath}")

    def start(self, setup_signals=True):
        """Start the PushToTalk application.

        Args:
            setup_signals: Whether to setup signal handlers (only works in main thread)
        """
        if self.is_running:
            logger.warning("Application is already running")
            return

        logger.info("Starting PushToTalk application...")

        self.is_running = True

        # Start command processing worker thread
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()

        self.hotkey_service.start_service()

        if self.config.is_parakeet_streaming_active():
            try:
                self._ensure_streaming_insert_worker()
                self._ensure_streaming_session()
                logger.info("Parakeet streaming WebSocket pre-warmed.")
            except Exception as e:
                logger.warning(
                    "Parakeet streaming WebSocket pre-warm failed; "
                    f"will retry on recording start: {e}"
                )

        logger.info("PushToTalk is running.")
        logger.info(f"Push-to-talk: Press and hold '{self.config.hotkey}' to record.")
        logger.info(
            f"Toggle mode: Press '{self.config.toggle_hotkey}' to start/stop recording."
        )

        # Setup signal handlers for graceful shutdown (only in main thread)
        if setup_signals:
            try:
                signal.signal(signal.SIGINT, self._signal_handler)
                signal.signal(signal.SIGTERM, self._signal_handler)
            except ValueError as e:
                # This happens when not in main thread - just log and continue
                logger.debug(f"Could not setup signal handlers: {e}")

    def stop(self):
        """Stop the PushToTalk application."""
        if not self.is_running:
            logger.warning("Application is not running")
            return

        logger.info("Stopping PushToTalk application...")

        self.is_running = False
        self.hotkey_service.stop_service()

        # Signal worker thread to stop
        self.command_queue.put("QUIT")
        if self.worker_thread:
            self.worker_thread.join(timeout=2.0)
            self.worker_thread = None

        # Wait for background processing threads to complete
        with self.processing_threads_lock:
            active_threads = [t for t in self.processing_threads if t.is_alive()]

        if active_threads:
            logger.info(
                f"Waiting for {len(active_threads)} background processing thread(s) to complete..."
            )
            for thread in active_threads:
                thread.join(timeout=5.0)
                if thread.is_alive():
                    logger.warning(
                        f"Background thread {thread.name} did not finish in time"
                    )

        # Clear processing threads list
        with self.processing_threads_lock:
            self.processing_threads.clear()

        self._close_streaming_session()
        self._stop_streaming_insert_worker()
        self._cancel_rest_auto_stop_timer()

        if self.audio_recorder:
            self.audio_recorder.shutdown()

        # No cleanup needed for audio feedback utility functions

        logger.info("PushToTalk application stopped")

    def run(self):
        """Run the application until stopped."""
        self.start()

        try:
            # Keep the main thread alive
            while self.is_running:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Received interrupt signal")
        finally:
            self.stop()

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        logger.info(f"Received signal {signum}, shutting down...")
        self.stop()
        sys.exit(0)

    def _worker_loop(self):
        """Worker loop to process commands from the queue."""
        logger.info("Worker thread started")
        while True:
            try:
                command = self.command_queue.get(timeout=0.5)
                if command == "QUIT":
                    break
                elif command == "START_RECORDING":
                    self._do_start_recording()
                elif command == "STOP_RECORDING":
                    self._do_stop_recording()
                elif command == "AUTO_STOP_RECORDING":
                    self._do_stop_recording(auto_stop=True)
                else:
                    logger.warning(f"Unknown command received: {command}")

                self.command_queue.task_done()
            except queue.Empty:
                if not self.is_running:
                    break
            except Exception as e:
                logger.error(f"Error in worker loop: {e}")

    def _on_start_recording(self):
        """Callback for when recording starts (called from hotkey thread)."""
        # Push command to queue to avoid blocking hotkey listener
        self.command_queue.put("START_RECORDING")

    def _on_stop_recording(self):
        """Callback for when recording stops (called from hotkey thread)."""
        # Push command to queue to avoid blocking hotkey listener
        self.command_queue.put("STOP_RECORDING")

    def _do_start_recording(self):
        """Internal method to perform start recording actions."""
        if getattr(self.audio_recorder, "is_recording", False):
            logger.debug("Ignoring start command; recording is already active")
            return

        # Play audio feedback if enabled
        if self.config.enable_audio_feedback:
            play_start_feedback()

        if self.config.is_parakeet_streaming_active():
            if not self._start_parakeet_streaming_recording():
                logger.error("Failed to start Parakeet streaming recording")
            return

        if self.audio_recorder.start_recording():
            self._start_rest_auto_stop_timer()
        else:
            logger.error("Failed to start audio recording")

    def _do_stop_recording(self, auto_stop: bool = False):
        """Internal method to perform stop recording actions."""
        if not getattr(self.audio_recorder, "is_recording", False):
            if not self.config.is_parakeet_streaming_active():
                self._cancel_rest_auto_stop_timer()
                logger.debug("Ignoring stop command; no REST recording is active")
            else:
                logger.debug(
                    "Ignoring stop command; no Parakeet streaming recording is active"
                )
            return

        # Play audio feedback immediately when hotkey is released
        if self.config.enable_audio_feedback:
            play_stop_feedback()

        if self.config.is_parakeet_streaming_active():
            self._stop_parakeet_streaming_recording()
            return

        self._cancel_rest_auto_stop_timer()
        if auto_stop:
            logger.info(
                "Auto-stopping Parakeet REST recording after "
                f"{self.config.parakeet_rest_auto_stop_seconds:.1f}s"
            )
            self._mark_hotkey_recording_stopped()

        # Stop recording and get audio file (fast operation)
        audio_file = self.audio_recorder.stop_recording()

        if not audio_file:
            logger.warning("No audio file to process")
            return

        # Spawn background thread for processing (don't block worker thread)
        processing_thread = threading.Thread(
            target=self._process_audio_background,
            args=(audio_file,),
            daemon=True,
            name=f"AudioProcessing-{len(self.processing_threads)}",
        )

        # Track the thread for graceful shutdown (before starting to avoid race condition)
        with self.processing_threads_lock:
            # Remove completed threads before adding new one to prevent memory leaks
            self.processing_threads = [
                t for t in self.processing_threads if t.is_alive()
            ]
            self.processing_threads.append(processing_thread)

        processing_thread.start()

        logger.info("Recording stopped, processing in background")

    def _start_rest_auto_stop_timer(self):
        """Start a timer that gracefully stops long Parakeet REST recordings."""
        self._cancel_rest_auto_stop_timer()
        if self.config.stt_provider != "parakeet" or self.config.parakeet_streaming_enabled:
            return

        timer = threading.Timer(
            self.config.parakeet_rest_auto_stop_seconds,
            self._on_rest_auto_stop_timer,
        )
        timer.daemon = True
        self.rest_auto_stop_timer = timer
        timer.start()
        logger.debug(
            "Parakeet REST auto-stop timer started for "
            f"{self.config.parakeet_rest_auto_stop_seconds:.1f}s"
        )

    def _cancel_rest_auto_stop_timer(self):
        """Cancel any pending Parakeet REST auto-stop timer."""
        timer = self.rest_auto_stop_timer
        self.rest_auto_stop_timer = None
        if timer:
            timer.cancel()

    def _on_rest_auto_stop_timer(self):
        """Queue a graceful stop when a Parakeet REST recording reaches its limit."""
        self.command_queue.put("AUTO_STOP_RECORDING")

    def _mark_hotkey_recording_stopped(self):
        """Keep hotkey state consistent after a timer-driven stop."""
        if hasattr(self.hotkey_service, "is_recording"):
            self.hotkey_service.is_recording = False
        if hasattr(self.hotkey_service, "is_toggle_mode"):
            self.hotkey_service.is_toggle_mode = False
        if hasattr(self.hotkey_service, "recording_state"):
            self.hotkey_service.recording_state = "idle"

    def _start_parakeet_streaming_recording(self) -> bool:
        """Start recording and stream live PCM frames to Parakeet."""
        try:
            self._validate_parakeet_streaming_audio_settings()
            self._ensure_streaming_insert_worker()
            self._wait_for_streaming_drain()
            self._wait_for_streaming_insert_queue()
            self.streaming_frame_buffer.clear()
            self._ensure_streaming_session()

            started = self.audio_recorder.start_recording(
                chunk_callback=self._on_streaming_audio_chunk,
                store_audio_data=self.config.debug_mode,
            )
            if not started:
                self._close_streaming_session()
                return False

            logger.info("Parakeet WebSocket streaming recording started")
            return True
        except Exception as e:
            logger.error(f"Failed to start Parakeet streaming recording: {e}")
            self._close_streaming_session()
            return False

    def _stop_parakeet_streaming_recording(self):
        """Stop live Parakeet streaming and skip REST transcription/refinement."""
        audio_file = self.audio_recorder.stop_recording()

        self._flush_streaming_frame_buffer()
        self._finish_streaming_recording()

        if self.config.debug_mode and audio_file:
            self._save_debug_audio(audio_file)
            try:
                if os.path.exists(audio_file):
                    os.unlink(audio_file)
            except Exception as cleanup_error:
                logger.warning(f"Error cleaning up streaming debug audio: {cleanup_error}")

        logger.info("Parakeet WebSocket streaming recording stopped")

    def _on_streaming_audio_chunk(self, chunk: bytes):
        """Buffer recorder chunks into 512-sample frames for upstream VAD."""
        if not self.streaming_session:
            return

        self.streaming_frame_buffer.extend(chunk)
        while len(self.streaming_frame_buffer) >= PARAKEET_STREAMING_FRAME_BYTES:
            frame = bytes(self.streaming_frame_buffer[:PARAKEET_STREAMING_FRAME_BYTES])
            del self.streaming_frame_buffer[:PARAKEET_STREAMING_FRAME_BYTES]
            self.streaming_session.send_audio(frame)

    def _flush_streaming_frame_buffer(self):
        """Send the final partial PCM frame padded to the expected frame size."""
        if not self.streaming_session or not self.streaming_frame_buffer:
            self.streaming_frame_buffer.clear()
            return

        padding = PARAKEET_STREAMING_FRAME_BYTES - len(self.streaming_frame_buffer)
        frame = bytes(self.streaming_frame_buffer) + (b"\x00" * padding)
        self.streaming_frame_buffer.clear()
        self.streaming_session.send_audio(frame)

    def _ensure_streaming_session(self):
        """Create or restart the long-lived Parakeet streaming session."""
        ws_url = build_parakeet_ws_url(
            self.config.parakeet_endpoint,
            vad_end_silence_ms=self.config.parakeet_streaming_vad_end_silence_ms,
            vad_max_chunk_seconds=self.config.parakeet_streaming_max_chunk_seconds,
            transcription_batch_size=self.config.parakeet_streaming_batch_size,
            transcription_batch_window_ms=self.config.parakeet_streaming_batch_window_ms,
        )
        if (
            self.streaming_session
            and self.streaming_session.ws_url == ws_url
            and not self.streaming_session.error
            and self.streaming_session.is_active
        ):
            self.streaming_session.start()
            return

        self._close_streaming_session()
        self.streaming_session = ParakeetStreamingSession(
            self.config.parakeet_endpoint,
            on_text=self._enqueue_streaming_text,
            vad_end_silence_ms=self.config.parakeet_streaming_vad_end_silence_ms,
            vad_max_chunk_seconds=self.config.parakeet_streaming_max_chunk_seconds,
            transcription_batch_size=self.config.parakeet_streaming_batch_size,
            transcription_batch_window_ms=self.config.parakeet_streaming_batch_window_ms,
        )
        self.streaming_session.start()

    def _finish_streaming_recording(self):
        """Flush the current recording without closing the long-lived socket."""
        session = self.streaming_session
        if not session:
            return

        if self.streaming_drain_thread and self.streaming_drain_thread.is_alive():
            logger.debug("Parakeet streaming drain already in progress")
            return

        self.streaming_drain_thread = threading.Thread(
            target=session.finish_recording,
            daemon=True,
            name="ParakeetStreamingDrain",
        )
        self.streaming_drain_thread.start()

    def _wait_for_streaming_drain(self):
        """Wait for the previous recording's final-text drain before reusing the socket."""
        thread = self.streaming_drain_thread
        if not thread:
            return

        if thread is threading.current_thread():
            return

        if thread.is_alive():
            session = self.streaming_session
            timeout = (session.drain_timeout + 0.5) if session else 1.0
            thread.join(timeout=timeout)
            if thread.is_alive():
                logger.warning(
                    "Timed out waiting for Parakeet streaming drain before continuing"
                )
                return

        self.streaming_drain_thread = None

    def _wait_for_streaming_insert_queue(self, timeout: float = 2.0):
        """Give prior streaming text inserts a bounded chance to finish."""
        deadline = time.monotonic() + timeout
        while getattr(self.streaming_insert_queue, "unfinished_tasks", 0):
            if time.monotonic() >= deadline:
                logger.warning(
                    "Timed out waiting for queued Parakeet streaming text insertion"
                )
                return
            time.sleep(0.01)

    def _close_streaming_session(self):
        """Close the active streaming session if one exists."""
        session = self.streaming_session
        if session:
            if getattr(self.audio_recorder, "is_recording", False):
                self.audio_recorder.stop_recording()
                self._flush_streaming_frame_buffer()
                self._finish_streaming_recording()
            self._wait_for_streaming_drain()
        self.streaming_session = None
        if session:
            session.stop()

    def _ensure_streaming_insert_worker(self):
        """Start the single-consumer insert worker for streaming text segments."""
        if self.streaming_insert_thread and self.streaming_insert_thread.is_alive():
            return

        self.streaming_insert_thread = threading.Thread(
            target=self._streaming_insert_loop,
            daemon=True,
            name="StreamingTextInsert",
        )
        self.streaming_insert_thread.start()

    def _stop_streaming_insert_worker(self):
        """Stop the streaming insert worker during app shutdown."""
        if self.streaming_insert_thread and self.streaming_insert_thread.is_alive():
            self.streaming_insert_queue.put(None)
            self.streaming_insert_thread.join(timeout=2.0)
        self.streaming_insert_thread = None

    def _enqueue_streaming_text(self, text: str):
        """Queue a finalized streaming segment for ordered insertion."""
        if text.strip():
            self.streaming_insert_queue.put(text)

    def _format_streaming_insert_segment(self, text: str) -> str:
        """Add a stable separator before streamed segments after the first one."""
        separator, segment, _ = format_streaming_insert_segment(
            text, self.streaming_insert_last_char
        )
        return f"{separator}{segment}"

    def _streaming_insert_loop(self):
        """Insert streaming text segments serially to avoid clipboard races."""
        while True:
            text = self.streaming_insert_queue.get()
            if text is None:
                self.streaming_insert_queue.task_done()
                break

            try:
                with self.streaming_insert_lock:
                    previous_last_char = self.streaming_insert_last_char
                    separator, segment, next_last_char = format_streaming_insert_segment(
                        text, previous_last_char
                    )
                    if not segment:
                        success = True
                        continue
                    logger.debug(
                        "Streaming insert segment: "
                        f"raw={text!r}, separator={separator!r}, "
                        f"segment={segment!r}, "
                        f"previous_last_char={previous_last_char!r}, "
                        f"next_last_char={next_last_char!r}"
                    )
                    success = True
                    if separator:
                        if self.config.streaming_boundary_space_keypress:
                            success = self.text_inserter.insert_space()
                        else:
                            segment = f"{separator}{segment}"
                    if success:
                        success = self.text_inserter.insert_text(segment)
                    if success:
                        self.streaming_insert_last_char = next_last_char
                if success:
                    logger.info("Streaming text insertion successful")
                else:
                    logger.error("Streaming text insertion failed")
            except TextInsertionError as e:
                logger.error(f"Streaming text insertion failed: {e}")
            finally:
                self.streaming_insert_queue.task_done()

    def _process_audio_background(self, audio_file: str):
        """Process audio in background thread (transcribe, refine, insert).

        This method runs in a separate daemon thread for each recording,
        allowing new recordings to start immediately without waiting for
        transcription/refinement to complete.

        Args:
            audio_file: Path to the recorded audio file
        """
        try:
            logger.info(f"Processing audio file: {audio_file}")

            # Save audio file in debug mode before processing
            if self.config.debug_mode:
                self._save_debug_audio(audio_file)

            # Get active window info for logging
            window_title = self.text_inserter.get_active_window_title()
            if window_title:
                logger.info(f"Target window: {window_title}")

            # Transcribe audio (1-3 seconds, runs in background)
            logger.info("Transcribing audio...")
            try:
                transcribed_text = self.transcriber.transcribe_audio(audio_file)
                logger.info(f"Transcribed text: {transcribed_text}")
            except (TranscriptionError, APIError) as e:
                logger.error(f"Transcription failed: {e}")
                transcribed_text = None

            # Clean up temporary audio file
            try:
                if os.path.exists(audio_file):
                    os.unlink(audio_file)
                    logger.debug(f"Cleaned up audio file: {audio_file}")
            except Exception as cleanup_error:
                logger.warning(f"Error cleaning up audio file: {cleanup_error}")

            if transcribed_text is None:
                logger.warning("Transcribed text is None, skipping refinement")
                return

            # Refine text if enabled (1-2 seconds, runs in background)
            final_text = transcribed_text
            if self.text_refiner and self.config.is_text_refinement_effective():
                logger.info("Refining transcribed text...")
                try:
                    refined_text = self.text_refiner.refine_text(transcribed_text)
                    if refined_text:
                        final_text = refined_text
                        logger.info(f"Refined: {final_text}")
                except (TextRefinementError, APIError) as e:
                    logger.error(
                        f"Text refinement failed, using original transcription: {e}"
                    )
                    final_text = transcribed_text

            # Insert text into active window
            logger.info("Inserting text into active window...")
            try:
                final_text = normalize_sentence_spacing(final_text)
                success = self.text_inserter.insert_text(final_text)
                if success:
                    logger.info("Text insertion successful")
                else:
                    logger.error("Text insertion failed")
            except TextInsertionError as e:
                logger.error(f"Text insertion failed: {e}")

        except Exception as e:
            logger.error(f"Error processing audio in background: {e}")
            # Clean up temporary audio file even on error
            try:
                if os.path.exists(audio_file):
                    os.unlink(audio_file)
                    logger.debug(f"Cleaned up audio file on error: {audio_file}")
            except Exception as cleanup_error:
                logger.error(
                    f"Error cleaning up audio file {audio_file}: {cleanup_error}"
                )

    def _save_debug_audio(self, audio_file: str):
        """
        Save recorded audio file to debug directory when debug mode is enabled.

        Args:
            audio_file: Path to the recorded audio file
        """
        try:
            import shutil
            from datetime import datetime

            # Create debug directory with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[
                :-3
            ]  # Remove last 3 digits of microseconds
            debug_dir = f"debug_audio_{timestamp}"
            os.makedirs(debug_dir, exist_ok=True)

            # Copy audio file to debug directory
            debug_audio_path = os.path.join(debug_dir, "recorded_audio.wav")
            shutil.copy2(audio_file, debug_audio_path)

            logger.info(f"Debug: Saved recorded audio to {debug_audio_path}")

            # Create info file with recording details
            info_path = os.path.join(debug_dir, "recording_info.txt")
            with open(info_path, "w") as f:
                f.write("Audio Recording Debug Information\n")
                f.write(f"Timestamp: {timestamp}\n")
                f.write("Settings:\n")
                f.write(f"  Sample Rate: {self.config.sample_rate} Hz\n")
                f.write(f"  Channels: {self.config.channels}\n")
                f.write(f"  Chunk Size: {self.config.chunk_size}\n")
                f.write("Configuration:\n")
                f.write(f"  STT Model: {self.config.stt_model}\n")
                f.write(
                    f"  Text Refinement: {'Enabled' if self.config.enable_text_refinement else 'Disabled'}\n"
                )
                if self.config.enable_text_refinement:
                    f.write(f"  Refinement Model: {self.config.refinement_model}\n")

            logger.info(f"Debug: Saved recording info to {info_path}")
            logger.info(f"Debug files saved to directory: {debug_dir}")

        except Exception as e:
            logger.error(f"Failed to save debug audio: {e}")

    def change_hotkey(self, new_hotkey: str) -> bool:
        """
        Change the push-to-talk hotkey combination.

        Args:
            new_hotkey: New push-to-talk hotkey combination

        Returns:
            True if hotkey was changed successfully
        """
        logger.info(
            f"Changing push-to-talk hotkey from '{self.config.hotkey}' to '{new_hotkey}'"
        )
        self.config.hotkey = new_hotkey

        # Reinitialize hotkey service with new hotkey
        if self.hotkey_service:
            self.hotkey_service.stop()

        self.hotkey_service = HotkeyService(
            hotkey=self.config.hotkey, toggle_hotkey=self.config.toggle_hotkey
        )
        self.hotkey_service.set_callbacks(
            on_start_recording=self._on_start_recording,
            on_stop_recording=self._on_stop_recording,
        )
        return True

    def change_toggle_hotkey(self, new_toggle_hotkey: str) -> bool:
        """
        Change the toggle hotkey combination.

        Args:
            new_toggle_hotkey: New toggle hotkey combination

        Returns:
            True if toggle hotkey was changed successfully
        """
        logger.info(
            f"Changing toggle hotkey from '{self.config.toggle_hotkey}' to '{new_toggle_hotkey}'"
        )
        self.config.toggle_hotkey = new_toggle_hotkey

        # Reinitialize hotkey service with new toggle hotkey
        if self.hotkey_service:
            self.hotkey_service.stop()

        self.hotkey_service = HotkeyService(
            hotkey=self.config.hotkey, toggle_hotkey=self.config.toggle_hotkey
        )
        self.hotkey_service.set_callbacks(
            on_start_recording=self._on_start_recording,
            on_stop_recording=self._on_stop_recording,
        )
        return True

    def toggle_text_refinement(self) -> bool:
        """
        Toggle text refinement on/off.

        Returns:
            New state of text refinement (True if enabled)
        """
        old_value = self.config.enable_text_refinement
        self.config.enable_text_refinement = not self.config.enable_text_refinement

        # Reinitialize text refiner if needed
        if old_value != self.config.enable_text_refinement:
            if self.config.is_text_refinement_effective():
                # Get the appropriate API key based on provider
                if self.config.refinement_provider == "openai":
                    api_key = self.config.openai_api_key or os.getenv("OPENAI_API_KEY")
                elif self.config.refinement_provider == "cerebras":
                    api_key = self.config.cerebras_api_key or os.getenv(
                        "CEREBRAS_API_KEY"
                    )
                elif self.config.refinement_provider == "gemini":
                    api_key = self.config.gemini_api_key or os.getenv("GOOGLE_API_KEY")
                elif self.config.refinement_provider == "custom":
                    if not self.config.get_custom_refinement_endpoint():
                        raise ConfigurationError(
                            "Custom refinement provider requires a custom refinement endpoint URL."
                        )
                    api_key = self.config.custom_api_key or "local"
                else:
                    raise ConfigurationError(
                        f"Unknown refinement provider: {self.config.refinement_provider}"
                    )

                self.text_refiner = TextRefinerFactory.create_refiner(
                    provider=self.config.refinement_provider,
                    api_key=api_key,
                    model=self.config.refinement_model,
                    glossary=self.config.custom_glossary,
                    base_url=self.config.get_custom_refinement_endpoint() or None,
                )
            else:
                self.text_refiner = None

            # Set glossary for transcriber if enabled
            if self.transcriber:
                self.transcriber.set_glossary(self.config.custom_glossary)

        logger.info(
            f"Text refinement {'enabled' if self.config.is_text_refinement_effective() else 'disabled'}"
        )
        return self.config.enable_text_refinement

    def toggle_audio_feedback(self) -> bool:
        """
        Toggle audio feedback on/off.

        Returns:
            New state of audio feedback (True if enabled)
        """
        self.config.enable_audio_feedback = not self.config.enable_audio_feedback

        # Audio feedback is now handled via utility functions - no service to manage

        logger.info(
            f"Audio feedback {'enabled' if self.config.enable_audio_feedback else 'disabled'}"
        )
        return self.config.enable_audio_feedback

    def get_status(self) -> Dict[str, Any]:
        """
        Get current application status.

        Returns:
            Dictionary containing status information
        """
        recording_mode = "idle"
        if hasattr(self.hotkey_service, "recording_state"):
            if self.hotkey_service.recording_state == "push_to_talk":
                recording_mode = "push-to-talk"
            elif self.hotkey_service.recording_state == "toggle":
                recording_mode = "toggle"

        return {
            "is_running": self.is_running,
            "hotkey": self.config.hotkey,
            "toggle_hotkey": self.config.toggle_hotkey,
            "recording_mode": recording_mode,
            "audio_feedback_enabled": self.config.enable_audio_feedback,
            "text_refinement_enabled": self.config.is_text_refinement_effective(),
            "parakeet_streaming_enabled": self.config.is_parakeet_streaming_active(),
            "logging_enabled": self.config.enable_logging,
        }
