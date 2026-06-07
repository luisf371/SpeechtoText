from typing import List, Optional
from src.transcription_base import TranscriberBase
from src.transcription_openai import OpenAITranscriber
from src.transcription_deepgram import DeepgramTranscriber
from src.transcription_parakeet import ParakeetTranscriber
from src.provider_registry import stt_provider_names


class TranscriberFactory:
    """Factory for creating transcriber instances based on provider."""

    @staticmethod
    def create_transcriber(
        provider: str,
        api_key: str,
        model: str,
        glossary: Optional[List[str]] = None,
        base_url: Optional[str] = None,
    ) -> TranscriberBase:
        """
        Create and return a transcriber instance.

        Args:
            provider: The transcription provider ("openai", "deepgram", "parakeet", or "custom")
            api_key: API key for the selected provider
            model: Model name to use for transcription
            glossary: Optional list of custom terms for improved recognition
            base_url: Optional custom API endpoint URL (for OpenAI-compatible APIs)

        Returns:
            TranscriberBase instance for the selected provider

        Raises:
            ValueError: If an unknown provider is specified
        """
        if provider == "openai":
            transcriber = OpenAITranscriber(api_key=api_key, model=model)
        elif provider == "deepgram":
            transcriber = DeepgramTranscriber(api_key=api_key, model=model)
        elif provider == "parakeet":
            transcriber = ParakeetTranscriber(api_key=api_key, endpoint=base_url or "")
        elif provider == "custom":
            transcriber = OpenAITranscriber(
                api_key=api_key,
                model=model,
                base_url=base_url,
            )
        else:
            supported = ", ".join(stt_provider_names())
            raise ValueError(
                f"Unknown transcription provider: {provider}. "
                f"Supported providers: {supported}"
            )

        # Set glossary if provided
        if glossary:
            transcriber.set_glossary(glossary)

        return transcriber
