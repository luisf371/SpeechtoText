import json
import mimetypes
import os
import time
import uuid
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from loguru import logger

from src.exceptions import APIError, TranscriptionError
from src.transcription_base import TranscriberBase
from src.utils import validate_audio_duration, validate_audio_file_exists


class ParakeetTranscriber(TranscriberBase):
    def __init__(
        self,
        api_key: Optional[str] = None,
        endpoint: str = "http://localhost:8000",
        timeout: float = 120.0,
    ):
        """
        Initialize the transcriber for Shadowfita's Parakeet FastAPI service.

        Args:
            api_key: Optional API key placeholder. The local Docker service does not
                require one, but the base class expects a value.
            endpoint: Base URL or transcribe URL for the Parakeet service.
            timeout: HTTP timeout in seconds.
        """
        super().__init__(api_key or "local", "Parakeet")
        self.endpoint = self._build_transcribe_url(endpoint)
        self.timeout = timeout
        logger.info(f"Using Parakeet transcription endpoint: {self.endpoint}")

    @staticmethod
    def _build_transcribe_url(endpoint: str) -> str:
        """Normalize user-entered service URLs to the native /transcribe route."""
        normalized = (endpoint or "").strip().rstrip("/")
        if not normalized:
            normalized = "http://localhost:8000"

        for suffix in ("/v1/audio/transcriptions", "/audio/transcriptions"):
            if normalized.endswith(suffix):
                normalized = normalized[: -len(suffix)]
                break

        if normalized.endswith("/v1"):
            normalized = normalized[:-3]
        if normalized.endswith("/transcribe"):
            return normalized
        return f"{normalized}/transcribe"

    def transcribe_audio(
        self, audio_file_path: str, language: Optional[str] = None
    ) -> Optional[str]:
        """
        Transcribe audio file to text using the Parakeet FastAPI native endpoint.

        Args:
            audio_file_path: Path to the audio file
            language: Ignored. The upstream Parakeet v2 service is English-only.

        Returns:
            Transcribed text or None if transcription failed
        """
        if not validate_audio_file_exists(audio_file_path):
            return None

        if not validate_audio_duration(audio_file_path):
            return None

        if language:
            logger.debug("Parakeet STT ignores language hints")
        if self.glossary:
            logger.debug("Parakeet STT ignores glossary terms")

        try:
            start_time = time.time()
            logger.debug(f"Starting Parakeet transcription for: {audio_file_path}")

            audio_path = Path(audio_file_path)
            body, content_type = self._build_multipart_body(audio_path)
            request = Request(
                self.endpoint,
                data=body,
                headers={
                    "Content-Type": content_type,
                    "Accept": "application/json",
                },
                method="POST",
            )

            with urlopen(request, timeout=self.timeout) as response:
                response_body = response.read().decode("utf-8")

            payload = json.loads(response_body)
            transcribed_text = str(payload.get("text", "")).strip()
            transcription_time = time.time() - start_time

            logger.info(
                f"Parakeet transcription successful: {len(transcribed_text)} characters in {transcription_time:.2f}s"
            )
            return transcribed_text if transcribed_text else None

        except HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            logger.error(f"Parakeet API error during transcription: {e}: {error_body}")
            raise APIError(
                f"Parakeet transcription API failed: HTTP {e.code} {e.reason}",
                provider="Parakeet",
                status_code=e.code,
            ) from e
        except URLError as e:
            logger.error(f"Parakeet connection failed: {e}")
            raise TranscriptionError(f"Failed to connect to Parakeet: {e}") from e
        except Exception as e:
            logger.error(f"Parakeet transcription failed: {e}")
            raise TranscriptionError(f"Failed to transcribe audio with Parakeet: {e}") from e

    @staticmethod
    def _build_multipart_body(audio_path: Path) -> tuple[bytes, str]:
        """Build multipart/form-data expected by the Parakeet /transcribe route."""
        boundary = f"----PushToTalkParakeet{uuid.uuid4().hex}"
        content_type = mimetypes.guess_type(audio_path.name)[0] or "audio/wav"

        fields = [
            (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="include_timestamps"\r\n\r\n'
                "false\r\n"
            ).encode("utf-8"),
            (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="should_chunk"\r\n\r\n'
                "false\r\n"
            ).encode("utf-8"),
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="{os.path.basename(audio_path)}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode("utf-8"),
            audio_path.read_bytes(),
            f"\r\n--{boundary}--\r\n".encode("utf-8"),
        ]

        return b"".join(fields), f"multipart/form-data; boundary={boundary}"
