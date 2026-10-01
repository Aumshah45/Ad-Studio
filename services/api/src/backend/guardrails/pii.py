"""Regex PII redaction (emails, phones, card-like numbers, PAN/Aadhaar-like IDs)."""

import re

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("card", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    ("aadhaar", re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}\b")),
    ("pan", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("phone", re.compile(r"(?<![\w-])\+?\d[\d\s().-]{8,}\d\b")),
]


def redact(text: str) -> tuple[str, list[str]]:
    """Return the text with PII replaced by `[REDACTED:<kind>]` and the kinds found."""
    found: list[str] = []
    for kind, pattern in _PATTERNS:
        text, count = pattern.subn(f"[REDACTED:{kind}]", text)
        if count:
            found.append(kind)
    return text, found
