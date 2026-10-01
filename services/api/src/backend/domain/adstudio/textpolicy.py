"""Brand-safety blocklist for `required_text` (ai-design §9.7 RT-09, 422 `text-policy`).

Whole-word match after NFKC, casefold and undoing common character swaps, so "Scunthorpe" and
"cocktail" pass. The list lives in `data/text_blocklist.txt` (human-maintained). The matched term
is never echoed back or logged.
"""

import unicodedata
from functools import lru_cache
from pathlib import Path

import regex

BLOCKLIST = Path(__file__).resolve().parent / "data" / "text_blocklist.txt"
_SWAPS = str.maketrans(
    {"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"}
)
_NON_LETTER = regex.compile(r"[^\p{L}\p{M}]+")


def _fold(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).casefold().translate(_SWAPS)
    return " " + _NON_LETTER.sub(" ", folded).strip() + " "


@lru_cache
def blocked_terms() -> tuple[str, ...]:
    terms: list[str] = []
    for line in BLOCKLIST.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            terms.append(_fold(line))
    return tuple(terms)


def violates_text_policy(text: str) -> bool:
    folded = _fold(text)
    return any(term in folded for term in blocked_terms())
