"""Text fidelity (ai-design §5.2): an OCR ensemble plus the VLM read-back, where a FAIL needs
two readers to agree (ev-0.8).

Readers: Tesseract (always), Apple Vision (macOS, `ocr2`) and the blind VLM read-back (`ad_inspect`
transcription). Each reader's zone read is aligned to the copy and turned into *error keys*: a
changed or deleted copy character (`chg`, position), an inserted character (`ins`, position,
glyph), extra zone content (`extra`: a quote/guillemet/bracket the copy lacks, or a confident
token that is not a word of the copy, read outside the matched window; ev-0.4) and a missing
critical token (`missing`).

- **Agreement fails.** An error key seen by both OCR engines, or by one OCR engine and the
  read-back, fails the text (a drawn «Summer Sale» or "Only / AED 49" fails).
- **Everyone exact passes.**
- **Otherwise re-read.** When an error is seen by only one reader, the zone is read again at
  `reread_upscale` with both engines. At that resolution an engine *still sees* an error only if
  every one of its reads (plain, inverted, contrast-stretched, full image) shows it: a character one
  preprocessing reads correctly is an engine artefact (Tesseract's "Ath" for a bold "4th"), not a
  rendering defect. Agreement then fails; no engine still seeing an error passes; anything else is
  `unverified` (fails closed to needs_review, never a silent pass).
- **One reader only** (a single engine and no read-back, e.g. the overlay check off macOS): its
  errors fail, as before ev-0.8.

Stray text (outside the headline zone, not the product's own label): agreement between any two
readers fails. A token only one OCR engine reads, which the other engine and the read-back's blind
transcription both lack, is OCR noise (a note). A read-back-only stray is re-read in its region with
both engines: confirmed fails, unconfirmed is `unverified` (OCR can't read embossed or perspective
prop text the VLM reads, so its silence doesn't refute it), unless the product itself was not
located, where label text can't be told from stray text and the failed product check owns the image
(a note).
"""

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

import regex
from PIL import Image, ImageOps
from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

from backend.domain.adstudio.evaluator.config import TextConfig
from backend.domain.adstudio.evaluator.ocr import (
    OcrEngine,
    OcrResult,
    OcrWord,
    reading_order,
    text_of,
)
from backend.domain.adstudio.evaluator.readback import (
    TextRead,
    TextReadback,
    vlm_strays,
    zone_text,
)
from backend.domain.adstudio.evaluator.schemas import CheckResult, DimensionResult
from backend.domain.adstudio.evaluator.schemas import dimension_from_checks as _dimension
from backend.domain.adstudio.imaging import resize
from backend.domain.adstudio.layout import NormBox
from backend.domain.adstudio.textnorm import (
    DELIMITERS,
    RTL_SCRIPTS,
    UNSPACED_SCRIPTS,
    compact,
    critical_tokens,
    delimiters_to_keep,
    fold,
    normalise,
)

PSM_BLOCK = 6  # the zone crop: one uniform block of text
PSM_SPARSE = 11  # the full image: sparse text anywhere
_ALNUM = regex.compile(r"[\p{L}\p{N}]")
_STRAY_SHAPE = regex.compile(r"^[\p{L}\p{N}\p{Sc}%.,:/+'&-]+$")


# --- alignment ------------------------------------------------------------------------------------


def align(read: str, required: str) -> tuple[int, int, int]:
    """Best approximate occurrence of `required` inside `read` (semi-global edit distance).

    Returns (distance, start, end) with `read[start:end]` the best-matching window. A read that
    contains the required text plus other words scores 0; a 1-character typo scores 1.
    """
    m, n = len(required), len(read)
    if m == 0:
        return 0, 0, 0
    if n == 0:
        return m, 0, 0
    prev = [0] * (n + 1)  # free start anywhere in `read`
    prev_start = list(range(n + 1))
    for i in range(1, m + 1):
        cur = [i] + [0] * n
        cur_start = [0] * (n + 1)
        ch = required[i - 1]
        for j in range(1, n + 1):
            sub = prev[j - 1] + (ch != read[j - 1])
            dele = prev[j] + 1
            ins = cur[j - 1] + 1
            best = min(sub, dele, ins)
            cur[j] = best
            if best == sub:
                cur_start[j] = prev_start[j - 1]
            elif best == dele:
                cur_start[j] = prev_start[j]
            else:
                cur_start[j] = cur_start[j - 1]
        prev, prev_start = cur, cur_start
    end = min(range(n + 1), key=lambda j: (prev[j], j))
    return prev[end], prev_start[end], end


def cer(read: str, required: str) -> tuple[float, str]:
    """(character error rate of the best window, the window) on already-normalised strings."""
    dist, start, end = align(read, required)
    return (dist / len(required) if required else 0.0), read[start:end]


# --- extra content in the zone -------------------------------------------------------------------


@dataclass(frozen=True)
class ZoneRead:
    """One zone read as a comparison string, with each word's span in it (-1 when it is empty)."""

    source: str
    words: list[OcrWord]  # reading order
    raw: str
    text: str
    spans: list[tuple[int, int]]


def zone_read(source: str, words: list[OcrWord], script: str, keep: frozenset[str]) -> ZoneRead:
    rtl = script in RTL_SCRIPTS
    ordered = [w for line in reading_order(words, rtl=rtl) for w in line]
    sep = "" if script in UNSPACED_SCRIPTS else " "
    parts: list[str] = []
    spans: list[tuple[int, int]] = []
    pos = 0
    for word in ordered:
        norm = compact(word.text, script, keep)
        if not norm:
            spans.append((-1, -1))
            continue
        if parts:
            pos += len(sep)
        spans.append((pos, pos + len(norm)))
        parts.append(norm)
        pos += len(norm)
    return ZoneRead(source, ordered, text_of(words, rtl=rtl), sep.join(parts), spans)


def zone_extras(
    read: ZoneRead,
    window: tuple[int, int],
    required_c: str,
    script: str,
    zone_px: tuple[int, int, int, int],
    cfg: TextConfig,
) -> dict[str, OcrWord]:
    """Content of `read` outside the matched window: {extra token or delimiter: its word}.

    Only confident words whose centre lies inside the exact zone count (scene texture in the
    crop margin is not headline content). A token that is a word of the copy is not extra.
    """
    start, end = window
    copy_words = set(required_c.split())
    out: dict[str, OcrWord] = {}
    for word, (s, e) in zip(read.words, read.spans, strict=True):
        if s < 0 or word.conf < cfg.stray_min_conf or not _inside(word, zone_px):
            continue
        left = read.text[s : min(e, start)] if s < start else ""
        right = read.text[max(s, end) : e] if e > end else ""
        for part in (left, right):
            for ch in part:
                if ch in DELIMITERS:
                    # Report the glyph as read (« and », not the folded "), one key per glyph.
                    raw = [r for r in word.text if fold(r) == ch] or [ch]
                    for glyph in raw:
                        out.setdefault(glyph, word)
            token = "".join(ch for ch in part if ch not in DELIMITERS).strip(" .,:/+-")
            if len(_ALNUM.findall(token)) < cfg.zone_extra_min_alnum:
                continue
            is_copy = token in required_c if script in UNSPACED_SCRIPTS else token in copy_words
            if not is_copy:
                out.setdefault(token, word)
    return out


def has_token(text: str, token: str, script: str = "Latn") -> bool:
    if not token:
        return True
    if script in UNSPACED_SCRIPTS:
        return token in text
    if any(ch.isdigit() for ch in token):
        # A number must not be part of a longer number ("30%" is not in "130%"); a unit or suffix
        # may follow it ("24h", "4th").
        pattern = r"(?<![\p{N}.,])" + regex.escape(token) + r"(?![\p{N}])"
    else:
        pattern = r"(?<![\p{L}\p{N}])" + regex.escape(token) + r"(?![\p{L}\p{N}])"
    return regex.search(pattern, text) is not None


def missing_tokens(window: str, required_raw: str, script: str) -> list[str]:
    tokens = critical_tokens(required_raw)
    return [t for t in tokens if not has_token(window, compact(t, script), script)]


# --- scoring a set of zone reads -----------------------------------------------------------------


@dataclass(frozen=True)
class ZoneScore:
    """The best zone read against the copy, plus the extra content enough reads agree on."""

    cer: float  # window CER + extra characters
    window_cer: float
    best: ZoneRead
    window: tuple[int, int]
    extras: list[str]
    extra_words: dict[str, OcrWord]

    @property
    def window_text(self) -> str:
        return self.best.text[self.window[0] : self.window[1]]


def score_reads(
    reads: dict[str, list[OcrWord]],
    required_c: str,
    script: str,
    keep: frozenset[str],
    tight_px: tuple[int, int, int, int],
    cfg: TextConfig,
) -> ZoneScore:
    scored: list[tuple[float, int, ZoneRead, tuple[int, int]]] = []  # (cer, order, read, window)
    for order, (source, words) in enumerate(reads.items()):
        read = zone_read(source, words, script, keep)
        dist, start, end = align(read.text, required_c)
        scored.append((dist / len(required_c) if required_c else 0.0, order, read, (start, end)))
    window_cer, _, best, window = min(scored, key=lambda s: (s[0], s[1]))
    # Extra content: agreed on by enough reads (each read counts a token once).
    seen: Counter[str] = Counter()
    extra_words: dict[str, OcrWord] = {}
    for _, _, read, win in scored:
        for token, word in zone_extras(read, win, required_c, script, tight_px, cfg).items():
            seen[token] += 1
            extra_words.setdefault(token, word)
    need = min(cfg.zone_extra_min_sources, len(scored))
    extras = [t for t in extra_words if seen[t] >= need]
    extra_chars = sum(len(t) for t in extras)
    total = window_cer + (extra_chars / len(required_c) if required_c else 0.0)
    return ZoneScore(total, window_cer, best, window, extras, extra_words)


# --- OCR orchestration ----------------------------------------------------------------------------


def _inside(word: OcrWord, box: tuple[int, int, int, int]) -> bool:
    x0, y0, x1, y1 = box
    return x0 <= word.cx <= x1 and y0 <= word.cy <= y1


def _stray_candidate(word: OcrWord, cfg: TextConfig) -> bool:
    text = word.text.strip()
    return (
        word.conf >= cfg.stray_min_conf
        and len(_ALNUM.findall(text)) >= cfg.stray_min_alnum
        and _STRAY_SHAPE.match(text) is not None
        and len(set(text.casefold())) > 1
    )


def _label_match(word: str, ref: str, threshold: float) -> bool:
    """The word reads like (part of) the product's label text on the reference photo."""
    if fuzz.ratio(word, ref) >= threshold:
        return True
    return len(word) <= len(ref) and fuzz.partial_ratio(word, ref) >= max(threshold, 90)


def _stray_words(
    words: list[OcrWord],
    zone_px: tuple[int, int, int, int],
    reference_words: list[OcrWord],
    cfg: TextConfig,
    exclude_px: list[tuple[int, int, int, int]] | None = None,
) -> list[OcrWord]:
    ref_texts = [normalise(w.text) for w in reference_words if normalise(w.text)]
    stray: list[OcrWord] = []
    for word in words:
        if _inside(word, zone_px) or not _stray_candidate(word, cfg):
            continue
        if any(_inside(word, box) for box in exclude_px or []):
            continue  # inside the product: its own label text (the product check covers it)
        norm = normalise(word.text)
        if any(_label_match(norm, ref, cfg.stray_reference_ratio) for ref in ref_texts):
            continue  # the product's own label text, as read on the reference photo
        stray.append(word)
    return stray


# --- error keys and agreement (ev-0.8) ------------------------------------------------------------

# (kind, copy position or -1, glyph or token). kind: chg | ins | extra | missing.
ErrKey = tuple[str, int, str]
_EXTRA_STRIP = " .,:/+-"
# A read whose best window misses more than this fraction of the copy is not a read of the copy
# (an empty or garbage preprocessing); it takes no part in the consistency check.
_GARBAGE_CER = 0.5
TESSERACT = "tesseract"
APPLE_VISION = "apple_vision"
VLM = "vlm"
_READER_LABEL = {TESSERACT: "Tesseract", APPLE_VISION: "Apple Vision", VLM: "the read-back"}


def window_errors(window: str, required_c: str, required_raw: str, script: str) -> set[ErrKey]:
    """Error keys of a matched window against the copy (both normalised)."""
    keys: set[ErrKey] = set()
    for op in Levenshtein.editops(required_c, window):
        if op.tag == "insert":
            keys.add(("ins", op.src_pos, window[op.dest_pos]))
        else:  # replace or delete: the copy character at src_pos is not rendered as written
            keys.add(("chg", op.src_pos, ""))
    for token in missing_tokens(window, required_raw, script):
        keys.add(("missing", -1, token))
    return keys


def string_extras(
    text: str, window: tuple[int, int], required_c: str, script: str, cfg: TextConfig
) -> list[str]:
    """Extra content of a plain zone string (the read-back) outside its matched window."""
    start, end = window
    copy_words = set(required_c.split())
    out: list[str] = []
    for part in (text[:start], text[end:]):
        out += [ch for ch in part if ch in DELIMITERS]
        stripped = "".join(ch for ch in part if ch not in DELIMITERS)
        tokens = [stripped] if script in UNSPACED_SCRIPTS else stripped.split()
        for raw in tokens:
            token = raw.strip(_EXTRA_STRIP)
            if len(_ALNUM.findall(token)) < cfg.zone_extra_min_alnum:
                continue
            is_copy = token in required_c if script in UNSPACED_SCRIPTS else token in copy_words
            if not is_copy:
                out.append(token)
    return out


def _squeeze(text: str) -> str:
    return fold(text).replace(" ", "")


def _same_token(a: str, b: str) -> bool:
    fa, fb = _squeeze(a), _squeeze(b)
    if fa == fb:
        return True
    return min(len(fa), len(fb)) >= 3 and (fa in fb or fb in fa)


def _key_seen(key: ErrKey, others: Iterable[ErrKey]) -> bool:
    kind, pos, val = key
    for o_kind, o_pos, o_val in others:
        if kind == o_kind == "chg" and pos == o_pos:
            return True
        if kind == o_kind == "ins" and fold(val) == fold(o_val) and abs(pos - o_pos) <= 1:
            return True
        if kind == o_kind and kind in ("extra", "missing") and _same_token(val, o_val):
            return True
        if {kind, o_kind} == {"ins", "extra"} and len(val) == len(o_val) == 1:
            if fold(val) == fold(o_val):  # a quote read inside one window, outside the other
                return True
    return False


def agreed_keys(a: set[ErrKey], b: set[ErrKey]) -> set[ErrKey]:
    """The error keys two readers both see."""
    return {k for k in a if _key_seen(k, b)} | {k for k in b if _key_seen(k, a)}


def consistent_keys(sets: Sequence[set[ErrKey]]) -> set[ErrKey]:
    """The error keys every one of an engine's reads shows."""
    if not sets:
        return set()
    out = set(sets[0])
    for other in sets[1:]:
        out = {k for k in out if _key_seen(k, other)}
    return out


def describe_keys(keys: Iterable[ErrKey], required_c: str) -> list[str]:
    out: list[str] = []
    for kind, pos, val in sorted(keys, key=lambda k: (k[1], k[0], k[2])):
        if kind == "chg":
            ch = required_c[pos] if 0 <= pos < len(required_c) else "?"
            out.append(f"'{ch}' at {pos + 1} altered or missing")
        elif kind == "ins":
            out.append(f"extra '{val}' at {pos + 1}")
        elif kind == "extra":
            out.append(f"'{val}' not in the copy")
        else:
            out.append(f"'{val}' missing")
    return out


def read_errors(
    read: ZoneRead,
    required_c: str,
    required_raw: str,
    script: str,
    tight_px: tuple[int, int, int, int],
    cfg: TextConfig,
) -> set[ErrKey] | None:
    """One read's error keys (its own extras), or None for an empty or garbage read."""
    if not read.text.strip():
        return None
    dist, start, end = align(read.text, required_c)
    if required_c and dist / len(required_c) > _GARBAGE_CER:
        return None
    keys = window_errors(read.text[start:end], required_c, required_raw, script)
    extras = zone_extras(read, (start, end), required_c, script, tight_px, cfg)
    return keys | {("extra", -1, t) for t in extras}


def vlm_zone_errors(
    reads: list[TextRead], required_text: str, script: str, zone: NormBox, cfg: TextConfig
) -> tuple[set[ErrKey], str]:
    """The read-back's headline-zone error keys and what it read there."""
    keep = delimiters_to_keep(required_text)
    in_zone = zone_text(reads, zone)
    required_c = compact(required_text, script)
    text = compact(in_zone, script, keep)
    _, start, end = align(text, required_c)
    keys = window_errors(text[start:end], required_c, required_text, script)
    extras = string_extras(text, (start, end), required_c, script, cfg)
    return keys | {("extra", -1, t) for t in extras}, in_zone


# --- engine reads ---------------------------------------------------------------------------------


@dataclass
class EngineZone:
    """One OCR engine's reads of the zone, its best window and its error keys."""

    name: str
    engine: str
    lang: str
    reads: dict[str, list[OcrWord]]
    score: ZoneScore
    errors: set[ErrKey]
    missing: list[str]
    full: list[OcrWord]
    ref: list[OcrWord] = field(default_factory=list[OcrWord])

    @property
    def exact(self) -> bool:
        return not self.errors


def _grow_px(
    box: tuple[int, int, int, int], size: tuple[int, int], frac: float, min_px: int = 0
) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = box
    dx = max(min_px, round((x1 - x0) * frac))
    dy = max(min_px, round((y1 - y0) * frac))
    w, h = size
    return max(0, x0 - dx), max(0, y0 - dy), min(w, x1 + dx), min(h, y1 + dy)


def _scaled(img: Image.Image, box: tuple[int, int, int, int], scale: int) -> Image.Image:
    crop = img.crop(box)
    return resize(crop, (max(1, crop.width * scale), max(1, crop.height * scale)))


async def _zone_reads(
    engine: OcrEngine,
    img: Image.Image,
    *,
    lang: str,
    base_lang: str | None,
    zone_px: tuple[int, int, int, int],
    tight_px: tuple[int, int, int, int],
    scale: int,
) -> tuple[dict[str, list[OcrWord]], OcrResult]:
    """The engine's first-pass zone reads (image coordinates) and its full-image read."""

    def mapped(words: list[OcrWord], box: tuple[int, int, int, int], s: int) -> list[OcrWord]:
        return [wd.shifted(box[0], box[1], s) for wd in words]

    if engine.name == APPLE_VISION:
        rgb = img.convert("RGB")
        zone = await engine.read(_scaled(rgb, zone_px, scale), lang=lang, psm=PSM_BLOCK)
        full = await engine.read(rgb, lang=lang, psm=PSM_SPARSE)
        return {
            "zone": mapped(zone.words, zone_px, scale),
            "full_image_zone": [wd for wd in full.words if _inside(wd, zone_px)],
        }, full
    gray = img.convert("L")
    crop = _scaled(gray, zone_px, scale)
    # The exact zone (no margin), contrast-stretched: band vs glyphs only. Tesseract's Devanagari
    # model misreads black-on-mid-tone bands that it reads exactly once they are stretched.
    tight = ImageOps.autocontrast(img.crop(tight_px).convert("L"))
    tight = resize(tight, (tight.width * scale, tight.height * scale))
    first = await engine.read(crop, lang=lang, psm=PSM_BLOCK)
    inverted = await engine.read(ImageOps.invert(crop), lang=lang, psm=PSM_BLOCK)
    stretched = await engine.read(tight, lang=lang, psm=PSM_BLOCK)
    # The market's packs come first (accents), but a mixed pack can misread symbols the script's
    # own pack gets right (nor+eng reads "°" as "*"): read the zone with the script's default pack
    # too and keep the best window, like the min over engines in §5.2.
    base = (
        await engine.read(crop, lang=base_lang, psm=PSM_BLOCK)
        if base_lang is not None and base_lang != lang
        else None
    )
    full = await engine.read(gray, lang=lang, psm=PSM_SPARSE)
    reads = {
        "zone": mapped(first.words, zone_px, scale),
        "zone_inverted": mapped(inverted.words, zone_px, scale),
        "full_image_zone": [wd for wd in full.words if _inside(wd, zone_px)],
        "zone_tight": mapped(stretched.words, tight_px, scale),
    }
    if base is not None:
        reads[f"zone_{base.lang}"] = mapped(base.words, zone_px, scale)
    return reads, full


async def _zone_rereads(
    engine: OcrEngine,
    img: Image.Image,
    *,
    lang: str,
    zone_px: tuple[int, int, int, int],
    tight_px: tuple[int, int, int, int],
    big: int,
) -> dict[str, list[OcrWord]]:
    """The higher-resolution re-read: plain, inverted (Tesseract) and contrast-stretched."""

    def mapped(words: list[OcrWord], box: tuple[int, int, int, int]) -> list[OcrWord]:
        return [wd.shifted(box[0], box[1], big) for wd in words]

    if engine.name == APPLE_VISION:
        rgb = img.convert("RGB")
        zone = await engine.read(_scaled(rgb, zone_px, big), lang=lang, psm=PSM_BLOCK)
        tight = ImageOps.autocontrast(img.crop(tight_px).convert("L"))
        tight = resize(tight, (tight.width * big, tight.height * big))
        stretched = await engine.read(tight, lang=lang, psm=PSM_BLOCK)
        return {
            "reread_zone": mapped(zone.words, zone_px),
            "reread_zone_tight": mapped(stretched.words, tight_px),
        }
    zone_big = _scaled(img.convert("L"), zone_px, big)
    tight_big = ImageOps.autocontrast(img.crop(tight_px).convert("L"))
    tight_big = resize(tight_big, (tight_big.width * big, tight_big.height * big))
    first = await engine.read(zone_big, lang=lang, psm=PSM_BLOCK)
    inverted = await engine.read(ImageOps.invert(zone_big), lang=lang, psm=PSM_BLOCK)
    stretched = await engine.read(tight_big, lang=lang, psm=PSM_BLOCK)
    return {
        "reread_zone": mapped(first.words, zone_px),
        "reread_zone_inverted": mapped(inverted.words, zone_px),
        "reread_zone_tight": mapped(stretched.words, tight_px),
    }


async def _region_texts(
    engine: OcrEngine, img: Image.Image, box: tuple[int, int, int, int], lang: str, big: int
) -> list[str]:
    """Every word, and each line, an engine reads in a region at `big`x (stray confirmation)."""
    region = _grow_px(box, img.size, 0.5, min_px=12)
    if engine.name == APPLE_VISION:
        result = await engine.read(_scaled(img.convert("RGB"), region, big), lang=lang, psm=0)
    else:
        result = await engine.read(_scaled(img.convert("L"), region, big), lang=lang, psm=11)
    lines = [" ".join(w.text for w in line) for line in reading_order(result.words)]
    return [w.text for w in result.words] + lines


# --- stray text -----------------------------------------------------------------------------------


@dataclass
class _Stray:
    source: str
    text: str
    box: tuple[float, float, float, float]  # normalised x0, y0, x1, y1
    word: OcrWord | None = None
    agreed_with: set[str] = field(default_factory=set[str])
    status: str = ""  # fail | unverified | note

    def px(self, size: tuple[int, int]) -> tuple[int, int, int, int]:
        w, h = size
        x0, y0, x1, y1 = self.box
        return round(x0 * w), round(y0 * h), round(x1 * w), round(y1 * h)


def _text_match(a: str, b: str, ratio: float) -> bool:
    na, nb = _squeeze(normalise(a)), _squeeze(normalise(b))
    if not na or not nb:
        return False
    if min(len(na), len(nb)) >= 4 and (na in nb or nb in na):
        return True
    return fuzz.ratio(na, nb) >= ratio


def _near(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    g = 0.02
    return not (
        a[2] + g < b[0] - g or b[2] + g < a[0] - g or a[3] + g < b[1] - g or b[3] + g < a[1] - g
    )


def _ocr_stray(source: str, word: OcrWord, size: tuple[int, int]) -> _Stray:
    w, h = size
    box = (word.left / w, word.top / h, (word.left + word.width) / w, (word.top + word.height) / h)
    return _Stray(source, word.text, box, word)


def _vlm_stray(read: TextRead) -> _Stray:
    ymin, xmin, ymax, xmax = (v / 1000 for v in (read.box_2d or [0, 0, 0, 0]))
    return _Stray(VLM, read.text, (xmin, ymin, xmax, ymax))


# --- orchestration --------------------------------------------------------------------------------


def _unverified(reason: str) -> tuple[list[CheckResult], DimensionResult]:
    check = CheckResult(dimension="text", name="ocr", passed=None, evidence=reason)
    dim = _dimension("text", [check], score=0.0, low_confidence=True)
    return [check], dim


def _box_list(words: list[OcrWord]) -> list[dict[str, object]]:
    return [
        {"text": w.text, "conf": round(w.conf, 1), "box": [w.left, w.top, w.width, w.height]}
        for w in words
    ]


def _label_words(ref_words: list[OcrWord], labels: Sequence[str]) -> list[OcrWord]:
    """The reference photo's OCR words plus the profile's label strings (as pseudo-words)."""
    extra = [OcrWord(text=t, conf=100, left=0, top=0, width=1, height=1) for t in labels if t]
    return [*ref_words, *extra]


async def evaluate_text(
    img: Image.Image,
    reference: Image.Image | None,
    *,
    required_text: str,
    script: str,
    zone: NormBox,
    market_langs: tuple[str, ...],
    text_mode: str,
    ocr: OcrEngine | None,
    cfg: TextConfig,
    readback: TextReadback | None = None,
    image_bytes: bytes = b"",
    product_boxes: list[NormBox] | None = None,
    reference_labels: tuple[str, ...] = (),
    lines: tuple[str, ...] = (),
    ocr2: OcrEngine | None = None,
    product_missing: bool = False,
) -> tuple[list[CheckResult], DimensionResult]:
    """The text dimension (module docstring). `ocr` is Tesseract, `ocr2` Apple Vision (either may
    be None or unavailable). `product_boxes` (from the vision judge) and `reference_labels` (the
    profile's label text) exclude the product's own label text from every stray check;
    `product_missing` (the judge found no instance of the product) turns an unconfirmed read-back
    stray into a note. `lines` is kept for the pipeline's call signature."""
    del lines
    if text_mode != "native":
        check = CheckResult(
            dimension="text",
            name="overlay_pending",
            passed=False,
            evidence=(
                "The text is overlay-only (flagged input): it was not sent to the image model "
                "and the deterministic overlay has not been applied yet."
            ),
        )
        return [check], _dimension("text", [check], score=0.0)
    engines: list[tuple[OcrEngine, str]] = []
    for engine in (ocr, ocr2):
        if engine is None or not engine.available():
            continue
        lang = engine.lang_for(script, market_langs)
        if lang is not None:
            engines.append((engine, lang))
    if not engines:
        if (ocr is None or not ocr.available()) and (ocr2 is None or not ocr2.available()):
            return _unverified("OCR is unavailable; the text can't be verified.")
        return _unverified(f"No OCR language pack for script {script}; text not verified.")

    w, h = img.size
    size = (w, h)
    zone_px = zone.expanded(cfg.zone_margin).to_pixels(w, h)
    tight_px = zone.to_pixels(w, h)
    scale = max(1, cfg.zone_upscale)
    big = max(scale + 1, cfg.reread_upscale)
    required_c = compact(required_text, script)
    keep = delimiters_to_keep(required_text)
    zones: list[EngineZone] = []
    try:
        for engine, lang in engines:
            base_lang = engine.lang_for(script, ()) if engine.name == TESSERACT else None
            reads, full = await _zone_reads(
                engine,
                img,
                lang=lang,
                base_lang=base_lang,
                zone_px=zone_px,
                tight_px=tight_px,
                scale=scale,
            )
            ref = (
                (await engine.read(reference.convert("L"), lang=lang, psm=PSM_SPARSE)).words
                if reference is not None
                else []
            )
            score = score_reads(reads, required_c, script, keep, tight_px, cfg)
            missing = missing_tokens(score.window_text, required_text, script)
            errors = window_errors(score.window_text, required_c, required_text, script)
            errors |= {("extra", -1, t) for t in score.extras}
            zones.append(
                EngineZone(
                    engine.name, full.engine, lang, reads, score, errors, missing, full.words, ref
                )
            )
    except Exception as exc:  # noqa: BLE001 - an OCR failure is "unverified", never a pass
        return _unverified(f"OCR failed ({type(exc).__name__}); the text can't be verified.")

    # The read-back (third reader).
    vlm_reads: list[TextRead] | None = None
    if readback is not None:
        try:
            vlm_reads = await readback.read(image_bytes)
        except Exception:  # noqa: BLE001 - a failed read-back adds nothing
            vlm_reads = None
    vlm_errors: set[ErrKey] | None = None
    vlm_zone = ""
    if vlm_reads is not None:
        vlm_errors, vlm_zone = vlm_zone_errors(vlm_reads, required_text, script, zone, cfg)

    # --- headline zone decision
    def agreement(sets: dict[str, set[ErrKey]]) -> dict[ErrKey, set[str]]:
        seen: dict[ErrKey, set[str]] = {}
        names = list(sets)
        for i, a in enumerate(names):
            for b in names[i + 1 :]:
                if a == VLM and b == VLM:
                    continue
                for key in agreed_keys(sets[a], sets[b]):
                    seen.setdefault(key, set()).update({a, b})
        return seen

    first_sets: dict[str, set[ErrKey]] = {z.name: z.errors for z in zones}
    if vlm_errors is not None:
        first_sets[VLM] = vlm_errors
    agreed = agreement(first_sets)
    readers = len(first_sets)
    reread: dict[str, object] | None = None
    if agreed:
        status, path = "fail", "agreed"
    elif not any(first_sets.values()):
        status, path = "pass", "all_exact"
    elif readers < 2:
        status, path = "fail", "single_reader"
        agreed = {k: {zones[0].name} for k in zones[0].errors}
    else:
        try:
            consistent: dict[str, set[ErrKey]] = {}
            for (engine, lang), zr in zip(engines, zones, strict=True):
                again = await _zone_rereads(
                    engine, img, lang=lang, zone_px=zone_px, tight_px=tight_px, big=big
                )
                every = {**zr.reads, **again}
                sets = [
                    k
                    for k in (
                        read_errors(
                            zone_read(src, wds, script, keep),
                            required_c,
                            required_text,
                            script,
                            tight_px,
                            cfg,
                        )
                        for src, wds in every.items()
                    )
                    if k is not None
                ]
                consistent[zr.name] = consistent_keys(sets) if sets else set(zr.errors)
                zr.reads = every
        except Exception as exc:  # noqa: BLE001 - an OCR failure is "unverified", never a pass
            return _unverified(f"OCR failed ({type(exc).__name__}); the text can't be verified.")
        second = dict(consistent)
        if vlm_errors is not None:
            second[VLM] = vlm_errors
        agreed = agreement(second)
        if agreed:
            status, path = "fail", "reread_agreed"
        elif not any(consistent.values()):
            status, path = "pass", "reread_resolved"
        else:
            status, path = "unverified", "reread_disagrees"
        reread = {
            "upscale": big,
            "still_seen": {n: describe_keys(k, required_c) for n, k in consistent.items() if k},
            "outcome": path,
        }

    # --- stray text
    exclude_px = [b.expanded(cfg.product_box_margin).to_pixels(w, h) for b in product_boxes or []]
    ref_pool = [wd for z in zones for wd in z.ref]
    strays: list[_Stray] = []
    for z in zones:
        words = _stray_words(
            z.full, zone_px, _label_words(ref_pool, reference_labels), cfg, exclude_px
        )
        strays += [_ocr_stray(z.name, wd, size) for wd in words]
    if vlm_reads is not None:
        margin_zone = zone.expanded(0.05)
        strays += [
            _vlm_stray(r)
            for r in vlm_strays(
                vlm_reads,
                margin_zone,
                product_boxes or [],
                reference_labels,
                cfg.stray_min_alnum,
                cfg.stray_reference_ratio,
                cfg.product_box_margin,
            )
        ]
    for s in strays:
        for o in strays:
            if o.source != s.source and _near(s.box, o.box):
                if _text_match(s.text, o.text, cfg.stray_reference_ratio):
                    s.agreed_with.add(o.source)
    try:
        by_name = {e.name: (e, lang) for e, lang in engines}
        for s in strays:
            if s.agreed_with:
                s.status = "fail"
                continue
            others = [n for n in by_name if n != s.source]
            if s.source == VLM and product_missing:
                s.status = "note"
                continue
            if s.source != VLM and not others and vlm_reads is None:
                s.status = "fail"  # a single reader: its stray words stand (pre-ev-0.8 rule)
                s.agreed_with.add(s.source)
                continue
            for name in others:
                engine, lang = by_name[name]
                texts = await _region_texts(engine, img, s.px(size), lang, big)
                if any(_text_match(s.text, t, cfg.stray_reference_ratio) for t in texts):
                    s.agreed_with.add(name)
            if s.agreed_with:
                s.status = "fail"
            else:
                s.status = "unverified" if s.source == VLM else "note"
    except Exception as exc:  # noqa: BLE001 - an OCR failure is "unverified", never a pass
        return _unverified(f"OCR failed ({type(exc).__name__}); the text can't be verified.")

    # --- checks
    primary = zones[0]
    best = min(zones, key=lambda z: (z.score.cer, zones.index(z)))
    shown_zone = best if status == "pass" else primary
    shown = shown_zone.score.best.raw or "(nothing legible)"
    agreed_desc = describe_keys(agreed, required_c)
    extras = sorted({k[2] for k in agreed if k[0] == "extra"})
    missing = sorted({k[2] for k in agreed if k[0] == "missing"})
    reads_by_engine = {z.name: z.score.best.raw for z in zones}
    who = sorted({src for srcs in agreed.values() for src in srcs})
    extra_note = (
        " Not in the required text: " + ", ".join(f"'{t}'" for t in extras) + "." if extras else ""
    )
    evidence = f"Rendered «{shown}»; required «{required_text}».{extra_note}"
    others_read = "; ".join(
        f"{_READER_LABEL.get(z.name, z.name)} read «{z.score.best.raw or '(nothing)'}»"
        for z in zones
        if z is not shown_zone
    )
    if others_read:
        evidence += f" {others_read}."
    if status == "fail" and path != "single_reader":
        evidence += (
            " Seen by "
            + " and ".join(_READER_LABEL.get(s, s) for s in who)
            + ": "
            + "; ".join(agreed_desc[:4])
            + "."
        )
    elif status == "unverified":
        evidence += (
            f" Re-read at {big}x: the readers still disagree and none confirms the other; "
            "unverified, not a fail."
        )
    elif path == "reread_resolved":
        evidence = (
            f"Rendered «{shown}»; required «{required_text}». A single reader's difference "
            f"was not seen again at {big}x by every read of that engine, and no other reader "
            "saw it."
        )
    cer_value = min(z.score.cer for z in zones)
    cer_data: dict[str, object] = {
        "zone_box": list(zone_px),
        "words": _box_list(shown_zone.reads.get(shown_zone.score.best.source, [])),
        "source": f"{shown_zone.name}:{shown_zone.score.best.source}",
        "engine": primary.engine,
        "engines": [z.engine for z in zones],
        "lang": primary.lang,
        "window_cer": round(shown_zone.score.window_cer, 4),
        "reads": reads_by_engine,
        "decision": path,
        "agreed": agreed_desc,
        "agreed_by": who,
    }
    if extras:
        cer_data["extra"] = extras
        words = [primary.score.extra_words[t] for t in extras if t in primary.score.extra_words]
        if words:
            cer_data["extra_words"] = _box_list(words)
    if reread is not None:
        cer_data["reread"] = reread
    cer_passed: bool | None = {"pass": True, "fail": False}.get(status)
    checks = [
        CheckResult(
            dimension="text",
            name="ocr_cer",
            passed=cer_passed,
            value=round(cer_value, 4),
            threshold=cfg.cer_max,
            evidence=evidence,
            data=cer_data,
        ),
        CheckResult(
            dimension="text",
            name="critical_tokens",
            passed=(
                False
                if missing
                else (None if status == "unverified" and any(z.missing for z in zones) else True)
            ),
            value=float(len(missing)),
            threshold=0.0,
            evidence=("Missing or altered: " + ", ".join(f"'{t}'" for t in missing))
            if missing
            else "",
        ),
    ]
    ocr_strays = [s for s in strays if s.source != VLM]
    failing = [s for s in ocr_strays if s.status == "fail"]
    noted = [s for s in ocr_strays if s.status == "note"]
    stray_data: dict[str, object] = {}
    if failing:
        stray_data["words"] = _box_list([s.word for s in failing if s.word is not None])
        stray_data["agreed_by"] = {s.text: sorted({s.source, *s.agreed_with}) for s in failing}
    if noted:
        stray_data["dismissed"] = [f"{s.source}:{s.text}" for s in noted]
        stray_data["note"] = True
    checks.append(
        CheckResult(
            dimension="text",
            name="stray_text",
            passed=not failing,
            value=float(len(failing)),
            threshold=0.0,
            evidence=(
                "Text outside the headline zone: " + ", ".join(f"«{s.text}»" for s in failing)
                if failing
                else (
                    "Note (not a failure): one OCR engine read "
                    + ", ".join(f"«{s.text}»" for s in noted)
                    + "; neither the other engine nor the read-back sees it."
                    if noted
                    else ""
                )
            ),
            data=stray_data or None,
        )
    )
    if vlm_errors:
        corroborated = bool(agreed) and VLM in who
        checks.append(
            CheckResult(
                dimension="text",
                name="vlm_readback",
                method="vlm",
                passed=False if corroborated else True,
                evidence=(
                    f"Vision read-back saw «{vlm_zone or '(no text)'}» in the headline zone."
                    + ("" if corroborated else " Not seen by any OCR engine (a note).")
                ),
                data={
                    "reads": [r.model_dump() for r in vlm_reads or []],
                    "note": not corroborated,
                },
            )
        )
    vlm_found = [s for s in strays if s.source == VLM]
    if vlm_found:
        bad = [s for s in vlm_found if s.status == "fail"]
        unsure = [s for s in vlm_found if s.status == "unverified"]
        notes = [s for s in vlm_found if s.status == "note"]
        vlm_passed: bool | None = False if bad else (None if unsure else True)
        shown_strays = bad or unsure or notes
        stray_evidence = "Vision read-back saw text outside the headline zone: " + ", ".join(
            f"«{s.text}»" for s in shown_strays
        )
        if not bad and unsure:
            stray_evidence += (
                f". No OCR engine confirms it at {big}x (it may be stylised or embossed): "
                "unverified, needs review."
            )
        elif not bad:
            stray_evidence += (
                ". The product was not located, so its label text can't be told from stray "
                "text; the product check fails the image (a note)."
            )
        checks.append(
            CheckResult(
                dimension="text",
                name="vlm_stray_text",
                method="vlm",
                passed=vlm_passed,
                evidence=stray_evidence,
                data={
                    "reads": [{"text": s.text, "box": list(s.box)} for s in shown_strays],
                    "agreed_by": {s.text: sorted(s.agreed_with) for s in bad},
                    "note": vlm_passed is True,
                },
            )
        )
    hint_parts = [f"rendered «{shown}»; required «{required_text}»"]
    if extras:
        hint_parts.append("remove from the headline " + ", ".join(f"'{t}'" for t in extras))
    if missing:
        hint_parts.append("missing token " + ", ".join(f"'{t}'" for t in missing))
    all_failing = [s for s in strays if s.status == "fail"]
    if all_failing:
        hint_parts.append("remove stray text " + ", ".join(f"«{s.text}»" for s in all_failing))
    rtl = script in RTL_SCRIPTS
    zone_reads = {
        compact(text_of(primary.reads[s], rtl=rtl), script) for s in ("zone", "full_image_zone")
    }
    dim = _dimension(
        "text",
        checks,
        score=max(0.0, 1.0 - min(cer_value, 1.0)),
        low_confidence=len(zone_reads) > 1 or status == "unverified",
        signals={
            "text_cer": round(cer_value, 4),
            "best_read": shown_zone.score.best.raw,
            "best_source": f"{shown_zone.name}:{shown_zone.score.best.source}",
            "reads": reads_by_engine,
            "decision": path,
            "agreed_errors": agreed_desc,
            "agreed_by": who,
            "zone_extra": extras,
            "critical_tokens_missing": missing,
            "stray_tokens": [s.text for s in all_failing],
            "stray_unverified": [s.text for s in strays if s.status == "unverified"],
            "stray_notes": [f"{s.source}:{s.text}" for s in strays if s.status == "note"],
            "vlm_zone": vlm_zone if vlm_reads is not None else None,
            "vlm_zone_errors": describe_keys(vlm_errors or set(), required_c),
            "vlm_strays": [s.text for s in vlm_found],
            "ocr_reread": reread,
            "ocr_engine": primary.engine,
            "ocr_engines": [z.engine for z in zones],
            "ocr_lang": primary.lang,
        },
        repair_hint="; ".join(hint_parts),
    )
    if dim.passed:
        dim.repair_hint = None
    return checks, dim
