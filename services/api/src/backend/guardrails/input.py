"""Input checks for user-controlled text before it reaches a model."""

import re

from backend.core.errors import AppError

_OVERRIDE_PHRASES = re.compile(
    r"(ignore|disregard|forget)\s+(all\s+|any\s+)?(the\s+)?(previous|prior|above|earlier)\s+"
    r"(instructions|prompts?|rules|messages)"
    r"|(ignore|disregard|forget)\s+(all|any|every)\s+(the\s+|your\s+)?"
    r"(rules|instructions|guidelines|checks)"
    r"|\b(approve|pass)\s+(this|the)\s+(ad|image)\b"
    r"|mark\s+(all|every)\s+(the\s+)?checks?\s+(as\s+)?pass"
    r"|you\s+are\s+now\s+"
    r"|new\s+instructions\s*:"
    r"|reveal\s+(your|the)\s+(system\s+)?prompt"
    r"|act\s+as\s+(an?\s+)?(unrestricted|jailbroken|dan)\b",
    re.IGNORECASE,
)
_FAKE_MARKERS = re.compile(
    r"<\s*/?\s*(system|assistant|tool|untrusted[^>]*)\s*>"
    r"|\[/?(system|inst)\]"
    r"|<\|(im_start|im_end|system|endoftext)\|>"
    r"|^\s*(system|assistant)\s*:",
    re.IGNORECASE | re.MULTILINE,
)
_TAG_CHARS = re.compile("[\U000e0000-\U000e007f]")


def injection_signals(text: str) -> list[str]:
    signals: list[str] = []
    if _OVERRIDE_PHRASES.search(text):
        signals.append("role_override")
    if _FAKE_MARKERS.search(text):
        signals.append("fake_marker")
    if _TAG_CHARS.search(text):
        signals.append("unicode_tags")
    return signals


def check_input(text: str, max_chars: int) -> str:
    """Validate size and emptiness; returns the stripped text."""
    stripped = text.strip()
    if not stripped:
        raise AppError(422, "empty-input", "Empty input", "Input text must not be empty.")
    if len(stripped) > max_chars:
        raise AppError(
            413,
            "input-too-long",
            "Input too long",
            f"Input has {len(stripped)} characters; the limit is {max_chars}.",
        )
    return stripped
