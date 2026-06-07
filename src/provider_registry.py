"""Provider and model metadata shared across the app."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderSpec:
    """Static metadata for a transcription or refinement provider."""

    name: str
    label: str
    default_model: str
    models: tuple[str, ...]


STT_PROVIDERS: dict[str, ProviderSpec] = {
    "openai": ProviderSpec(
        name="openai",
        label="OpenAI",
        default_model="gpt-4o-mini-transcribe",
        models=("whisper-1", "gpt-4o-transcribe", "gpt-4o-mini-transcribe"),
    ),
    "deepgram": ProviderSpec(
        name="deepgram",
        label="Deepgram",
        default_model="nova-3",
        models=("nova-3", "nova-2", "base", "enhanced", "whisper-medium"),
    ),
    "parakeet": ProviderSpec(
        name="parakeet",
        label="Parakeet",
        default_model="parakeet-tdt-0.6b-v2",
        models=("parakeet-tdt-0.6b-v2",),
    ),
    "custom": ProviderSpec(
        name="custom",
        label="Custom",
        default_model="whisper-1",
        models=("whisper-1", "whisper-large-v3", "Systran/faster-whisper-large-v3"),
    ),
}

REFINEMENT_PROVIDERS: dict[str, ProviderSpec] = {
    "openai": ProviderSpec(
        name="openai",
        label="OpenAI",
        default_model="gpt-4.1-nano",
        models=(
            "gpt-5",
            "gpt-5-mini",
            "gpt-5-nano",
            "gpt-4.1",
            "gpt-4.1-mini",
            "gpt-4.1-nano",
            "gpt-4o-mini",
            "gpt-4o",
        ),
    ),
    "cerebras": ProviderSpec(
        name="cerebras",
        label="Cerebras",
        default_model="llama-3.3-70b",
        models=(
            "llama-3.3-70b",
            "qwen-3-235b-a22b-instruct-2507",
            "qwen-3-32b",
            "llama3.1-8b",
            "gpt-oss-120b",
        ),
    ),
    "gemini": ProviderSpec(
        name="gemini",
        label="Gemini",
        default_model="gemini-3-flash-preview",
        models=(
            "gemini-3-flash-preview",
            "gemini-3-pro-preview",
            "gemini-2.5-flash-preview-05-20",
            "gemini-2.5-pro-preview-06-05",
        ),
    ),
    "custom": ProviderSpec(
        name="custom",
        label="Custom",
        default_model="llama3",
        models=("llama3", "mistral", "mixtral", "gemma"),
    ),
}


def stt_provider_names() -> list[str]:
    """Return supported STT provider names in display order."""
    return list(STT_PROVIDERS)


def refinement_provider_names() -> list[str]:
    """Return supported refinement provider names in display order."""
    return list(REFINEMENT_PROVIDERS)


def stt_models(provider: str) -> list[str]:
    """Return STT models for provider in display order."""
    spec = STT_PROVIDERS.get(provider)
    return list(spec.models) if spec else []


def refinement_models(provider: str) -> list[str]:
    """Return refinement models for provider in display order."""
    spec = REFINEMENT_PROVIDERS.get(provider)
    return list(spec.models) if spec else []


def default_stt_model(provider: str) -> str:
    """Return default STT model for provider."""
    spec = STT_PROVIDERS.get(provider)
    return spec.default_model if spec else ""


def default_refinement_model(provider: str) -> str:
    """Return default refinement model for provider."""
    spec = REFINEMENT_PROVIDERS.get(provider)
    return spec.default_model if spec else ""
