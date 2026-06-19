"""Background audio transcription, refinement, insertion, and debug export."""

import os
import shutil
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING

from loguru import logger

from src import platform_support
from src.exceptions import (
    APIError,
    TextInsertionError,
    TextRefinementError,
    TranscriptionError,
)
from src.text_formatting import normalize_sentence_spacing
from src.text_inserter import TextInserter
from src.text_refiner_base import TextRefinerBase
from src.transcription_base import TranscriberBase

if TYPE_CHECKING:
    from src.push_to_talk import PushToTalkConfig


class AudioProcessingService:
    """Process completed recordings in a background thread."""

    def __init__(
        self,
        get_config: Callable[[], "PushToTalkConfig"],
        get_transcriber: Callable[[], TranscriberBase],
        get_text_refiner: Callable[[], TextRefinerBase | None],
        get_text_inserter: Callable[[], TextInserter],
    ):
        self.get_config = get_config
        self.get_transcriber = get_transcriber
        self.get_text_refiner = get_text_refiner
        self.get_text_inserter = get_text_inserter

    def process_audio(self, audio_file: str) -> None:
        """Transcribe, optionally refine, and insert a completed recording."""
        config = self.get_config()
        try:
            logger.info(f"Processing audio file: {audio_file}")

            if config.debug_mode:
                self.save_debug_audio(audio_file)

            text_inserter = self.get_text_inserter()
            window_title = text_inserter.get_active_window_title()
            if window_title:
                logger.info(f"Target window: {window_title}")

            logger.info("Transcribing audio...")
            try:
                transcribed_text = self.get_transcriber().transcribe_audio(audio_file)
                logger.info(f"Transcribed text: {transcribed_text}")
            except (TranscriptionError, APIError) as e:
                logger.error(f"Transcription failed: {e}")
                transcribed_text = None

            self._cleanup_audio_file(audio_file)

            if transcribed_text is None:
                logger.warning("Transcribed text is None, skipping refinement")
                return

            final_text = transcribed_text
            text_refiner = self.get_text_refiner()
            config = self.get_config()
            if text_refiner and config.is_text_refinement_effective():
                logger.info("Refining transcribed text...")
                try:
                    refined_text = text_refiner.refine_text(transcribed_text)
                    if refined_text:
                        final_text = refined_text
                        logger.info(f"Refined: {final_text}")
                except (TextRefinementError, APIError) as e:
                    logger.error(
                        f"Text refinement failed, using original transcription: {e}"
                    )
                    final_text = transcribed_text

            handoff = platform_support.use_clipboard_handoff()
            logger.info(
                "Copying text to clipboard for manual paste..."
                if handoff
                else "Inserting text into active window..."
            )
            try:
                final_text = normalize_sentence_spacing(final_text)
                success = text_inserter.insert_text(final_text)
                if success:
                    if handoff:
                        logger.info(
                            "Transcript ready on clipboard; press Ctrl+V to paste"
                        )
                        if config.enable_audio_feedback:
                            from src.utils import play_clipboard_ready_feedback

                            play_clipboard_ready_feedback()
                    else:
                        logger.info("Text insertion successful")
                else:
                    logger.error("Text insertion failed")
            except TextInsertionError as e:
                logger.error(f"Text insertion failed: {e}")

        except Exception as e:
            logger.error(f"Error processing audio in background: {e}")
            self._cleanup_audio_file(audio_file, on_error=True)

    def save_debug_audio(self, audio_file: str) -> None:
        """Save recorded audio and metadata when debug mode is enabled."""
        try:
            config = self.get_config()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            debug_dir = f"debug_audio_{timestamp}"
            os.makedirs(debug_dir, exist_ok=True)

            debug_audio_path = os.path.join(debug_dir, "recorded_audio.wav")
            shutil.copy2(audio_file, debug_audio_path)

            logger.info(f"Debug: Saved recorded audio to {debug_audio_path}")

            info_path = os.path.join(debug_dir, "recording_info.txt")
            with open(info_path, "w") as f:
                f.write("Audio Recording Debug Information\n")
                f.write(f"Timestamp: {timestamp}\n")
                f.write("Settings:\n")
                f.write(f"  Sample Rate: {config.sample_rate} Hz\n")
                f.write(f"  Channels: {config.channels}\n")
                f.write(f"  Chunk Size: {config.chunk_size}\n")
                f.write("Configuration:\n")
                f.write(f"  STT Model: {config.stt_model}\n")
                f.write(
                    "  Text Refinement: "
                    f"{'Enabled' if config.enable_text_refinement else 'Disabled'}\n"
                )
                if config.enable_text_refinement:
                    f.write(f"  Refinement Model: {config.refinement_model}\n")

            logger.info(f"Debug: Saved recording info to {info_path}")
            logger.info(f"Debug files saved to directory: {debug_dir}")

        except Exception as e:
            logger.error(f"Failed to save debug audio: {e}")

    @staticmethod
    def _cleanup_audio_file(audio_file: str, on_error: bool = False) -> None:
        try:
            if os.path.exists(audio_file):
                os.unlink(audio_file)
                suffix = " on error" if on_error else ""
                logger.debug(f"Cleaned up audio file{suffix}: {audio_file}")
        except Exception as cleanup_error:
            if on_error:
                logger.error(
                    f"Error cleaning up audio file {audio_file}: {cleanup_error}"
                )
            else:
                logger.warning(f"Error cleaning up audio file: {cleanup_error}")
