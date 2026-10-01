"""OCR behind a small protocol: Tesseract via pytesseract, and (ev-0.8, macOS only) Apple Vision
via pyobjc (installed with `ocrmac`) as the second engine of the text ensemble. Both are local and
deterministic; neither is an AI service call.

Every read goes through `guarded_call(kind="other")`, so it is timed, recorded in the ledger and
cached by (engine version, language, mode, sha256 of the exact pixels read). Replaying from a
`FileCache` snapshot therefore needs neither engine installed.
"""

import asyncio
import importlib
import io
import os
import platform
import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol, cast

from PIL import Image
from pydantic import BaseModel

from backend.llm.cache import cache_key
from backend.llm.calls import CallRuntime, guarded_call
from backend.storage.blobs import sha256_hex

# OpenMP builds of Tesseract (Debian/Ubuntu) spin-wait per read; alongside the evaluator's other
# CPU work, a 0.2 s zone read measured 30-40 s and hit the OCR timeout. Reads already run in
# parallel threads, so one OpenMP thread each loses nothing. The subprocess inherits os.environ.
os.environ.setdefault("OMP_THREAD_LIMIT", "1")

# ISO 15924 script -> Tesseract language pack(s). Latin copy adds the market's languages.
SCRIPT_LANGS: dict[str, tuple[str, ...]] = {
    "Latn": ("eng",),
    "Cyrl": ("rus", "ukr"),
    "Grek": ("ell",),
    # ev-0.8: ara alone reads a Latin "30%" in Arabic copy as "3090"; eng as a second pack fixes it.
    "Arab": ("ara", "eng"),
    "Hebr": ("heb",),
    "Deva": ("hin",),
    "Beng": ("ben",),
    "Taml": ("tam",),
    "Thai": ("tha",),
    "Khmr": ("khm",),
    "Jpan": ("jpn",),
    "Hani": ("chi_sim", "chi_tra"),
    "Hans": ("chi_sim",),
    "Hant": ("chi_tra",),
    "Kore": ("kor",),
    "Hang": ("kor",),
    "Ethi": ("amh",),
    "Sinh": ("sin",),
}


class OcrWord(BaseModel):
    text: str
    conf: float
    left: int
    top: int
    width: int
    height: int

    @property
    def cx(self) -> float:
        return self.left + self.width / 2

    @property
    def cy(self) -> float:
        return self.top + self.height / 2

    def shifted(self, dx: int, dy: int, scale: float) -> "OcrWord":
        return OcrWord(
            text=self.text,
            conf=self.conf,
            left=round(self.left / scale) + dx,
            top=round(self.top / scale) + dy,
            width=round(self.width / scale),
            height=round(self.height / scale),
        )


class OcrResult(BaseModel):
    engine: str
    lang: str
    psm: int
    words: list[OcrWord]


class OcrUnavailableError(RuntimeError):
    pass


class OcrEngine(Protocol):
    name: str

    def available(self) -> bool: ...

    def lang_for(self, script: str, market_langs: tuple[str, ...] = ()) -> str | None:
        """The language string for this script, or None when no pack is installed."""
        ...

    async def read(self, image: Image.Image, *, lang: str, psm: int) -> OcrResult: ...


def reading_order(words: list[OcrWord], *, rtl: bool = False) -> list[list[OcrWord]]:
    """Group words into lines by vertical-centre overlap; lines top-down, words left-right (right
    to left for a right-to-left script, so the text comes out in logical order)."""
    lines: list[list[OcrWord]] = []
    for word in sorted(words, key=lambda w: (w.cy, w.left)):
        for line in lines:
            ref = line[0]
            if abs(word.cy - ref.cy) <= max(ref.height, word.height) * 0.5:
                line.append(word)
                break
        else:
            lines.append([word])
    ordered = [sorted(line, key=lambda w: w.left, reverse=rtl) for line in lines]
    return sorted(ordered, key=lambda line: min(w.top for w in line))


def text_of(words: list[OcrWord], *, rtl: bool = False) -> str:
    return " ".join(" ".join(w.text for w in line) for line in reading_order(words, rtl=rtl))


@dataclass(frozen=True)
class _TessInfo:
    version: str
    langs: frozenset[str]


@lru_cache(maxsize=1)
def _tesseract_info() -> _TessInfo | None:
    try:
        import pytesseract  # pyright: ignore[reportMissingTypeStubs]

        version = str(pytesseract.get_tesseract_version())  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        langs = cast(list[str], pytesseract.get_languages(config=""))  # pyright: ignore[reportUnknownMemberType]
    except Exception:  # noqa: BLE001 - binary missing or broken: OCR is unavailable
        return None
    return _TessInfo(version=version, langs=frozenset(langs))


def _png(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


class TesseractOcr:
    name = "tesseract"

    def __init__(self, runtime: CallRuntime | None = None, timeout_s: float = 30.0) -> None:
        self.runtime = runtime
        self.timeout_s = timeout_s

    def available(self) -> bool:
        return _tesseract_info() is not None

    @property
    def version(self) -> str:
        info = _tesseract_info()
        return info.version if info else "missing"

    def languages(self) -> frozenset[str]:
        info = _tesseract_info()
        return info.langs if info else frozenset()

    def lang_for(self, script: str, market_langs: tuple[str, ...] = ()) -> str | None:
        langs = self.languages()
        if not self.available() or not langs:
            return None
        wanted = list(SCRIPT_LANGS.get(script, ()))
        if script == "Latn":
            # The market's language first (accents), English as the fallback model.
            wanted = [*market_langs, *[lang for lang in wanted if lang not in market_langs]]
        installed = [lang for lang in wanted if lang in langs]
        if not installed:
            return None
        return "+".join(installed)

    def _run(self, png: bytes, lang: str, psm: int) -> dict[str, Any]:
        import pytesseract  # pyright: ignore[reportMissingTypeStubs]

        with Image.open(io.BytesIO(png)) as img:
            data: Any = pytesseract.image_to_data(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
                img, lang=lang, config=f"--psm {psm}", output_type=pytesseract.Output.DICT
            )
        words: list[dict[str, Any]] = []
        raw = cast(dict[str, list[Any]], data)
        for i, text in enumerate(raw["text"]):
            token = str(text).strip()
            conf = float(raw["conf"][i])
            if not token or conf < 0:
                continue
            words.append(
                {
                    "text": token,
                    "conf": conf,
                    "left": int(raw["left"][i]),
                    "top": int(raw["top"][i]),
                    "width": int(raw["width"][i]),
                    "height": int(raw["height"][i]),
                }
            )
        return {"engine": f"tesseract-{self.version}", "lang": lang, "psm": psm, "words": words}

    async def read(self, image: Image.Image, *, lang: str, psm: int) -> OcrResult:
        if not self.available():
            raise OcrUnavailableError("tesseract is not installed")
        png = _png(image)
        engine = f"local:tesseract-{self.version}"

        async def call() -> dict[str, Any]:
            return await asyncio.to_thread(self._run, png, lang, psm)

        result, _ = await guarded_call(
            "other",
            "ocr.tesseract",
            engine,
            call,
            version=self.version,
            prompt_name="ocr",
            cache_key=cache_key("ocr", engine, lang, psm, sha256_hex(png)),
            runtime=self.runtime,
            timeout_s=self.timeout_s,
            retry_on_timeout=False,
        )
        return OcrResult.model_validate(result)


# --- Apple Vision (ev-0.8, the second engine) ----------------------------------------------------

# Tesseract pack (the market languages in countries.yaml) -> Apple Vision recognition language.
VISION_LANGS: dict[str, str] = {
    "eng": "en-US",
    "fra": "fr-FR",
    "deu": "de-DE",
    "spa": "es-ES",
    "por": "pt-BR",
    "ita": "it-IT",
    "nld": "nl-NL",
    "nor": "no-NO",
    "swe": "sv-SE",
    "dan": "da-DK",
    "fin": "fi-FI",
    "pol": "pl-PL",
    "ces": "cs-CZ",
    "ron": "ro-RO",
    "tur": "tr-TR",
    "ind": "id-ID",
    "msa": "ms-MY",
    "vie": "vi-VT",
}
# ISO 15924 script -> Apple Vision languages (Latin adds the market's languages).
VISION_SCRIPT_LANGS: dict[str, tuple[str, ...]] = {
    "Latn": ("en-US",),
    "Cyrl": ("ru-RU", "uk-UA"),
    "Arab": ("ar-SA",),
    "Deva": ("hi-IN",),
    "Thai": ("th-TH",),
    "Jpan": ("ja-JP",),
    "Hani": ("zh-Hans", "zh-Hant"),
    "Hans": ("zh-Hans",),
    "Hant": ("zh-Hant",),
    "Kore": ("ko-KR",),
    "Hang": ("ko-KR",),
}
_WORD = re.compile(r"\S+")


def _first(value: Any) -> Any:
    """pyobjc returns (result, error) for `...error_` selectors on some mappings."""
    if isinstance(value, tuple):
        return cast(tuple[Any, ...], value)[0]
    return value


def _objc_module(name: str) -> Any:
    """A pyobjc module (Vision, Foundation, objc), typed as Any: pyobjc ships no stubs."""
    return importlib.import_module(name)


@dataclass(frozen=True)
class _VisionInfo:
    version: str
    langs: frozenset[str]


@lru_cache(maxsize=1)
def _vision_info() -> _VisionInfo | None:
    if sys.platform != "darwin":
        return None
    try:
        vision = _objc_module("Vision")
        req = vision.VNRecognizeTextRequest.alloc().init()
        req.setRecognitionLevel_(0)
        langs = [str(x) for x in req.supportedRecognitionLanguagesAndReturnError_(None)[0]]
        revision = int(req.revision())
    except Exception:  # noqa: BLE001 - not macOS / pyobjc missing: the engine is unavailable
        return None
    return _VisionInfo(version=f"macos{platform.mac_ver()[0]}-r{revision}", langs=frozenset(langs))


class AppleVisionOcr:
    """Apple Vision `VNRecognizeTextRequest`, accurate level, **language correction off** (an
    exact-text check must not autocorrect a typo). Returns word boxes (one per whitespace token of
    each recognised line). `psm` is accepted for the protocol and ignored."""

    name = "apple_vision"

    def __init__(self, runtime: CallRuntime | None = None, timeout_s: float = 60.0) -> None:
        self.runtime = runtime
        self.timeout_s = timeout_s

    def available(self) -> bool:
        return _vision_info() is not None

    @property
    def version(self) -> str:
        info = _vision_info()
        return info.version if info else "missing"

    def languages(self) -> frozenset[str]:
        info = _vision_info()
        return info.langs if info else frozenset()

    def lang_for(self, script: str, market_langs: tuple[str, ...] = ()) -> str | None:
        langs = self.languages()
        if not self.available() or not langs:
            return None
        wanted = list(VISION_SCRIPT_LANGS.get(script, ()))
        if script == "Latn":
            market = [VISION_LANGS[m] for m in market_langs if m in VISION_LANGS]
            wanted = [*market, *[lang for lang in wanted if lang not in market]]
        installed = [lang for lang in dict.fromkeys(wanted) if lang in langs]
        return ",".join(installed) or None

    def _run(self, png: bytes, lang: str) -> dict[str, Any]:
        foundation = _objc_module("Foundation")
        objc = _objc_module("objc")
        vision = _objc_module("Vision")
        with Image.open(io.BytesIO(png)) as img:
            width, height = img.size
        words: list[dict[str, Any]] = []
        with objc.autorelease_pool():
            req = vision.VNRecognizeTextRequest.alloc().init()
            req.setRecognitionLevel_(0)
            req.setUsesLanguageCorrection_(False)
            req.setRecognitionLanguages_(lang.split(","))
            handler = vision.VNImageRequestHandler.alloc().initWithData_options_(png, None)
            ok = _first(handler.performRequests_error_([req], None))
            if not ok:
                raise RuntimeError("Apple Vision text recognition failed")
            results: list[Any] = list(req.results() or ())
            for obs in results:
                candidates: list[Any] = list(obs.topCandidates_(1) or ())
                if not candidates:
                    continue
                cand = candidates[0]
                line = str(cand.string())
                conf = float(cand.confidence()) * 100
                for match in _WORD.finditer(line):
                    rng = foundation.NSMakeRange(match.start(), match.end() - match.start())
                    rect = _first(cand.boundingBoxForRange_error_(rng, None))
                    if rect is None:
                        continue
                    box: Any = rect.boundingBox()
                    # Vision boxes are normalised with the origin at the bottom left.
                    x0 = box.origin.x * width
                    y1 = (1 - box.origin.y) * height
                    words.append(
                        {
                            "text": match.group(0),
                            "conf": round(conf, 1),
                            "left": round(x0),
                            "top": round(y1 - box.size.height * height),
                            "width": max(1, round(box.size.width * width)),
                            "height": max(1, round(box.size.height * height)),
                        }
                    )
        return {"engine": f"apple-vision-{self.version}", "lang": lang, "psm": 0, "words": words}

    def warm(self) -> None:
        """The first Vision request loads the models (tens of seconds on a cold machine)."""
        if self.available():
            self._run(_png(Image.new("RGB", (64, 32), "white")), "en-US")

    async def read(self, image: Image.Image, *, lang: str, psm: int = 0) -> OcrResult:
        del psm
        if not self.available():
            raise OcrUnavailableError("Apple Vision OCR is not available (macOS only)")
        png = _png(image)
        engine = f"local:apple-vision-{self.version}"

        async def call() -> dict[str, Any]:
            return await asyncio.to_thread(self._run, png, lang)

        result, _ = await guarded_call(
            "other",
            "ocr.apple_vision",
            engine,
            call,
            version=self.version,
            prompt_name="ocr",
            cache_key=cache_key("ocr", engine, lang, "accurate-nocorrection", sha256_hex(png)),
            runtime=self.runtime,
            timeout_s=self.timeout_s,
            retry_on_timeout=False,
        )
        return OcrResult.model_validate(result)


def second_engine(runtime: CallRuntime | None, enabled: bool = True) -> "AppleVisionOcr | None":
    """Apple Vision when enabled and available (macOS), else None (Tesseract + read-back only)."""
    if not enabled:
        return None
    engine = AppleVisionOcr(runtime)
    return engine if engine.available() else None
