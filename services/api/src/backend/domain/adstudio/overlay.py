"""Deterministic text overlay into `spec.text_zone` (ai-design §4.4): the exact-text guarantee.

- A rounded, opaque band in `band_color` covers the zone, so any wrong native text is hidden
  (ai-design says alpha 0.92; at 0.92 a wrong native headline still ghosts through, so it is 1.0).
  Only pixels inside the zone box change; everything outside is byte-identical (asserted by tests).
- Bundled Noto fonts (OFL, `assets/fonts/`): Noto Sans Bold for Latin/Greek/Cyrillic, Noto Sans
  Devanagari Bold, Noto Sans JP Bold and (ev-0.8) Noto Sans KR Bold (Hangul subset), Noto Sans
  Thai Bold and Noto Sans Arabic Bold. Each character is drawn with the first font (script's
  preferred font first) that has a glyph for it, so mixed text ("दिवाली सेल 20%") works.
- Right-to-left scripts (Arabic): each font run is shaped by libraqm (joining, bidi inside the run)
  and the runs of a line are laid out right to left.
- Complex scripts (Devanagari, Arabic, Thai, ...) are shaped with libraqm. Without libraqm the
  overlay is refused (`overlay-script-unsupported`): we never ship broken shaping.
- Fit: the largest size (>= `MIN_FONT_PX` = 28) at which the lines fit the zone minus 8% padding,
  over every line breaking of at most `max_lines` lines. Line breaks never change a character:
  spaced scripts satisfy `" ".join(lines) == normalized_space(raw)`, unspaced scripts (Japanese,
  Chinese, Thai) `"".join(lines) == normalized_space(raw)`.
- Text colour: black or white, whichever has the higher WCAG contrast against the band as actually
  composited (>= 4.5 where possible).

`Overlay.render()` is pure CPU (no model call). `Overlay.verify()` re-reads the result with OCR:
`"ocr"` when the read is exact, `"construction"` when the script has no OCR pack (the rendered
string equals `raw` by construction), `"failed"` otherwise.
"""

import io
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fontTools.ttLib import TTFont  # pyright: ignore[reportMissingTypeStubs]
from PIL import Image, ImageDraw, ImageFont, features

from backend.core.errors import AppError
from backend.core.settings import API_DIR
from backend.domain.adstudio.evaluator.config import TextConfig
from backend.domain.adstudio.evaluator.ocr import OcrEngine
from backend.domain.adstudio.evaluator.text import evaluate_text
from backend.domain.adstudio.layout import NormBox, normalized_space
from backend.domain.adstudio.prompting import hex_to_rgb
from backend.domain.adstudio.textnorm import RTL_SCRIPTS, UNSPACED_SCRIPTS, detect_script

FONT_DIR = API_DIR / "assets" / "fonts"
MIN_FONT_PX = 28
PADDING = 0.08  # of the zone's width/height, on each side
BAND_ALPHA = 1.0  # a translucent band lets a wrong native headline show through
MIN_CONTRAST = 4.5
LINE_GAP = 0.18  # of the font size, between lines
MAX_LINES = 3

LATIN = "NotoSans-Bold.ttf"
DEVANAGARI = "NotoSansDevanagari-Bold.ttf"
JAPANESE = "NotoSansJP-Bold.otf"
KOREAN = "NotoSansKR-Bold-Hangul.otf"
THAI = "NotoSansThai-Bold.ttf"
ARABIC = "NotoSansArabic-Bold.ttf"
FONT_FILES: tuple[str, ...] = (LATIN, DEVANAGARI, JAPANESE, KOREAN, THAI, ARABIC)
# The font tried first for a script; any other bundled font may fill a missing glyph.
PREFERRED: dict[str, str] = {
    "Latn": LATIN,
    "Grek": LATIN,
    "Cyrl": LATIN,
    "Zyyy": LATIN,
    "Deva": DEVANAGARI,
    "Jpan": JAPANESE,
    "Hani": JAPANESE,
    "Kana": JAPANESE,
    "Kore": KOREAN,
    "Hang": KOREAN,
    "Thai": THAI,
    "Arab": ARABIC,
}
# Scripts whose glyphs need OpenType shaping (reordering, conjuncts, joining).
COMPLEX_SCRIPTS = frozenset(
    {"Deva", "Beng", "Taml", "Arab", "Hebr", "Thai", "Khmr", "Laoo", "Mymr", "Sinh", "Ethi"}
)


class OverlayRefusedError(Exception):
    """The overlay can't guarantee exact, correctly shaped text; the run goes to needs_review."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


# --- fonts ----------------------------------------------------------------------------------------


@lru_cache(maxsize=8)
def font_cmap(name: str) -> frozenset[int]:
    font = TTFont(str(FONT_DIR / name), lazy=True)
    try:
        cmap = font.getBestCmap() or {}
        return frozenset(cmap.keys())
    finally:
        font.close()


def raqm_available() -> bool:
    return bool(features.check("raqm"))


def _ignorable(ch: str) -> bool:
    return ch.isspace() or unicodedata.category(ch) == "Cf"  # ZWJ / ZWNJ are shaping hints


def font_order(script: str) -> tuple[str, ...]:
    first = PREFERRED.get(script, LATIN)
    return (first, *(f for f in FONT_FILES if f != first))


def missing_glyphs(text: str, script: str | None = None) -> list[str]:
    """Characters no bundled font can draw (whitespace and ZWJ/ZWNJ are always fine)."""
    order = font_order(script or detect_script(text))
    return [
        ch
        for ch in dict.fromkeys(text)
        if not _ignorable(ch) and not any(ord(ch) in font_cmap(f) for f in order)
    ]


def ensure_overlay_supported(text: str) -> None:
    """422 `unsupported-script` at input: the exact-text guarantee needs a glyph for every char."""
    missing = missing_glyphs(text)
    if missing:
        shown = " ".join(f"U+{ord(c):04X}" for c in missing[:8])
        raise AppError(
            422,
            "unsupported-script",
            "Unsupported script",
            f"The required text uses characters our overlay fonts can't draw ({shown}), so the "
            "exact-text guarantee can't be kept. Supported: Latin, Greek, Cyrillic, Devanagari, "
            "Japanese, Korean, Thai and Arabic.",
        )


def needs_shaping(text: str) -> bool:
    scripts = {detect_script(ch) for ch in text if ch.isalpha()}
    return bool(scripts & COMPLEX_SCRIPTS)


@lru_cache(maxsize=256)
def _font(name: str, size: int, raqm: bool) -> ImageFont.FreeTypeFont:
    engine = ImageFont.Layout.RAQM if raqm else ImageFont.Layout.BASIC
    return ImageFont.truetype(str(FONT_DIR / name), size, layout_engine=engine)


def font_runs(line: str, script: str) -> list[tuple[str, str]]:
    """Split a line into (font file, text) runs by glyph coverage; marks and spaces stay put."""
    order = font_order(script)
    runs: list[tuple[str, str]] = []
    for ch in line:
        if runs and (_ignorable(ch) or unicodedata.category(ch).startswith("M")):
            name, text = runs[-1]
            runs[-1] = (name, text + ch)
            continue
        name = next((f for f in order if ord(ch) in font_cmap(f)), order[0])
        if runs and runs[-1][0] == name:
            runs[-1] = (name, runs[-1][1] + ch)
        else:
            runs.append((name, ch))
    return runs


def _bidi_class(ch: str) -> str:
    """L (left-to-right: Latin, digits), R (right-to-left letters) or N (neutral/weak)."""
    cls = unicodedata.bidirectional(ch)
    if cls in ("L", "EN", "AN"):
        return "L"
    if cls in ("R", "AL"):
        return "R"
    return "N"


def directional_segments(line: str) -> list[tuple[str, str]]:
    """A right-to-left line split into ("rtl" | "ltr", text) segments in logical order: a small
    UAX #9 resolution (paragraph direction RTL). Numbers and Latin runs are LTR; a separator or
    terminator touching a number ("30%", "1,5") joins it; other neutrals take the direction of
    both neighbours when they agree, else the paragraph's (RTL); marks follow their base."""
    classes = [_bidi_class(ch) for ch in line]
    for i, ch in enumerate(line):
        if unicodedata.bidirectional(ch) in ("ES", "ET", "CS"):
            near = [j for j in (i - 1, i + 1) if 0 <= j < len(line)]
            if any(unicodedata.bidirectional(line[j]) in ("EN", "AN") for j in near):
                classes[i] = "L"
    for i, ch in enumerate(line):
        if unicodedata.category(ch).startswith("M") and i > 0:
            classes[i] = classes[i - 1]
    resolved = list(classes)
    for i, cls in enumerate(classes):
        if cls != "N":
            continue
        before = next((c for c in reversed(classes[:i]) if c != "N"), "R")
        after = next((c for c in classes[i + 1 :] if c != "N"), "R")
        resolved[i] = "L" if before == after == "L" else "R"
    segments: list[tuple[str, str]] = []
    for ch, cls in zip(line, resolved, strict=True):
        direction = "ltr" if cls == "L" else "rtl"
        if segments and segments[-1][0] == direction:
            segments[-1] = (direction, segments[-1][1] + ch)
        else:
            segments.append((direction, ch))
    return segments


Run = tuple[str, str, str | None]  # (font file, text, raqm direction or None)


def visual_runs(line: str, script: str) -> list[Run]:
    """The line's font runs in drawing order, left to right. Left-to-right scripts: the font runs
    in order. Right-to-left: the directional segments right to left, each segment's font runs in
    its own direction; each run is then shaped by libraqm with that direction."""
    if script not in RTL_SCRIPTS:
        return [(name, text, None) for name, text in font_runs(line, script)]
    out: list[Run] = []
    for direction, segment in reversed(directional_segments(line)):
        runs = font_runs(segment, script)
        if direction == "rtl":
            runs = list(reversed(runs))
        out += [(name, text, direction) for name, text in runs]
    return out


# --- line breaking --------------------------------------------------------------------------------


def _greedy(units: list[str], budget: int, sep: str) -> list[str]:
    lines: list[str] = []
    current = ""
    for unit in units:
        candidate = f"{current}{sep}{unit}" if current else unit
        if current and len(candidate) > budget:
            lines.append(current)
            current = unit
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _char_chunks(text: str, n: int) -> list[str]:
    """Split unspaced text into n nearly equal chunks without splitting a grapheme's marks."""
    clusters: list[str] = []
    for ch in text:
        if clusters and unicodedata.category(ch).startswith("M"):
            clusters[-1] += ch
        else:
            clusters.append(ch)
    size = -(-len(clusters) // n)
    return ["".join(clusters[i : i + size]) for i in range(0, len(clusters), size)]


def line_breakings(raw: str, script: str, max_lines: int = MAX_LINES) -> list[list[str]]:
    """Every distinct breaking into <= max_lines lines that keeps the text exactly.

    Explicit newlines in the input are kept as the only breaks. Spaced scripts break at spaces
    (joined with " "); unspaced scripts break between characters (joined with "").
    """
    explicit = [normalized_space(p) for p in raw.split("\n") if p.strip()]
    if len(explicit) > 1:
        return [explicit]
    text = normalized_space(raw)
    out: list[list[str]] = []
    if script in UNSPACED_SCRIPTS:
        for n in range(1, max_lines + 1):
            lines = [c for c in _char_chunks(text, n) if c]
            if len(lines) <= max_lines and lines not in out:
                out.append(lines)
    else:
        words = text.split(" ")
        for budget in range(len(text), 0, -1):
            lines = _greedy(words, budget, " ")
            if len(lines) > max_lines:
                break
            if lines not in out:
                out.append(lines)
    for lines in out:
        joined = ("" if script in UNSPACED_SCRIPTS else " ").join(lines)
        if joined != text:  # pragma: no cover - invariant
            raise AssertionError("line breaking changed the text")
    return out


# --- colour ---------------------------------------------------------------------------------------


def _luminance(rgb: tuple[int, int, int]) -> float:
    def channel(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = rgb
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _best_text_colour(band: tuple[int, int, int]) -> tuple[tuple[int, int, int], float]:
    options = ((0, 0, 0), (255, 255, 255))
    best = max(options, key=lambda c: contrast(band, c))
    return best, contrast(band, best)


# --- render ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class OverlayZone:
    """The subset of `spec.text_zone` the overlay needs."""

    box: NormBox
    band_color: str
    text_color: str | None = None  # the spec's pick; re-checked against the composited band
    max_lines: int = MAX_LINES


@dataclass(frozen=True)
class OverlayResult:
    data: bytes  # PNG, same size as the input
    width: int
    height: int
    lines: tuple[str, ...]
    font_size: int
    fonts: tuple[str, ...]
    zone_px: tuple[int, int, int, int]
    band_color: str
    band_alpha: float
    text_color: str
    contrast: float
    shaped: bool
    meta: dict[str, object] = field(default_factory=dict[str, object])


@dataclass(frozen=True)
class _Layout:
    lines: list[str]
    size: int
    runs: list[list[Run]]
    widths: list[float]
    ascent: int
    descent: int


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


class Overlay:
    def __init__(self, *, min_font_px: int = MIN_FONT_PX, raqm: bool | None = None) -> None:
        self.min_font_px = min_font_px
        self.raqm = raqm_available() if raqm is None else raqm

    def _measure(self, lines: list[str], script: str, size: int) -> _Layout:
        runs = [visual_runs(line.strip(), script) for line in lines]
        widths: list[float] = []
        ascent = descent = 0
        for line_runs in runs:
            width = 0.0
            for name, text, direction in line_runs:
                font = _font(name, size, self.raqm)
                width += font.getlength(text, direction=direction if self.raqm else None)
                a, d = font.getmetrics()
                ascent, descent = max(ascent, a), max(descent, d)
            widths.append(width)
        return _Layout(lines, size, runs, widths, ascent, descent)

    def _fits(self, layout: _Layout, inner_w: int, inner_h: int) -> bool:
        n = len(layout.lines)
        block = n * (layout.ascent + layout.descent) + (n - 1) * round(layout.size * LINE_GAP)
        return max(layout.widths) <= inner_w and block <= inner_h

    def fit(self, raw: str, script: str, inner: tuple[int, int], max_lines: int) -> _Layout:
        """The largest-size layout over all exact line breakings; refuses below the minimum."""
        inner_w, inner_h = inner
        best: _Layout | None = None
        for lines in line_breakings(raw, script, max_lines):
            lo, hi = self.min_font_px, max(self.min_font_px, inner_h)
            if not self._fits(self._measure(lines, script, lo), inner_w, inner_h):
                continue
            while lo < hi:  # largest size that fits
                mid = (lo + hi + 1) // 2
                if self._fits(self._measure(lines, script, mid), inner_w, inner_h):
                    lo = mid
                else:
                    hi = mid - 1
            if best is None or lo > best.size:
                best = self._measure(lines, script, lo)
        if best is None:
            raise OverlayRefusedError(
                "overlay-does-not-fit",
                f"The text does not fit the zone at {self.min_font_px} px in {max_lines} lines.",
            )
        return best

    def render(self, image: bytes, zone: OverlayZone, raw: str) -> OverlayResult:
        script = detect_script(raw)
        missing = missing_glyphs(raw, script)
        if missing:
            raise OverlayRefusedError(
                "overlay-script-unsupported",
                "No bundled font has a glyph for " + " ".join(f"U+{ord(c):04X}" for c in missing),
            )
        shaped = needs_shaping(raw)
        if shaped and not self.raqm:
            raise OverlayRefusedError(
                "overlay-script-unsupported",
                f"Script {script} needs libraqm shaping, which this Pillow build lacks.",
            )
        with Image.open(io.BytesIO(image)) as src:
            base = src.convert("RGB")
        w, h = base.size
        x0, y0, x1, y1 = zone.box.to_pixels(w, h)
        zw, zh = x1 - x0, y1 - y0
        pad_x, pad_y = round(zw * PADDING), round(zh * PADDING)
        layout = self.fit(raw, script, (zw - 2 * pad_x, zh - 2 * pad_y), zone.max_lines)

        # Band: composite in a zone-sized layer, so nothing outside the zone box is touched.
        region = base.crop((x0, y0, x1, y1)).convert("RGBA")
        band_rgb = hex_to_rgb(zone.band_color)
        alpha = BAND_ALPHA
        text_rgb, ratio = (0, 0, 0), 0.0
        composed = region
        for alpha in dict.fromkeys((BAND_ALPHA, 1.0)):
            layer = Image.new("RGBA", (zw, zh), (0, 0, 0, 0))
            radius = max(2, round(min(zw, zh) * 0.12))
            ImageDraw.Draw(layer).rounded_rectangle(
                (0, 0, zw - 1, zh - 1), radius=radius, fill=(*band_rgb, round(alpha * 255))
            )
            composed = Image.alpha_composite(region, layer)
            inner = composed.crop((pad_x, pad_y, zw - pad_x, zh - pad_y)).convert("RGB")
            mean = inner.resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))
            effective = (int(mean[0]), int(mean[1]), int(mean[2]))  # type: ignore[index]
            text_rgb, ratio = _best_text_colour(effective)
            if ratio >= MIN_CONTRAST:
                break
        draw = ImageDraw.Draw(composed)
        n = len(layout.lines)
        line_h = layout.ascent + layout.descent
        gap = round(layout.size * LINE_GAP)
        block = n * line_h + (n - 1) * gap
        y = (zh - block) // 2
        for line_runs, width in zip(layout.runs, layout.widths, strict=True):
            x = (zw - width) / 2
            baseline = y + layout.ascent
            for name, text, direction in line_runs:  # visual order, left to right
                font = _font(name, layout.size, self.raqm)
                way = direction if self.raqm else None
                draw.text(
                    (x, baseline),
                    text,
                    font=font,
                    fill=(*text_rgb, 255),
                    anchor="ls",
                    direction=way,
                )
                x += font.getlength(text, direction=way)
            y += line_h + gap
        out = base.copy()
        out.paste(composed.convert("RGB"), (x0, y0))
        buf = io.BytesIO()
        out.save(buf, format="PNG", optimize=False)
        return OverlayResult(
            data=buf.getvalue(),
            width=w,
            height=h,
            lines=tuple(line.strip() for line in layout.lines),
            font_size=layout.size,
            fonts=tuple(dict.fromkeys(name for runs in layout.runs for name, _, _ in runs)),
            zone_px=(x0, y0, x1, y1),
            band_color=zone.band_color.upper(),
            band_alpha=alpha,
            text_color=_hex(text_rgb),
            contrast=round(ratio, 2),
            shaped=shaped,
            meta={"script": script, "raqm": self.raqm},
        )

    async def verify(
        self,
        result: OverlayResult,
        raw: str,
        zone: NormBox,
        *,
        ocr: OcrEngine | None,
        cfg: TextConfig,
        market_langs: tuple[str, ...] = (),
        ocr2: OcrEngine | None = None,
    ) -> Literal["ocr", "construction", "failed"]:
        """Re-read the overlaid headline. No OCR pack for the script -> "construction"."""
        script = detect_script(raw)
        if ocr is None or not ocr.available() or ocr.lang_for(script, market_langs) is None:
            return "construction"
        with Image.open(io.BytesIO(result.data)) as img:
            rgb = img.convert("RGB")
        _, dim = await evaluate_text(
            rgb,
            None,
            required_text=raw,
            script=script,
            zone=zone,
            market_langs=market_langs,
            text_mode="native",
            ocr=ocr,
            cfg=cfg,
            ocr2=ocr2,
        )
        if dim.passed is None:
            return "construction"
        # Only the headline matters here: stray text elsewhere is the precondition's business.
        headline = {"ocr_cer", "critical_tokens", "vlm_readback"}
        return "failed" if headline & set(dim.failed_checks) else "ocr"


def font_path(name: str) -> Path:
    return FONT_DIR / name
