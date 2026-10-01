"""Text-safe zone and headline line breaking (ai-design §2.2). Deterministic; never a model.

Line breaks never change a character: `" ".join(lines) == normalized_space(raw)` is asserted.
"""

import re
from typing import Literal

from pydantic import BaseModel, Field

HEADLINE_CHARS_PER_LINE = 18
MAX_LINES = 3


class NormBox(BaseModel):
    """Normalised box, 0..1, origin top-left."""

    x0: float = Field(ge=0.0, le=1.0)
    y0: float = Field(ge=0.0, le=1.0)
    x1: float = Field(ge=0.0, le=1.0)
    y1: float = Field(ge=0.0, le=1.0)

    def to_pixels(self, width: int, height: int) -> tuple[int, int, int, int]:
        return (
            round(self.x0 * width),
            round(self.y0 * height),
            round(self.x1 * width),
            round(self.y1 * height),
        )

    def expanded(self, margin: float) -> "NormBox":
        """Grown by `margin` (fraction of the image) on every side, clamped to the image."""
        return NormBox(
            x0=max(0.0, self.x0 - margin),
            y0=max(0.0, self.y0 - margin),
            x1=min(1.0, self.x1 + margin),
            y1=min(1.0, self.y1 + margin),
        )

    @property
    def height(self) -> float:
        return self.y1 - self.y0


def default_zone(aspect_ratio: str, anchor: Literal["top", "bottom"] = "top") -> NormBox:
    """1:1 top band y in [0.04, 0.26]; 4:5 y in [0.04, 0.22]; x in [0.06, 0.94]. Bottom mirrors."""
    y0, y1 = (0.04, 0.26) if aspect_ratio == "1:1" else (0.04, 0.22)
    if anchor == "bottom":
        y0, y1 = 1.0 - y1, 1.0 - y0
    return NormBox(x0=0.06, y0=y0, x1=0.94, y1=y1)


def normalized_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _greedy(words: list[str], budget: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in words:
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= budget:
            current = f"{current} {word}"
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def break_lines(
    text: str, *, chars_per_line: int = HEADLINE_CHARS_PER_LINE, max_lines: int = MAX_LINES
) -> list[str]:
    """Greedy by character budget; explicit newlines in the input are kept as line breaks.

    If the text needs more than `max_lines` at the headline budget, the budget grows until it fits
    (the overlay fit check handles the smaller font size later).
    """
    explicit = [normalized_space(part) for part in text.split("\n") if part.strip()]
    if len(explicit) > 1:
        lines = explicit
    else:
        words = normalized_space(text).split(" ")
        budget = chars_per_line
        lines = _greedy(words, budget)
        while len(lines) > max_lines:
            budget += 2
            lines = _greedy(words, budget)
    if " ".join(lines) != normalized_space(text):  # pragma: no cover - invariant
        raise AssertionError("line breaking changed the text")
    return lines
