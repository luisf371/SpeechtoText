"""Ordered text insertion worker for finalized streaming transcription chunks."""

import queue
import threading
import time
from collections.abc import Callable

from loguru import logger

from src.exceptions import TextInsertionError
from src.text_formatting import format_streaming_insert_segment
from src.text_inserter import TextInserter


class StreamingTextInserter:
    """Serialize live transcription text insertion to avoid clipboard races."""

    def __init__(
        self,
        text_inserter: TextInserter,
        use_boundary_space_keypress: Callable[[], bool],
    ):
        self.text_inserter = text_inserter
        self.use_boundary_space_keypress = use_boundary_space_keypress
        self.queue: queue.Queue[str | None] = queue.Queue()
        self.thread: threading.Thread | None = None
        self.lock = threading.Lock()
        self.last_char: str | None = None

    def update_text_inserter(self, text_inserter: TextInserter) -> None:
        """Update the insertion backend after component reinitialization."""
        self.text_inserter = text_inserter

    def start(self) -> None:
        """Start the single-consumer insert worker if needed."""
        if self.thread and self.thread.is_alive():
            return

        self.thread = threading.Thread(
            target=self.run,
            daemon=True,
            name="StreamingTextInsert",
        )
        self.thread.start()

    def stop(self) -> None:
        """Stop the insert worker during app shutdown."""
        if self.thread and self.thread.is_alive():
            self.queue.put(None)
            self.thread.join(timeout=2.0)
        self.thread = None

    def enqueue(self, text: str) -> None:
        """Queue a finalized streaming segment for ordered insertion."""
        if text.strip():
            self.queue.put(text)

    def wait_until_idle(self, timeout: float = 2.0) -> None:
        """Give queued streaming text a bounded chance to finish inserting."""
        deadline = time.monotonic() + timeout
        while getattr(self.queue, "unfinished_tasks", 0):
            if time.monotonic() >= deadline:
                logger.warning(
                    "Timed out waiting for queued Parakeet streaming text insertion"
                )
                return
            time.sleep(0.01)

    def format_segment(self, text: str) -> str:
        """Format text using the current insertion boundary state."""
        separator, segment, _ = format_streaming_insert_segment(text, self.last_char)
        return f"{separator}{segment}"

    def run(self) -> None:
        """Insert streaming text segments serially."""
        while True:
            text = self.queue.get()
            try:
                if text is None:
                    break
                self._insert_text(text)
            finally:
                self.queue.task_done()

    def _insert_text(self, text: str) -> None:
        success = False
        try:
            with self.lock:
                previous_last_char = self.last_char
                separator, segment, next_last_char = format_streaming_insert_segment(
                    text, previous_last_char
                )
                if not segment:
                    return
                logger.debug(
                    "Streaming insert segment: "
                    f"raw={text!r}, separator={separator!r}, "
                    f"segment={segment!r}, "
                    f"previous_last_char={previous_last_char!r}, "
                    f"next_last_char={next_last_char!r}"
                )
                success = True
                if separator:
                    if self.use_boundary_space_keypress():
                        if hasattr(self.text_inserter, "insert_space_then_text"):
                            success = self.text_inserter.insert_space_then_text(segment)
                            segment = ""
                        else:
                            success = self.text_inserter.insert_space()
                    else:
                        segment = f"{separator}{segment}"
                if success and segment:
                    success = self.text_inserter.insert_text(segment)
                if success:
                    self.last_char = next_last_char
            if success:
                logger.info("Streaming text insertion successful")
            else:
                logger.error("Streaming text insertion failed")
        except TextInsertionError as e:
            logger.error(f"Streaming text insertion failed: {e}")
