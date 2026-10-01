"""Font lookup for local rendering (the fake image client and synthetic test fixtures).

Prefers a wide-coverage system font (dashes, accents, CJK, Devanagari) and falls back to Pillow's
bundled scalable font, which covers basic Latin only. The deterministic overlay does not use this:
it renders with the bundled Noto fonts under `assets/fonts/` (`overlay.py`).
"""

from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)

Font = ImageFont.FreeTypeFont | ImageFont.ImageFont


@lru_cache(maxsize=1)
def system_font_path() -> str | None:
    for path in _CANDIDATES:
        if Path(path).is_file():
            return path
    return None


def load_font(size: int) -> Font:
    path = system_font_path()
    if path is not None:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)
