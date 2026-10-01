"""ev-0.8 text ensemble (Tesseract + Apple Vision + the VLM read-back), with scripted engines and
read-backs (no Tesseract, no macOS, no network):

- a text FAIL needs two readers to agree (both OCR engines, or one engine plus the read-back);
- a difference only one reader sees is re-read larger; an engine still sees an error only if all
  of its reads show it; then agreement fails, nobody seeing it passes, anything else is
  `unverified` (never a silent pass);
- a single reader (one engine, no read-back) keeps the pre-ev-0.8 strict rule;
- stray text: agreement fails; OCR-only noise is a note; a read-back-only stray is re-read, then
  `unverified` unless confirmed, or a note when the product itself was not located.
"""

from dataclasses import dataclass, field

import pytest
from PIL import Image

from backend.domain.adstudio.evaluator.config import load_config
from backend.domain.adstudio.evaluator.core import Evaluator
from backend.domain.adstudio.evaluator.ocr import OcrResult, OcrWord
from backend.domain.adstudio.evaluator.readback import NoReadback, TextRead, vlm_strays
from backend.domain.adstudio.evaluator.schemas import CheckResult, Evaluation
from backend.domain.adstudio.evaluator.text import (
    agreed_keys,
    consistent_keys,
    evaluate_text,
    window_errors,
)
from backend.domain.adstudio.layout import NormBox
from backend.domain.adstudio.textnorm import compact
from tests.unit.test_evaluator import ZONE, synthetic_ad, target

AD_SIZE = (825, 1024)  # synthetic_ad() at 4:5 after post-processing
REREAD_MIN_WIDTH = 2500  # zone crops are ~1600 px wide at 2x and ~3200 px at 4x (825 px ad)
CFG = load_config().text


def _words(text: str, *, scale: int = 1) -> list[OcrWord]:
    return [
        OcrWord(
            text=t,
            conf=95,
            left=(40 + i * 160) * scale,
            top=100 * scale,
            width=150 * scale,
            height=60 * scale,
        )
        for i, t in enumerate(text.split())
    ]


@dataclass
class _Ocr:
    """A scripted engine. Zone reads (psm 6) cycle through `first` (normal size) or `reread`
    (the larger re-read); any other read returns `full` (image coordinates) or, for a stray
    region re-read, `region`."""

    first: list[str]
    reread: list[str] | None = None
    full: list[OcrWord] = field(default_factory=list[OcrWord])
    region: list[str] = field(default_factory=list[str])
    name: str = "tesseract"
    rereads: int = 0
    region_reads: int = 0
    _i: int = 0
    _j: int = 0

    def available(self) -> bool:
        return True

    @property
    def version(self) -> str:
        return "scripted"

    def languages(self) -> frozenset[str]:
        return frozenset({"eng"})

    def lang_for(self, script: str, market_langs: tuple[str, ...] = ()) -> str | None:
        return "eng"

    async def read(self, image: Image.Image, *, lang: str, psm: int = 6) -> OcrResult:
        if psm == 6 and image.width >= REREAD_MIN_WIDTH:
            self.rereads += 1
            texts = self.reread or self.first
            self._j += 1
            words = _words(texts[(self._j - 1) % len(texts)], scale=2)
        elif psm == 6:
            self._i += 1
            words = _words(self.first[(self._i - 1) % len(self.first)])
        elif image.size != AD_SIZE:
            self.region_reads += 1
            words = [
                OcrWord(text=t, conf=95, left=5, top=5, width=40, height=20) for t in self.region
            ]
        else:
            # The full image: the headline (as the first zone read) plus any stray words.
            zx, zy, _, _ = ZONE.expanded(CFG.zone_margin).to_pixels(*AD_SIZE)
            headline = [w.shifted(zx, zy, 2) for w in _words(self.first[0])]
            words = [*headline, *self.full]
        return OcrResult(engine=f"{self.name}-scripted", lang=lang, psm=psm, words=words)


class _Readback:
    def __init__(self, text: str, extra: list[TextRead] | None = None) -> None:
        self.reads = [TextRead(text=text, box_2d=[60, 80, 180, 920]), *(extra or [])]

    async def read(self, image: bytes) -> list[TextRead] | None:
        return self.reads


def _tess(*first: str, reread: list[str] | None = None, **kw: object) -> _Ocr:
    return _Ocr(list(first), reread, name="tesseract", **kw)  # type: ignore[arg-type]


def _apple(*first: str, reread: list[str] | None = None, **kw: object) -> _Ocr:
    return _Ocr(list(first), reread, name="apple_vision", **kw)  # type: ignore[arg-type]


async def _evaluate(
    copy: str, t: _Ocr, a: _Ocr | None, vlm: str | None, extra: list[TextRead] | None = None
) -> Evaluation:
    readback = _Readback(vlm, extra) if vlm is not None else NoReadback()
    return await Evaluator(t, ocr2=a, readback=readback).evaluate(
        synthetic_ad(copy), None, target(copy)
    )


def _check(ev: Evaluation, name: str) -> CheckResult:
    return next(c for c in ev.checks if c.name == name)


JULY = "4th of July — 25% OFF"


# --- error keys ------------------------------------------------------------


def test_error_keys_and_agreement() -> None:
    req = compact("Stay Cool. Only AED 49", "Latn")
    slash = window_errors(compact("Stay Cool. Only / AED 49", "Latn"), req, "x", "Latn")
    assert ("ins", 16, "/") in slash or ("ins", 17, "/") in slash
    # The same insertion one position apart (alignment ambiguity) still agrees.
    other = {("ins", 17, "/")}
    assert agreed_keys(slash, other)
    # A quote read outside one window and inside the other agrees too.
    assert agreed_keys({("extra", -1, "‘")}, {("ins", 0, "'")})
    # Different errors do not agree.
    assert not agreed_keys({("chg", 0, "")}, {("chg", 5, "")})
    # Consistency: an error one read does not show is dropped.
    assert consistent_keys([{("chg", 0, "")}, {("chg", 0, "")}, {("chg", 2, "")}]) == set()
    assert consistent_keys([{("chg", 0, "")}, {("chg", 0, ""), ("chg", 3, "")}]) == {("chg", 0, "")}


# --- headline zone ------------------------------------------------------------


async def test_engine_artefact_is_resolved_by_the_reread() -> None:
    """PL29: Tesseract reads the bold "4th" as "Ath"; Apple Vision and the read-back read it
    exactly. At 4x one Tesseract preprocessing reads the "4" (and misreads other letters), so
    Tesseract does not consistently see the error: pass."""
    t = _tess(
        "Ath of July — 25% OFF",
        reread=["Ath of July — 25% OFF", "Ath of July — 25% OFF", "4tn of July — 29% OFF"],
    )
    a = _apple(JULY)
    ev = await _evaluate(JULY, t, a, JULY)
    text = ev.dimensions["text"]
    assert text.passed is True, text.reasons
    assert text.signals["decision"] == "reread_resolved"
    assert t.rereads == 3 and a.rereads == 2


async def test_a_consistent_single_reader_error_is_unverified_never_a_pass() -> None:
    t = _tess("Ath of July — 25% OFF")  # every read, at every size, shows "Ath"
    ev = await _evaluate(JULY, t, _apple(JULY), JULY)
    text = ev.dimensions["text"]
    assert text.passed is None and ev.verdict == "unverified"
    cer = _check(ev, "ocr_cer")
    assert cer.passed is None and "unverified, not a fail" in cer.evidence
    assert cer.data and cer.data["decision"] == "reread_disagrees"


@pytest.mark.parametrize(
    ("copy", "t_read", "a_read", "vlm"),
    [
        # The line-break marker drawn as "/": all three readers see it.
        ("Stay Cool. Only AED 49", "Stay Cool. Only / AED 49", "Stay Cool. Only / AED 49", None),
        # A planted typo the VLM autocorrects: both engines see it.
        ("Summer Sale — 30% OFF", "Summer Sale — 38% OFF", "Summer Sale — 38% OFF", "same"),
        # A stray opening quote: Tesseract and the read-back see it, Apple Vision garbles it.
        (
            "Merry Christmas, Mate!",
            "‘Merry Christmas, Mate!",
            "Merry Christmas, Mate!",
            "‘Merry Christmas, Mate!",
        ),
        # Drawn guillemets (ev-0.4 delimiter rule).
        ("Summer Sale", "«Summer Sale»", "«Summer Sale»", "Summer Sale"),
        # An extra word in the zone (ev-0.4 zone-extra rule).
        ("Summer Sale", "Summer Sale BONUS", "Summer Sale BONUS", "Summer Sale"),
        # A missing word: one engine and the read-back.
        (
            "Summer Sale — 30% OFF",
            "Summer Sale — 30%",
            "Summer Sale — 30% OFF",
            "Summer Sale — 30%",
        ),
    ],
)
async def test_agreed_errors_fail_without_reread(
    copy: str, t_read: str, a_read: str, vlm: str | None
) -> None:
    t = _tess(t_read, reread=[copy])  # a re-read would "fix" it: it must not be asked for
    a = _apple(a_read, reread=[copy])
    ev = await _evaluate(copy, t, a, copy if vlm == "same" else vlm)
    assert ev.dimensions["text"].passed is False, ev.dimensions["text"].signals
    assert t.rereads == 0 and a.rereads == 0
    assert _check(ev, "ocr_cer").data["decision"] == "agreed"  # type: ignore[index]


async def test_a_readback_only_difference_is_a_note() -> None:
    t, a = _tess("Summer Sale"), _apple("Summer Sale")
    ev = await _evaluate("Summer Sale", t, a, "Summer Sole")
    assert ev.dimensions["text"].passed is True
    rb = _check(ev, "vlm_readback")
    assert rb.passed is True and rb.data and rb.data["note"] is True


async def test_a_single_reader_keeps_the_strict_rule() -> None:
    """One engine and no read-back (the overlay check off macOS): its error fails."""
    t = _tess("Summer Sale — 38% OFF", reread=["Summer Sale — 30% OFF"])
    ev = await _evaluate("Summer Sale — 30% OFF", t, None, None)
    assert ev.dimensions["text"].passed is False and t.rereads == 0
    assert ev.dimensions["text"].signals["decision"] == "single_reader"


async def test_tesseract_and_readback_without_apple_vision() -> None:
    """Off macOS the ensemble is Tesseract + the read-back: agreement still fails."""
    ev = await _evaluate("Summer Sale", _tess("Summr Sale"), None, "Summr Sale")
    assert ev.dimensions["text"].passed is False


# --- stray text ------------------------------------------------------------

FAR = 700  # below the zone, away from any product box (no reference -> no product box)


def _stray(text: str) -> OcrWord:
    return OcrWord(text=text, conf=90, left=100, top=FAR, width=160, height=40)


async def test_stray_tokens_need_four_letters_and_agreement() -> None:
    assert CFG.stray_min_alnum == 4
    copy = "Summer Sale"
    # Short noise never counts.
    ev = await _evaluate(copy, _tess(copy, full=[_stray("pts")]), _apple(copy), copy)
    assert _check(ev, "stray_text").passed is True
    # Both engines read it: fail.
    ev = await _evaluate(
        copy, _tess(copy, full=[_stray("SALEE")]), _apple(copy, full=[_stray("SALEE")]), copy
    )
    assert _check(ev, "stray_text").passed is False
    # Only Tesseract reads it; Apple Vision (also re-reading the region) and the read-back don't.
    t = _tess(copy, full=[_stray("SALEE")])
    a = _apple(copy, region=["wood"])
    ev = await _evaluate(copy, t, a, copy)
    stray = _check(ev, "stray_text")
    assert stray.passed is True and stray.data and stray.data["note"] is True
    assert a.region_reads == 1
    # ...unless the other engine confirms it in the region re-read.
    ev = await _evaluate(
        copy, _tess(copy, full=[_stray("SALEE")]), _apple(copy, region=["SALEE"]), copy
    )
    assert _check(ev, "stray_text").passed is False


async def test_readback_only_stray_is_reread_then_unverified_or_confirmed() -> None:
    copy = "Summer Sale"
    prop = TextRead(text="OLIVE OIL", box_2d=[700, 100, 740, 300])
    # No OCR engine confirms embossed prop text: unverified (needs review), not a pass.
    ev = await _evaluate(copy, _tess(copy), _apple(copy, region=["IVE"]), copy, [prop])
    check = _check(ev, "vlm_stray_text")
    assert check.passed is None and ev.dimensions["text"].passed is None
    # An engine confirms it: fail.
    ev = await _evaluate(copy, _tess(copy), _apple(copy, region=["OLIVE", "OIL"]), copy, [prop])
    assert _check(ev, "vlm_stray_text").passed is False


async def test_readback_stray_on_an_unlocated_product_is_a_note() -> None:
    """PL18: the swapped-in product was not located, so the read-back's "Sun Bum" can't be told
    from stray text; the failed product check owns the image."""
    copy = "Summer Sale"
    img = Image.open(__import__("io").BytesIO(synthetic_ad(copy))).convert("RGB")
    reads = [
        TextRead(text=copy, box_2d=[60, 80, 180, 920]),
        TextRead(text="Sun Bum", box_2d=[709, 516, 725, 550]),
    ]

    class _Rb:
        async def read(self, image: bytes) -> list[TextRead] | None:
            return reads

    for missing, expected in ((True, True), (False, None)):
        checks, dim = await evaluate_text(
            img,
            None,
            required_text=copy,
            script="Latn",
            zone=ZONE,
            market_langs=(),
            text_mode="native",
            ocr=_tess(copy),
            ocr2=_apple(copy),
            cfg=CFG,
            readback=_Rb(),
            product_missing=missing,
        )
        stray = next(c for c in checks if c.name == "vlm_stray_text")
        assert stray.passed is expected and dim.passed is expected


def test_readback_stray_skips_label_text_and_text_on_the_product() -> None:
    product = NormBox(x0=0.40, y0=0.50, x1=0.60, y1=0.90)
    reads = [
        TextRead(text="Summer Sale — 30% OFF", box_2d=[60, 80, 180, 920]),
        # printed label, spelled differently from the reference facts ("Sun BUM®")
        TextRead(text="SUN BUM", box_2d=[300, 700, 340, 800]),
        # on the product but its centre just outside the box (a long label line)
        TextRead(text="Broad Spectrum", box_2d=[850, 380, 880, 560]),
        # a real stray word elsewhere
        TextRead(text="SALEE XQ", box_2d=[300, 60, 340, 300]),
    ]
    found = vlm_strays(
        reads,
        ZONE.expanded(0.05),
        [product],
        ("Sun BUM®", "Broad Spectrum SPF 30"),
        4,
        80,
        0.02,
    )
    assert [r.text for r in found] == ["SALEE XQ"]
