"""Evaluator: technical checks, text normalisation, alignment, critical tokens and text fidelity.

The live-OCR tests render synthetic ads with Pillow (correct text vs a typo'd copy) and read them
with the local Tesseract binary. They are skipped, with a reason, only when Tesseract is missing;
every logic test runs regardless.
"""

import io
from dataclasses import dataclass, field

import pytest
from PIL import Image, ImageDraw

from backend.domain.adstudio.evaluator.config import load_config
from backend.domain.adstudio.evaluator.core import EvalTarget, Evaluator
from backend.domain.adstudio.evaluator.ocr import AppleVisionOcr, OcrResult, OcrWord, TesseractOcr
from backend.domain.adstudio.evaluator.readback import TextRead
from backend.domain.adstudio.evaluator.technical import decode_for_checks, technical_checks
from backend.domain.adstudio.evaluator.text import align, cer, has_token, missing_tokens
from backend.domain.adstudio.fonts import load_font
from backend.domain.adstudio.image_clients import FakeScene, render_fake_ad
from backend.domain.adstudio.imaging import postprocess_generated
from backend.domain.adstudio.layout import break_lines, default_zone
from backend.domain.adstudio.textnorm import (
    compact,
    critical_tokens,
    delimiters_to_keep,
    detect_script,
    fold,
    normalise,
)

TESSERACT = TesseractOcr()
needs_tesseract = pytest.mark.skipif(
    not TESSERACT.available(), reason="tesseract binary not found on PATH (live-OCR test)"
)
APPLE = AppleVisionOcr()
# ev-0.8: the live text tests run with Tesseract alone and with the Tesseract + Apple Vision
# ensemble (macOS only): real defects must fail under both.
ENGINES = pytest.mark.parametrize(
    "ocr2",
    [
        pytest.param(None, id="tesseract"),
        pytest.param(
            APPLE,
            id="tesseract+apple_vision",
            marks=pytest.mark.skipif(not APPLE.available(), reason="Apple Vision is macOS only"),
        ),
    ],
)
ZONE = default_zone("4:5")
B01 = "Summer Sale — 30% OFF"


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _product() -> bytes:
    """A plain, label-free product (so no product text can be mistaken for stray text)."""
    img = Image.new("RGB", (400, 520), (235, 235, 230))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((120, 60, 280, 480), radius=40, fill=(200, 30, 45))
    draw.rectangle((170, 20, 230, 70), fill=(60, 60, 60))
    return _png(img)


def synthetic_ad(text: str, *, stray: str | None = None, aspect: str = "4:5") -> bytes:
    """What the image model would return, run through the real post-processing (<= 1024 px)."""
    zone = default_zone(aspect)
    scene = FakeScene(
        palette=("#F4D35E", "#EE964B"),
        headline_lines=tuple(break_lines(text)),
        zone=(zone.x0, zone.y0, zone.x1, zone.y1),
        band_color="#0D3B66",
        text_color="#FFFFFF",
    )
    img = render_fake_ad(_product(), aspect, scene, seed="fixture")
    if stray:
        draw = ImageDraw.Draw(img)
        draw.rectangle((40, 900, 420, 1000), fill=(250, 250, 250))
        draw.text((60, 915), stray, font=load_font(56), fill=(10, 10, 10))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return postprocess_generated(buf.getvalue()).data


def target(text: str = B01, **kw: object) -> EvalTarget:
    return EvalTarget(
        aspect_ratio="4:5",
        required_text=text,
        lines=tuple(break_lines(text)),
        text_zone=ZONE,
        script=detect_script(text),
        **kw,  # type: ignore[arg-type]
    )


# --- technical ------------------------------------------------------------------------------------


def _checks(data: bytes, aspect: str = "4:5", reference: bytes | None = None) -> dict[str, bool]:
    img = decode_for_checks(data)
    ref = decode_for_checks(reference) if reference else None
    return {
        c.name: bool(c.passed) for c in technical_checks(img, aspect, load_config().technical, ref)
    }


def test_technical_checks() -> None:
    good = synthetic_ad(B01)
    assert all(_checks(good, reference=_product()).values())

    blank = _png(Image.new("RGB", (820, 1024), (128, 128, 128)))
    assert _checks(blank)["not_blank"] is False
    assert _checks(blank)["not_placeholder"] is False

    oversize = Image.open(io.BytesIO(good)).resize((1640, 2048))
    assert _checks(_png(oversize))["resolution"] is False

    wrong_aspect = Image.open(io.BytesIO(good)).crop((0, 0, 825, 825))
    assert _checks(_png(wrong_aspect))["aspect"] is False
    assert _checks(_png(wrong_aspect), aspect="1:1")["aspect"] is True

    truncated = good[: len(good) // 2]
    assert _checks(truncated) == {"decodes": False}

    # "Returned the reference": the ad is the product photo itself.
    ref = Image.open(io.BytesIO(good))
    assert _checks(good, reference=_png(ref))["not_copy"] is False


async def test_technical_failure_short_circuits_text() -> None:
    blank = _png(Image.new("RGB", (820, 1024), (0, 0, 0)))
    ev = await Evaluator(_ScriptedOcr({})).evaluate(blank, None, target())
    assert ev.verdict == "fail"
    assert set(ev.dimensions) == {"technical"}  # text is not judged on a broken image


# --- normalisation, alignment, tokens -------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Summer Sale — 30% OFF", "summer sale - 30% off"),
        ("Summer  Sale – 30%  off!", "summer sale - 30% off"),
        ("“Merry Christmas, Mate!”", "merry christmas, mate"),
        ("Built for −20°C", "built for -20°c"),
        ("Built for −20℃", "built for -20°c"),  # NFKC folds the degree-Celsius sign
        ("Holiday Deals from $19.90", "holiday deals from $19.90"),
        ("Nouveau: Éclat 24h", "nouveau: éclat 24h"),  # accents are kept (they are the text)
        ("New Season.\nNew Pace.", "new season. new pace."),
        ("Stay Cool. Only AED 49", "stay cool. only aed 49"),
        ("Valentine's Day", "valentines day"),
        ("ＳＡＬＥ　５０％", "sale 50%"),  # full-width forms
        ("  Straße  ", "strasse"),
    ],
)
def test_textnorm_cases(raw: str, expected: str) -> None:
    assert normalise(raw) == expected


def test_textnorm_scripts_and_compact() -> None:
    assert detect_script("Summer Sale") == "Latn"
    assert detect_script("春の新作") == "Jpan"
    assert detect_script("新品上市") == "Hani"
    assert detect_script("दिवाली सेल 20% छूट") == "Deva"
    assert detect_script("겨울 세일") == "Kore"
    assert compact("春 の 新 作", "Jpan") == "春の新作"  # OCR spaces vanish for unspaced scripts
    assert compact("겨울 세일", "Kore") == "겨울 세일"


@pytest.mark.parametrize(
    ("read", "required", "expected_cer", "window"),
    [
        ("summer sale - 30% off", "summer sale - 30% off", 0.0, "summer sale - 30% off"),
        # Extra words around the headline don't count against the window (the evaluator counts
        # them separately as zone extras, see test_text_fails_on_extra_zone_words).
        ("new! summer sale - 30% off today", "summer sale - 30% off", 0.0, "summer sale - 30% off"),
        ("summr sale - 30% off", "summer sale - 30% off", 1 / 21, "summr sale - 30% off"),
        ("summer sale - 38% off", "summer sale - 30% off", 1 / 21, "summer sale - 38% off"),
        ("summer sael - 30% off", "summer sale - 30% off", 2 / 21, "summer sael - 30% off"),
        ("", "summer", 1.0, ""),
        ("xyz", "summer", 1.0, None),
    ],
)
def test_cer_alignment(read: str, required: str, expected_cer: float, window: str | None) -> None:
    value, got_window = cer(read, required)
    assert value == pytest.approx(expected_cer)
    if window is not None:
        assert got_window == window
    dist, start, end = align(read, required)
    assert 0 <= start <= end <= len(read)
    assert dist == round(expected_cer * len(required))


def test_critical_tokens() -> None:
    assert critical_tokens("Summer Sale — 30% OFF") == ["30%", "off"]
    assert critical_tokens("Holiday Deals from $19.90") == ["$19.90"]
    assert critical_tokens("Built for −20°C") == ["-20"]
    assert critical_tokens("Stay Cool. Only AED 49") == ["49", "aed"]
    assert critical_tokens("4th of July — 25% OFF") == ["4", "25%", "off"]
    assert critical_tokens("Nouveau: Éclat 24h") == ["24"]
    assert critical_tokens("Winter Warmers") == []

    # A one-character change in a price or percentage is caught even when CER is small.
    assert missing_tokens("summer sale - 38% off", "Summer Sale — 30% OFF", "Latn") == ["30%"]
    assert missing_tokens("summer sale - 130% off", "Summer Sale — 30% OFF", "Latn") == ["30%"]
    assert missing_tokens("holiday deals from $19.60", "Holiday Deals from $19.90", "Latn") == [
        "$19.90"
    ]
    assert missing_tokens("summer sale - 30% of", "Summer Sale — 30% OFF", "Latn") == ["off"]
    assert missing_tokens("nouveau: éclat 24h", "Nouveau: Éclat 24h", "Latn") == []
    assert has_token("4th of july", "4") and not has_token("14th of july", "4")


# --- text fidelity with live Tesseract ------------------------------------------------------------


@needs_tesseract
@pytest.mark.parametrize(
    "typo",
    [
        "Summer Sale — 38% OFF",  # digit substitution in a critical token
        "Sumer Sale — 30% OFF",  # deletion
        "Summer Slae — 30% OFF",  # transposition
        "Summer Sale — 30% OF",  # truncated all-caps token
    ],
)
@ENGINES
async def test_evaluator_flags_text_typos(typo: str, ocr2: AppleVisionOcr | None) -> None:
    from backend.domain.adstudio.vision import ContextCheck, FakeVisionClient

    rubric = (ContextCheck(id="ctx.x", question="?", severity="must", pass_on="yes"),)
    evaluator = Evaluator(TESSERACT, vision=FakeVisionClient(), ocr2=ocr2)
    clean = await evaluator.evaluate(synthetic_ad(B01), _product(), target(context_checks=rubric))
    bad = await evaluator.evaluate(synthetic_ad(typo), _product(), target(context_checks=rubric))

    assert clean.dimensions["text"].passed is True, clean.dimensions["text"].reasons
    assert clean.verdict == "pass"
    text = bad.dimensions["text"]
    assert text.passed is False
    assert "ocr_cer" in text.failed_checks
    assert text.signals["text_cer"] > 0
    assert text.repair_hint and "required «Summer Sale — 30% OFF»" in text.repair_hint
    assert bad.verdict == "fail"


@needs_tesseract
@ENGINES
async def test_evaluator_flags_stray_text(ocr2: AppleVisionOcr | None) -> None:
    ev = await Evaluator(TESSERACT, ocr2=ocr2).evaluate(
        synthetic_ad(B01, stray="SALEE XQZ"), _product(), target()
    )
    text = ev.dimensions["text"]
    assert text.passed is False and "stray_text" in text.failed_checks
    assert "SALEE" in " ".join(text.signals["stray_tokens"])
    assert "ocr_cer" not in text.failed_checks  # the headline itself was right


@needs_tesseract
@pytest.mark.parametrize(
    ("shown", "glyphs"),
    [
        ("«Summer Sale — 30% OFF»", ["«", "»"]),  # what ad_generate v2 made Gemini draw
        ("“Summer Sale — 30% OFF”", ["“", "”"]),
        ("(Summer Sale — 30% OFF)", ["(", ")"]),
    ],
)
@ENGINES
async def test_text_fails_on_rendered_delimiters(
    shown: str, glyphs: list[str], ocr2: AppleVisionOcr | None
) -> None:
    evaluator = Evaluator(TESSERACT, ocr2=ocr2)
    plain = await evaluator.evaluate(synthetic_ad(B01), _product(), target())
    assert plain.dimensions["text"].passed is True, plain.dimensions["text"].reasons

    wrapped = await evaluator.evaluate(synthetic_ad(shown), _product(), target())
    text = wrapped.dimensions["text"]
    assert text.passed is False and "ocr_cer" in text.failed_checks
    cer_check = next(c for c in wrapped.checks if c.name == "ocr_cer")
    # The words match (window CER 0); the delimiters are the extra characters.
    assert cer_check.data and cer_check.data["window_cer"] == 0.0
    assert cer_check.value and cer_check.value > 0  # never counted as shipped exact text
    assert all(f"'{g}'" in (cer_check.evidence or "") for g in glyphs), cer_check.evidence
    if ocr2 is None:
        assert text.signals["zone_extra"] == glyphs
    else:  # the engines may read a curly quote as a straight one: both are reported
        assert {fold(g) for g in glyphs} == {fold(g) for g in text.signals["zone_extra"]}
    assert text.repair_hint and "remove from the headline" in text.repair_hint

    # A copy that contains quotes itself passes when they are drawn as written.
    quoted = '"Best" Deal 20% OFF'
    ev = await evaluator.evaluate(synthetic_ad(quoted), _product(), target(quoted))
    assert ev.dimensions["text"].passed is True, ev.dimensions["text"].reasons


@needs_tesseract
@pytest.mark.parametrize(
    ("shown", "extra"),
    [("NEW Summer Sale — 30% OFF", "new"), ("Summer Sale — 30% OFF today", "today")],
)
@ENGINES
async def test_text_fails_on_extra_zone_words(
    shown: str, extra: str, ocr2: AppleVisionOcr | None
) -> None:
    ev = await Evaluator(TESSERACT, ocr2=ocr2).evaluate(synthetic_ad(shown), _product(), target())
    text = ev.dimensions["text"]
    assert text.passed is False and "ocr_cer" in text.failed_checks
    assert text.signals["zone_extra"] == [extra]
    cer_check = next(c for c in ev.checks if c.name == "ocr_cer")
    assert f"Not in the required text: '{extra}'" in (cer_check.evidence or "")
    assert "stray_text" not in text.failed_checks  # inside the zone: a headline error, not stray


def _word(text: str, x: int, conf: float = 95) -> OcrWord:
    # top=100 in the 2x zone crop: the centre lies inside the exact zone for every read.
    return OcrWord(text=text, conf=conf, left=x, top=100, width=150, height=60)


async def test_zone_extras_tolerate_ocr_noise() -> None:
    """Scripted OCR (every zone read returns the same words): confident extra tokens fail, while
    low-confidence or one-character noise does not (B01's `Li 12` foliage read was conf 57/28)."""
    ad = synthetic_ad(B01)
    base = [_word(t, 40 + i * 160) for i, t in enumerate(B01.split())]
    x = 40 + len(base) * 160

    async def text_of_zone(words: list[OcrWord]) -> tuple[bool | None, list[str]]:
        ev = await Evaluator(_ScriptedOcr({"zone": words})).evaluate(ad, None, target())
        return ev.dimensions["text"].passed, ev.dimensions["text"].signals.get("zone_extra", [])

    assert await text_of_zone(base) == (True, [])
    assert await text_of_zone([*base, _word("XQ7", x)]) == (False, ["xq7"])
    assert await text_of_zone([*base, _word("Li", x, conf=57), _word("12", x + 160, conf=28)]) == (
        True,
        [],
    )
    assert await text_of_zone([*base, _word("x", x)]) == (True, [])  # < zone_extra_min_alnum
    assert await text_of_zone([_word("«Summer", 40), *base[1:-1], _word("OFF»", x)]) == (
        False,
        ["«", "»"],
    )
    # A repeated word of the copy is not reported as foreign content.
    assert await text_of_zone([*base, _word("Sale", x)]) == (True, [])


async def test_drawn_delimiters_fail_when_ocr_and_readback_agree() -> None:
    """ev-0.8: the read-back's «…» counts when an OCR engine sees it too; alone it is a note."""
    drawn = [_word(t, 40 + i * 160) for i, t in enumerate("«Summer Sale — 30% OFF»".split())]
    agreed = await Evaluator(
        _ScriptedOcr({"zone": drawn}),
        readback=_Readback("«Summer Sale — 30% OFF»"),
    ).evaluate(synthetic_ad(B01), None, target())
    assert agreed.dimensions["text"].passed is False
    assert "vlm_readback" in agreed.dimensions["text"].failed_checks
    alone = await Evaluator(
        _ScriptedOcr({"zone": _words(B01)}), readback=_Readback("«Summer Sale — 30% OFF»")
    ).evaluate(synthetic_ad(B01), None, target())
    assert alone.dimensions["text"].passed is True
    note = next(c for c in alone.checks if c.name == "vlm_readback")
    assert note.data and note.data["note"] is True


def test_textnorm_keeps_delimiters_the_copy_lacks() -> None:
    assert delimiters_to_keep(B01) >= {'"', "'", "(", ")"}
    assert '"' not in delimiters_to_keep('"Best" Deal') and "'" not in delimiters_to_keep("It’s")
    keep = delimiters_to_keep(B01)
    assert normalise("«Summer Sale — 30% OFF»", keep) == '"summer sale - 30% off"'
    assert normalise("‹Sale› [NEW]", keep) == '"sale" [new]'
    assert normalise("«Summer Sale — 30% OFF»") == "summer sale - 30% off"  # default unchanged


# --- fail-closed paths and the VLM veto (scripted OCR, no Tesseract needed) ----------------------


@dataclass
class _ScriptedOcr:
    """Returns the same scripted words for every read (zone crops are read at 2x)."""

    zone_words: dict[str, list[OcrWord]]
    fail: bool = False
    calls: list[int] = field(default_factory=list[int])
    name: str = "scripted"

    def available(self) -> bool:
        return True

    def lang_for(self, script: str, market_langs: tuple[str, ...] = ()) -> str | None:
        return "eng"

    async def read(self, image: Image.Image, *, lang: str, psm: int) -> OcrResult:
        self.calls.append(psm)
        if self.fail:
            raise RuntimeError("ocr crashed")
        words = self.zone_words.get("zone", []) if psm == 6 else []
        return OcrResult(engine="scripted", lang=lang, psm=psm, words=words)


def _words(text: str) -> list[OcrWord]:
    return [
        OcrWord(text=t, conf=95, left=40 + i * 160, top=40, width=150, height=60)
        for i, t in enumerate(text.split())
    ]


class _Readback:
    def __init__(self, text: str | None) -> None:
        self.text = text

    async def read(self, image: bytes) -> list[TextRead] | None:
        if self.text is None:
            return None
        return [TextRead(text=self.text, box_2d=[60, 80, 180, 920])]


async def test_ocr_failure_is_unverified_never_pass() -> None:
    ad = synthetic_ad(B01)
    ev = await Evaluator(_ScriptedOcr({}, fail=True)).evaluate(ad, None, target())
    assert ev.dimensions["text"].passed is None
    assert ev.verdict == "unverified"  # fails closed: the run can't auto-pass

    no_ocr = await Evaluator(None).evaluate(ad, None, target())
    assert no_ocr.verdict == "unverified"


async def test_vlm_readback_never_passes_text() -> None:
    """ev-0.8: the read-back is the third reader. It can corroborate an OCR error but never
    pardon one; uncorroborated differences go to a re-read and then pass or stay unverified."""
    ad = synthetic_ad(B01)
    right = {"zone": _words(B01)}
    wrong = {"zone": _words("Summer Sale — 38% OFF")}

    # OCR exact, the VLM read differs: the OCR re-read is exact again -> pass (a note).
    noted = await Evaluator(
        _ScriptedOcr(right), readback=_Readback("Summer Sale 3O% OFF")
    ).evaluate(ad, None, target())
    assert noted.dimensions["text"].passed is True

    # OCR sees an error at every size, the VLM "sees" the right text (autocorrect) -> unverified,
    # never a pass.
    pardoned = await Evaluator(_ScriptedOcr(wrong), readback=_Readback(B01)).evaluate(
        ad, None, target()
    )
    assert pardoned.dimensions["text"].passed is None

    # Both see the same error -> fail.
    both = await Evaluator(
        _ScriptedOcr(wrong), readback=_Readback("Summer Sale — 38% OFF")
    ).evaluate(ad, None, target())
    assert both.dimensions["text"].passed is False

    # OCR unverified, VLM matches -> still unverified, never a pass.
    unsure = await Evaluator(_ScriptedOcr(right, fail=True), readback=_Readback(B01)).evaluate(
        ad, None, target()
    )
    assert unsure.dimensions["text"].passed is None

    # Both agree -> pass; a read-back that is not run changes nothing.
    agreed = await Evaluator(_ScriptedOcr(right), readback=_Readback(B01)).evaluate(
        ad, None, target()
    )
    assert agreed.dimensions["text"].passed is True
    skipped = await Evaluator(_ScriptedOcr(right), readback=_Readback(None)).evaluate(
        ad, None, target()
    )
    assert skipped.dimensions["text"].passed is True


async def test_overlay_only_text_is_not_expected_natively() -> None:
    ev = await Evaluator(_ScriptedOcr({})).evaluate(
        synthetic_ad(B01), None, target(text_mode="overlay_only")
    )
    assert ev.dimensions["text"].failed_checks == ["overlay_pending"]


@needs_tesseract
async def test_ocr_results_are_cached_and_recorded() -> None:
    from backend.llm.cache import MemoryCache
    from backend.llm.calls import CallRuntime
    from backend.llm.ledger import MemoryRecorder

    recorder = MemoryRecorder()
    ocr = TesseractOcr(CallRuntime(recorder=recorder, cache=MemoryCache()))
    evaluator = Evaluator(ocr)
    ad = synthetic_ad(B01)
    first = await evaluator.evaluate(ad, _product(), target())
    second = await evaluator.evaluate(ad, _product(), target())
    assert first.dimensions == second.dimensions
    reads = [r for r in recorder.records if r.operation == "ocr.tesseract"]
    assert reads and all(r.kind == "other" for r in reads)
    n = len(reads) // 2
    assert not any(r.cached for r in reads[:n]) and all(r.cached for r in reads[n:])
