"""Text normalization helpers used before insertion."""

import re


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
