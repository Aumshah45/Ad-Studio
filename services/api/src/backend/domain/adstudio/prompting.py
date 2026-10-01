"""Compile the `ad_generate` image prompt from typed fields (code, never a model).

The required text appears once, the whole copy alone on one prompt line (`copy_lines`, no
delimiters, no line-break marker); nothing else user-controlled reaches the image prompt
(geography and season arrive as code-resolved values).
"""

import colorsys
from dataclasses import dataclass, field
from typing import Literal

from backend.llm.prompts.ad_generate import (
    AD_GENERATE,
    DEFAULT_SURFACE,
    SCALE_BLOCK_SIZED,
    SCALE_BLOCK_UNSIZED,
    TEXT_BLOCK_NATIVE,
    TEXT_BLOCK_OVERLAY,
    copy_lines,
    escape_literal,
)
from backend.llm.prompts.base import Prompt

_NAMED_COLORS: dict[str, tuple[int, int, int]] = {
    "black": (0, 0, 0),
    "white": (255, 255, 255),
    "charcoal": (54, 69, 79),
    "grey": (128, 128, 128),
    "light grey": (211, 211, 211),
    "navy": (13, 40, 90),
    "deep blue": (13, 59, 102),
    "blue": (40, 100, 200),
    "sky blue": (135, 206, 235),
    "teal": (0, 128, 128),
    "green": (40, 140, 60),
    "olive": (110, 120, 40),
    "mint": (170, 230, 200),
    "yellow": (244, 211, 94),
    "cream": (250, 240, 202),
    "orange": (238, 150, 75),
    "red": (200, 16, 46),
    "burgundy": (110, 20, 40),
    "pink": (240, 150, 180),
    "purple": (110, 60, 150),
    "brown": (120, 80, 40),
    "sand": (220, 195, 150),
}


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    c = color.strip().lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def color_name(color: str) -> str:
    """Nearest plain colour name for a hex value (image models follow names better than hex)."""
    r, g, b = hex_to_rgb(color)
    return min(
        _NAMED_COLORS,
        key=lambda n: sum((a - c) ** 2 for a, c in zip((r, g, b), _NAMED_COLORS[n], strict=True)),
    )


# --- product colours (ad_generate v6) ------------------------------------------------------------
# `color_name` picks the nearest of a few RGB anchors, which is fine for a band or a palette but
# names P4's raspberry-pink bottle (#DC2E63) "red": the prompt then asked for a red bottle and every
# P4 ad drew one (analysis E6). Product colours are named by hue family instead (HLS hue, with
# lightness and chroma deciding the achromatic, brown/tan and light/dark cases), and the prompt
# also names the neighbouring hues the product is NOT.
_HUE_FAMILIES: tuple[tuple[float, str], ...] = (
    (12, "red"),
    (40, "orange"),
    (68, "yellow"),
    (165, "green"),
    (200, "teal"),
    (250, "blue"),
    (290, "purple"),
    (330, "magenta"),
    (348, "pink"),
    (360, "red"),
)
_NOT_HUES: dict[str, tuple[str, str]] = {
    "red": ("orange", "pink"),
    "orange": ("red", "yellow"),
    "yellow": ("orange", "green"),
    "green": ("yellow", "teal"),
    "teal": ("green", "blue"),
    "blue": ("teal", "purple"),
    "purple": ("blue", "magenta"),
    "magenta": ("purple", "pink"),
    "pink": ("red", "magenta"),
    "brown": ("orange", "red"),
    "tan": ("orange", "yellow"),
}
_ACHROMATIC_CHROMA = 0.12  # max-min of the 0-1 RGB channels below this: a grey


def hue_family(color: str) -> tuple[str, bool]:
    """(plain colour name, chromatic?) for a product colour, e.g. ("pink", True)."""
    r, g, b = (v / 255 for v in hex_to_rgb(color))
    hue, light, _ = colorsys.rgb_to_hls(r, g, b)
    chroma = max(r, g, b) - min(r, g, b)
    if chroma < _ACHROMATIC_CHROMA:
        for limit, name in (
            (0.15, "black"),
            (0.35, "charcoal"),
            (0.6, "grey"),
            (0.85, "light grey"),
        ):
            if light < limit:
                return name, False
        return "white", False
    deg = hue * 360
    base = next(name for limit, name in _HUE_FAMILIES if deg < limit)
    if 12 <= deg < 50 and chroma < 0.45:
        return ("tan" if light >= 0.45 else "brown"), True
    if base in ("orange", "red") and light < 0.3:
        return "brown" if base == "orange" else "dark red", True
    if base == "red" and light > 0.7:
        return "pink", True
    if light > 0.72:
        return f"pale {base}", True
    if light < 0.25:
        return f"dark {base}", True
    return base, True


def product_color_name(color: str) -> str:
    return hue_family(color)[0]


def product_colors_text(colors: list[str]) -> str:
    """ "pink #DC2E63, charcoal #494A49" (names plus hex; image models follow names better)."""
    return ", ".join(f"{product_color_name(c)} {c.upper()}" for c in colors)


def product_colour_line(colors: list[str]) -> str:
    """The product block's colour line: the main colour and the hues it must not drift to."""
    if not colors:
        return "COLOURS: exactly as in the reference image."
    name, chromatic = hue_family(colors[0])
    main = f"COLOURS: the main colour is {name} ({colors[0].upper()})"
    family = name.split()[-1]
    if chromatic and family in _NOT_HUES:
        a, b = _NOT_HUES[family]
        main += f", not {a} or {b}"
    rest = [f"{product_color_name(c)} ({c.upper()})" for c in colors[1:]]
    if rest:
        main += "; the other colours are " + _and(rest, "")
    return main + ". Keep these exact hues: do not shift them warmer, cooler, lighter or darker."


@dataclass(frozen=True)
class AdPromptFields:
    aspect_ratio: str
    country_name: str
    effective_season: str
    months_text: str
    setting: str
    cues: list[str]
    lighting: str
    palette: list[str]
    mood: str
    avoid: list[str]
    lines: list[str]
    headline: str = ""  # the copy as written (required_text.raw); "" -> the lines joined
    text_mode: Literal["native", "overlay"] = "native"
    zone_anchor: Literal["top", "bottom"] = "top"
    zone_height_pct: int = 18
    band_color: str = "#0D3B66"
    text_color: str = "#FFFFFF"
    category: str = "product"
    dominant_colors: list[str] = field(default_factory=list[str])
    visible_text: list[str] = field(default_factory=list[str])
    product_anchor: str = "lower centre"
    product_scale_pct: int = 50
    # ADR-007 sizing (empty for specs without a real-world size: the unsized scale line is used)
    framing_text: str = ""
    size_cm: float | None = None
    scale_min_pct: int | None = None
    scale_max_pct: int | None = None
    resting_surface: str = ""
    scale_references: list[str] = field(default_factory=list[str])


def _join(items: list[str], empty: str) -> str:
    cleaned = [i.strip() for i in items if i.strip()]
    return ", ".join(cleaned) if cleaned else empty


def _and(items: list[str], empty: str) -> str:
    cleaned = [i.strip() for i in items if i.strip()]
    if not cleaned:
        return empty
    return cleaned[0] if len(cleaned) == 1 else ", ".join(cleaned[:-1]) + " and " + cleaned[-1]


def render_ad_prompt(f: AdPromptFields, prompt: Prompt = AD_GENERATE) -> str:
    if f.text_mode == "native":
        text_block = TEXT_BLOCK_NATIVE.format(
            zone_anchor=f.zone_anchor,
            zone_height_pct=f.zone_height_pct,
            band_color_name=color_name(f.band_color),
            text_color_name=color_name(f.text_color),
            n_lines=len(f.lines),
            copy_lines=copy_lines(f.lines, f.headline),
        )
    else:
        text_block = TEXT_BLOCK_OVERLAY.format(
            zone_anchor=f.zone_anchor,
            zone_height_pct=f.zone_height_pct,
            band_color_name=color_name(f.band_color),
        )
    category = f.category
    if f.framing_text and f.size_cm and f.scale_min_pct and f.scale_max_pct:
        scale_block = SCALE_BLOCK_SIZED.format(
            framing_text=f.framing_text,
            category=category,
            size_cm=f"{f.size_cm:.0f}",
            scale_min_pct=f.scale_min_pct,
            scale_max_pct=f.scale_max_pct,
            scale_references=_and(f.scale_references, "a familiar everyday object"),
        )
    else:
        scale_block = SCALE_BLOCK_UNSIZED.format(product_scale_pct=f.product_scale_pct)
    visible = (
        "; ".join(f"label text to preserve: «{escape_literal(t)}»" for t in f.visible_text)
        if f.visible_text
        else "no printed text"
    )
    return prompt.system.format(
        aspect_ratio=f.aspect_ratio,
        category=f.category,
        dominant_colors=product_colors_text(f.dominant_colors) or "as in the reference",
        colour_line=product_colour_line(f.dominant_colors),
        visible_text=visible,
        product_anchor=f.product_anchor,
        scale_block=scale_block,
        surface=f.resting_surface.strip() or DEFAULT_SURFACE,
        setting=f.setting.rstrip("."),
        country_name=f.country_name,
        effective_season=f.effective_season.replace("_", " "),
        months_text=f.months_text,
        cues=_join(f.cues, "a clean, uncluttered setting"),
        lighting=f.lighting,
        palette=_join([color_name(c) for c in f.palette], "neutral tones"),
        mood=f.mood,
        avoid=_join(f.avoid, "clutter"),
        text_block=text_block,
    )
