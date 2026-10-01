"""`Evaluator.evaluate()`: a pure function of (image, reference, target) -> `Evaluation`.

No DB access; the caller persists `evaluations` / `evaluation_checks`. Dimensions run cheapest and
most certain first, and a technical failure short-circuits the rest (the image can't be judged).

After technical, the three vision calls (`ad_inspect`, `context_judge`, `composition_judge`) run in
parallel. Their answers feed: the product dimension (the box to crop for CIEDE2000 + the
checklist), the text dimension (the product box excludes label text from the stray check; the
blind read-back can veto), the context dimension and the composition dimension (ADR-007: realistic
scale and natural integration, with the product box's area as a deterministic sanity signal).
Judge authority is **veto, not pardon**: text and product pass only on their deterministic checks.
With no judge (unconfigured, down, invalid answer) the vision checks are `unverified`, so the
verdict is `unverified` and the run fails closed to `needs_review`.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import cast

from backend.domain.adstudio.evaluator.color import Box
from backend.domain.adstudio.evaluator.composition import (
    CompositionTarget,
    composition_checks,
    evaluate_composition,
    scale_sanity,
)
from backend.domain.adstudio.evaluator.config import EvaluatorConfig, load_config
from backend.domain.adstudio.evaluator.context import evaluate_context
from backend.domain.adstudio.evaluator.ocr import OcrEngine
from backend.domain.adstudio.evaluator.product import (
    evaluate_product,
    primary_box,
    product_boxes,
    unverified_product,
)
from backend.domain.adstudio.evaluator.readback import StaticReadback, TextReadback
from backend.domain.adstudio.evaluator.schemas import (
    CheckResult,
    Dimension,
    DimensionResult,
    Evaluation,
    dimension_from_checks,
    overall_verdict,
)
from backend.domain.adstudio.evaluator.technical import (
    decode_for_checks,
    technical_checks,
    technical_dimension,
)
from backend.domain.adstudio.evaluator.text import evaluate_text
from backend.domain.adstudio.layout import NormBox
from backend.domain.adstudio.vision import (
    AdInspection,
    CompositionVerdict,
    ContextCheck,
    ContextVerdict,
    VisionClient,
)
from backend.llm.cache import CacheMissError
from backend.storage.blobs import sha256_hex

EVALUATOR_VERSION = load_config().evaluator_version
JUDGE_CACHE_VERSION = load_config().judge_cache_version


@dataclass(frozen=True)
class EvalTarget:
    """What the image is judged against (compiled from the run's spec and product facts by code)."""

    aspect_ratio: str
    required_text: str
    lines: tuple[str, ...]
    text_zone: NormBox
    script: str = "Latn"
    country_code: str | None = None
    ocr_langs: tuple[str, ...] = ()
    text_mode: str = "native"  # native | overlay_only (no text expected until the overlay)
    reference_box: Box | None = None  # product profile box_2d on the reference, 0-1000
    reference_labels: tuple[str, ...] = ()  # label text on the product (data, never instructions)
    product_summary: str = ""
    context_checks: tuple[ContextCheck, ...] = ()
    composition: CompositionTarget = field(default_factory=CompositionTarget)
    extra: dict[str, str] = field(default_factory=dict[str, str])


def _box_to_norm(box: Box) -> NormBox:
    ymin, xmin, ymax, xmax = box
    return NormBox(x0=xmin / 1000, y0=ymin / 1000, x1=xmax / 1000, y1=ymax / 1000)


@dataclass
class _VisionAnswers:
    inspection: AdInspection | None = None
    context: ContextVerdict | None = None
    composition: CompositionVerdict | None = None
    inspect_error: str = ""
    context_error: str = ""
    composition_error: str = ""
    model: str = ""


def _why(exc: BaseException) -> str:
    return f"The vision judge is unavailable ({type(exc).__name__}); not auto-approved."


class Evaluator:
    def __init__(
        self,
        ocr: OcrEngine | None = None,
        *,
        vision: VisionClient | None = None,
        readback: TextReadback | None = None,
        config: EvaluatorConfig | None = None,
        ocr2: OcrEngine | None = None,
    ) -> None:
        """`ocr` is Tesseract; `ocr2` the second engine of the text ensemble (Apple Vision on
        macOS, ev-0.8). `readback=NoReadback()` turns the VLM read-back off (the A1 ablation's
        deterministic layer)."""
        self.ocr = ocr
        self.ocr2 = ocr2
        self.vision = vision
        self.readback = readback  # explicit override; otherwise the inspection's read-back is used
        self.config = config or load_config()

    @property
    def version(self) -> str:
        return self.config.evaluator_version

    async def _ask_vision(
        self, image: bytes, reference: bytes | None, target: EvalTarget
    ) -> _VisionAnswers:
        vision = self.vision
        if vision is None or not vision.configured():
            reason = "No vision judge is configured (VISION_JUDGE); not auto-approved."
            return _VisionAnswers(
                inspect_error=reason, context_error=reason, composition_error=reason
            )
        answers = _VisionAnswers(model=vision.model)

        async def inspect() -> AdInspection | None:
            if reference is None:
                return None
            return await vision.inspect(reference, image, summary=target.product_summary)

        async def judge() -> ContextVerdict | None:
            if not target.context_checks:
                return None
            return await vision.judge_context(image, list(target.context_checks))

        async def compose() -> CompositionVerdict:
            return await vision.judge_composition(image, composition_checks(target.composition))

        inspected, judged, composed = await asyncio.gather(
            inspect(), judge(), compose(), return_exceptions=True
        )
        for result in (inspected, judged, composed):
            if isinstance(result, CacheMissError):
                raise result  # replay snapshot is stale: fail loudly, never go live or guess
        if isinstance(inspected, BaseException):
            answers.inspect_error = _why(inspected)
        else:
            answers.inspection = inspected
        if isinstance(judged, BaseException):
            answers.context_error = _why(judged)
        else:
            answers.context = judged
        if isinstance(composed, BaseException):
            answers.composition_error = _why(composed)
        else:
            answers.composition = composed
        return answers

    async def evaluate(
        self, image: bytes, reference: bytes | None, target: EvalTarget
    ) -> Evaluation:
        started = time.perf_counter()
        img = decode_for_checks(image)
        ref_img = decode_for_checks(reference) if reference else None
        checks: list[CheckResult] = technical_checks(
            img, target.aspect_ratio, self.config.technical, ref_img
        )
        dims: dict[Dimension, DimensionResult] = {"technical": technical_dimension(checks)}
        vision_model = ""
        if dims["technical"].passed and img is not None:
            answers = await self._ask_vision(image, reference, target)
            vision_model = answers.model
            inspection = answers.inspection
            readback = self.readback
            if readback is None and inspection is not None and inspection.visible_text:
                # An empty transcription is no opinion: OCR can't read an exact headline that isn't
                # there, so "the VLM saw nothing" adds no evidence against a deterministic pass.
                readback = StaticReadback(inspection.visible_text)
            text_checks, text_dim = await evaluate_text(
                img,
                ref_img,
                required_text=target.required_text,
                script=target.script,
                zone=target.text_zone,
                market_langs=target.ocr_langs,
                text_mode=target.text_mode,
                ocr=self.ocr,
                cfg=self.config.text,
                readback=readback,
                image_bytes=image,
                product_boxes=[_box_to_norm(b) for b in product_boxes(inspection)],
                reference_labels=target.reference_labels,
                lines=target.lines,
                ocr2=self.ocr2,
                product_missing=(
                    reference is not None
                    and inspection is not None
                    and inspection.product_count == 0
                    and not product_boxes(inspection)
                ),
            )
            checks += text_checks
            dims["text"] = text_dim

            if reference is None:
                product_checks, product_dim = unverified_product(
                    "No reference image; product fidelity can't be checked."
                )
            else:
                product_checks, product_dim = evaluate_product(
                    img,
                    ref_img,
                    reference_box=target.reference_box,
                    inspection=inspection,
                    unavailable_reason=answers.inspect_error,
                    cfg=self.config.product,
                )
            checks += product_checks
            dims["product"] = product_dim

            context_checks, context_dim = evaluate_context(
                list(target.context_checks),
                answers.context,
                unavailable_reason=answers.context_error,
                cfg=self.config.context,
            )
            checks += context_checks
            dims["context"] = context_dim

            boxes = product_boxes(inspection)
            main_box = primary_box(inspection.products) if inspection is not None else None
            sanity = scale_sanity(
                target.composition,
                main_box or (boxes[0] if boxes else None),
                img.size,
                self.config.composition,
            )
            comp_checks, comp_dim = evaluate_composition(
                composition_checks(target.composition),
                answers.composition,
                sanity,
                unavailable_reason=answers.composition_error,
                cfg=self.config.composition,
                target=target.composition,
            )
            checks += comp_checks
            dims["composition"] = comp_dim
        verdict = overall_verdict(dims)
        composite = sum(d.score for d in dims.values()) / len(dims)
        return Evaluation(
            image_sha=sha256_hex(image),
            evaluator_version=self.version,
            dimensions=dims,
            checks=checks,
            verdict=verdict,
            composite=round(composite, 4),
            latency_ms=int((time.perf_counter() - started) * 1000),
            vision_model=vision_model or None,
        )

    async def evaluate_overlay(
        self,
        image: bytes,
        reference: bytes | None,
        target: EvalTarget,
        *,
        base: Evaluation,
        verification: str,
    ) -> Evaluation:
        """Re-evaluate an overlaid image (ai-design §4.4 step 6): technical + text are measured
        again; product, context and composition are inherited from the base image, which is valid
        because only the zone pixels changed and the overlay precondition keeps the product out of
        the zone.

        `verification="construction"` (no OCR pack for the script) passes text by construction:
        the rendered string equals `raw` by assertion. Any other unverified text stays unverified.
        """
        started = time.perf_counter()
        img = decode_for_checks(image)
        ref_img = decode_for_checks(reference) if reference else None
        checks: list[CheckResult] = technical_checks(
            img, target.aspect_ratio, self.config.technical, ref_img
        )
        dims: dict[Dimension, DimensionResult] = {"technical": technical_dimension(checks)}
        if dims["technical"].passed and img is not None:
            product = base.dimensions.get("product")
            box = product.signals.get("ad_box") if product else None
            boxes: list[Box] = []
            if isinstance(box, list) and len(box) == 4:  # pyright: ignore[reportUnknownArgumentType]
                boxes.append(cast(Box, tuple(int(v) for v in box)))  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
            if verification == "construction":
                check = CheckResult(
                    dimension="text",
                    name="overlay_construction",
                    passed=True,
                    value=0.0,
                    threshold=0.0,
                    evidence=(
                        "No OCR language pack for this script: the overlay drew the exact string, "
                        "so the text is verified by construction."
                    ),
                    data={"verification": "construction"},
                )
                text_checks = [check]
                text_dim = dimension_from_checks("text", text_checks, score=1.0)
            else:
                text_checks, text_dim = await evaluate_text(
                    img,
                    ref_img,
                    required_text=target.required_text,
                    script=target.script,
                    zone=target.text_zone,
                    market_langs=target.ocr_langs,
                    text_mode="native",
                    ocr=self.ocr,
                    cfg=self.config.text,
                    readback=None,
                    image_bytes=image,
                    product_boxes=[_box_to_norm(b) for b in boxes],
                    reference_labels=target.reference_labels,
                    lines=target.lines,
                    ocr2=self.ocr2,
                )
            checks += text_checks
            dims["text"] = text_dim
            for name in ("product", "context", "composition"):
                inherited = base.dimensions.get(name)
                if inherited is None:
                    continue
                dims[name] = inherited
                checks += [
                    c.model_copy(update={"data": {**(c.data or {}), "inherited": True}})
                    for c in base.checks
                    if c.dimension == name
                ]
        verdict = overall_verdict(dims)
        composite = sum(d.score for d in dims.values()) / len(dims)
        return Evaluation(
            image_sha=sha256_hex(image),
            evaluator_version=self.version,
            dimensions=dims,
            checks=checks,
            verdict=verdict,
            composite=round(composite, 4),
            latency_ms=int((time.perf_counter() - started) * 1000),
            vision_model=base.vision_model,
        )
