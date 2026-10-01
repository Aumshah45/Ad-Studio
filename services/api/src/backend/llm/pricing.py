"""List-price equivalents per model prefix (estimates, even on free tiers).

Token prices are USD per 1M tokens; image prices per image; audio per second; items per item.
Longest matching prefix wins. Update from provider pricing pages on the day.
"""

from typing import Literal

UnitType = Literal["tokens", "images", "seconds", "items"]

# (prefix, unit_type) -> (input price, output price)
PRICES: dict[tuple[str, UnitType], tuple[float, float]] = {
    ("google:gemini-3.1-flash-lite-image", "images"): (0.0, 0.034),
    ("google:gemini-3.1-flash-image", "images"): (0.0, 0.067),
    ("google:gemini-3.1-flash-lite-image", "tokens"): (0.10, 0.40),
    ("google:gemini-3.1-flash-image", "tokens"): (0.30, 2.50),
    ("google:gemini-3.5-flash-lite", "tokens"): (0.10, 0.40),
    ("google:gemini-3.1-flash-lite", "tokens"): (0.10, 0.40),
    ("google:gemini", "tokens"): (0.30, 2.50),
    ("google:gemini-embedding", "tokens"): (0.15, 0.0),
    ("groq:llama-3.3-70b", "tokens"): (0.59, 0.79),
    ("groq:llama-3.1-8b", "tokens"): (0.05, 0.08),
    ("groq:openai/gpt-oss-120b", "tokens"): (0.15, 0.75),
    ("groq:openai/gpt-oss-20b", "tokens"): (0.10, 0.50),
    ("groq:whisper", "seconds"): (0.111 / 3600, 0.0),
    ("openrouter:", "tokens"): (0.0, 0.0),
    ("test:", "tokens"): (0.0, 0.0),
}

_PER_MILLION: set[UnitType] = {"tokens"}


def estimate_cost(
    model: str, input_units: float, output_units: float, unit_type: UnitType
) -> float:
    best: tuple[float, float] | None = None
    best_len = -1
    for (prefix, utype), price in PRICES.items():
        if utype == unit_type and model.startswith(prefix) and len(prefix) > best_len:
            best, best_len = price, len(prefix)
    if best is None:
        return 0.0
    scale = 1_000_000 if unit_type in _PER_MILLION else 1
    return round((input_units * best[0] + output_units * best[1]) / scale, 8)
