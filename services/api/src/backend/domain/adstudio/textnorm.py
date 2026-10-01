"""Text normalisation, script detection and critical tokens (ai-design §5.2). Pure functions.

`normalise`: NFKC -> casefold -> unify dashes and quotes -> drop Unicode punctuation except
`% . , : / + -` (currency symbols are kept: they are `Sc`, not punctuation) -> collapse whitespace.
It is used on both sides of every text comparison, never on the text we render.

`keep` preserves chosen quote-like / bracket characters (`DELIMITERS`, after folding their variants)
through normalisation: the OCR read keeps every delimiter the required text does not contain
(`delimiters_to_keep`), so a drawn «…» around the copy is an extra character, not invisible noise.
"""

import unicodedata
from collections import Counter

import regex

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−﹘﹣－⸺⸻"), "-")
_QUOTES = dict.fromkeys(map(ord, "‘’‚‛′´`"), "'") | dict.fromkeys(map(ord, "“”„‟″«»‹›"), '"')
KEEP_PUNCT = frozenset("%.,:/+-")
# Quote-like and bracket characters after `fold` (every single quote -> ', every double quote and
# guillemet -> "). `<` and `>` are math symbols and survive normalisation anyway.
DELIMITERS = frozenset("'\"()[]{}<>「」『』【】〔〕")
# Scripts written without spaces between words: spaces are dropped before comparing them.
UNSPACED_SCRIPTS = frozenset({"Jpan", "Hans", "Hant", "Hani", "Thai", "Khmr", "Laoo", "Mymr"})
# Scripts written right to left: OCR words of a line are read from the rightmost one.
RTL_SCRIPTS = frozenset({"Arab", "Hebr"})

_TOKEN_NUMBER = regex.compile(r"(?<![\p{L}\p{N}])[-+]?\p{Sc}?\d[\d.,:/]*%?\p{Sc}?")
_TOKEN_CAPS = regex.compile(r"(?<![\p{L}\p{N}])\p{Lu}[\p{Lu}\p{N}]{1,3}(?![\p{L}\p{N}])")
_WS = regex.compile(r"\s+")


def fold(text: str) -> str:
    """NFKC -> casefold -> unify dashes and quote variants (no characters dropped)."""
    return unicodedata.normalize("NFKC", text).casefold().translate(_DASHES).translate(_QUOTES)


def normalise(text: str, keep: frozenset[str] = frozenset()) -> str:
    t = fold(text)
    kept = [
        ch
        for ch in t
        if ch in keep or not (unicodedata.category(ch).startswith("P") and ch not in KEEP_PUNCT)
    ]
    return _WS.sub(" ", "".join(kept)).strip()


def delimiters_to_keep(required: str) -> frozenset[str]:
    """The delimiters the required text does not contain: in a read, each one is an extra
    character. The ones it does contain are dropped on both sides (OCR often misses quotes)."""
    present = set(fold(required))
    return frozenset(ch for ch in DELIMITERS if ch not in present)


def compact(text: str, script: str, keep: frozenset[str] = frozenset()) -> str:
    """Normalised text for comparison; unspaced scripts also lose their (OCR-inserted) spaces."""
    t = normalise(text, keep)
    return t.replace(" ", "") if script in UNSPACED_SCRIPTS else t


def _char_script(ch: str) -> str | None:
    if not ch.isalpha():
        return None
    name = unicodedata.name(ch, "")
    for prefix, script in (
        ("LATIN", "Latn"),
        ("CYRILLIC", "Cyrl"),
        ("GREEK", "Grek"),
        ("ARABIC", "Arab"),
        ("HEBREW", "Hebr"),
        ("DEVANAGARI", "Deva"),
        ("BENGALI", "Beng"),
        ("TAMIL", "Taml"),
        ("THAI", "Thai"),
        ("KHMER", "Khmr"),
        ("HANGUL", "Hang"),
        ("HIRAGANA", "Kana"),
        ("KATAKANA", "Kana"),
        ("CJK", "Hani"),
        ("ETHIOPIC", "Ethi"),
        ("SINHALA", "Sinh"),
    ):
        if name.startswith(prefix):
            return script
    return "Zyyy"


def detect_script(text: str) -> str:
    """ISO 15924 code of the dominant script: Latn, Cyrl, Jpan (any kana), Hani, Kore, Deva, ..."""
    counts = Counter(s for s in map(_char_script, text) if s is not None)
    if not counts:
        return "Zyyy"
    if counts.get("Kana"):
        return "Jpan"
    top = counts.most_common(1)[0][0]
    return "Kore" if top == "Hang" else top


def critical_tokens(raw: str) -> list[str]:
    """Numbers, prices, percentages, dates/times and short all-caps words, normalised, in order.

    Each must appear exactly in the OCR read: a 1-character typo in `30%` is a failure even when
    the overall CER is small.
    """
    found: list[str] = []
    text = unicodedata.normalize("NFKC", raw).translate(_DASHES)
    for match in _TOKEN_NUMBER.finditer(text):
        token = match.group(0).rstrip(".,:/")
        found.append(normalise(token))
    for match in _TOKEN_CAPS.finditer(text):
        if not any(ch.isdigit() for ch in match.group(0)):
            found.append(normalise(match.group(0)))
    seen: set[str] = set()
    ordered: list[str] = []
    for token in found:
        if token and token not in seen:
            seen.add(token)
            ordered.append(token)
    return ordered
