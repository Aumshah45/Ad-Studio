"""Repair routing (ai-design §4.3): an evaluation -> the next action, its model and its prompt.

Deterministic code, no model call. `Repairer.plan()` reads the base candidate's `Evaluation`,
turns failed checks into **structured failure codes** and walks the routing table below. Prompts
are compiled from those codes plus spec fields; the vision judge's evidence prose never reaches an
image prompt (production-readiness T2). The loop around it (N candidates, budget, events) is
`pipeline.py`.

Rows, first match wins (row id: base candidate state -> action on model):
- `technical`: technical fail (corrupt, blank, copy of the reference) -> regenerate, Flash-Lite
- `product`: product fail (± others) -> repair_product, Flash
- `context`: context fail, product ok -> repair_context, Flash
- `composition`: composition fail (unrealistic scale or pasted look), product and context ok ->
  repair_composition, Flash (ADR-007)
- `unverified`: product, context or composition unverified (vision judge down) -> needs_review
- `text_outside_zone`: text fail, stray text outside the zone or product in it -> clean_plate,
  Flash-Lite (then overlay)
- `overlay_only`: overlay-only text (flagged input) on a clean plate -> overlay
- `text_in_zone`: wrong headline inside the zone, a text repair left -> repair_text, Flash
- `text_exhausted`: text still failing / unverified, no text repair or budget left -> overlay
- `exhausted`: a non-text dimension failing and no repair or budget left -> needs_review
"""

from dataclasses import dataclass
from typing import Any, Literal, cast

from pydantic import BaseModel, Field

from backend.core.settings import Settings
from backend.domain.adstudio.adprompt import prompt_fields
from backend.domain.adstudio.evaluator.composition import (
    INTEGRATION_CHECK,
    RATIO_CHECK,
    SANITY_CHECK,
    SCALE_CHECK,
)
from backend.domain.adstudio.evaluator.product import CHECKLIST_LABELS
from backend.domain.adstudio.evaluator.schemas import Evaluation
from backend.domain.adstudio.image_clients import FakeScene, ImageRequest
from backend.domain.adstudio.layout import NormBox
from backend.domain.adstudio.planner import ReferenceFacts
from backend.domain.adstudio.prompting import (
    product_color_name,
    product_colors_text,
    product_colour_line,
    render_ad_prompt,
)
from backend.domain.adstudio.resolver import month_text
from backend.domain.adstudio.spec import CreativeSpec
from backend.llm.prompts.ad_generate import (
    AD_GENERATE,
    DEFAULT_SURFACE,
    copy_lines,
    escape_line,
    escape_literal,
)
from backend.llm.prompts.ad_repair import (
    AD_REPAIR_COMPOSITION,
    AD_REPAIR_CONTEXT,
    AD_REPAIR_PRODUCT,
    AD_REPAIR_TEXT,
    STRAY_BLOCK,
)
from backend.llm.prompts.base import Prompt

Action = Literal[
    "approve",
    "regenerate",
    "repair_product",
    "repair_context",
    "repair_composition",
    "repair_text",
    "clean_plate",
    "overlay",
    "needs_review",
]
Inputs = Literal["reference", "reference+candidate", "candidate", "none"]
Code = Literal[
    "TECHNICAL",
    "PRODUCT_MISSING",
    "PRODUCT_DUPLICATED",
    "PRODUCT_COLOR_DRIFT",
    "PRODUCT_CHECK",
    "CONTEXT_CHECK",
    "COMPOSITION_SCALE",
    "COMPOSITION_INTEGRATION",
    "TEXT_MISMATCH",
    "TEXT_TOKENS_MISSING",
    "STRAY_TEXT",
    "OVERLAY_PENDING",
]
ZONE_OVERLAP_MAX = 0.05  # product box ∩ zone, as a fraction of the product box (§4.4 step 1)


class FailureCode(BaseModel):
    """One failed check, reduced to code-owned fields (ids, numbers, OCR literals)."""

    code: Code
    check: str
    value: float | None = None
    expected: str | None = None
    got: str | None = None
    tokens: list[str] = Field(default_factory=list[str])


class RepairPlan(BaseModel):
    action: Action
    row: str
    reason: str
    base_image_sha: str
    model: str | None = None
    prompt_name: str | None = None
    prompt_version: str | None = None
    prompt: str | None = None
    inputs: Inputs = "none"
    text_mode: Literal["native", "overlay"] = "native"
    codes: list[FailureCode] = Field(default_factory=list[FailureCode])
    counts_as_repair: bool = False
    counts_as_text_repair: bool = False

    @property
    def calls_model(self) -> bool:
        return self.model is not None


@dataclass(frozen=True)
class RepairState:
    repairs_used: int = 0
    text_repairs_used: int = 0
    can_afford_image_call: bool = True  # the per-run Budget's projection (slice 12)


# --- failure codes --------------------------------------------------------------------------------


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _list(data: dict[str, Any], key: str) -> list[Any]:
    value = data.get(key)
    return cast(list[Any], value) if isinstance(value, list) else []


def failure_codes(evaluation: Evaluation) -> list[FailureCode]:
    dims = evaluation.dimensions
    gating_context = set(dims["context"].failed_checks) if "context" in dims else set[str]()
    text_signals = dims["text"].signals if "text" in dims else {}
    comp_signals = dims["composition"].signals if "composition" in dims else {}
    codes: list[FailureCode] = []
    for check in evaluation.checks:
        if check.passed is not False:
            continue
        data = check.data or {}
        if check.dimension == "technical":
            codes.append(FailureCode(code="TECHNICAL", check=check.name, value=check.value))
        elif check.name == "product_count":
            count = int(check.value or 0)
            code: Code = "PRODUCT_MISSING" if count == 0 else "PRODUCT_DUPLICATED"
            codes.append(FailureCode(code=code, check=check.name, value=float(count)))
        elif check.name == "color_delta_e":
            per = [float(v) for v in _list(data, "per_colour")]
            i = max(range(len(per)), key=lambda k: per[k]) if per else 0
            ref = _list(data, "reference_colors")
            got = _list(data, "ad_colors")
            codes.append(
                FailureCode(
                    code="PRODUCT_COLOR_DRIFT",
                    check=check.name,
                    value=max(check.value or 0.0, float(data.get("worst_colour_delta_e") or 0.0)),
                    expected=_str(ref[i]) if i < len(ref) else None,
                    got=_str(got[i]) if i < len(got) else None,
                )
            )
        elif check.dimension == "product" and check.name.startswith("vlm_"):
            codes.append(FailureCode(code="PRODUCT_CHECK", check=check.name.removeprefix("vlm_")))
        elif check.dimension == "context" and check.name in gating_context:
            codes.append(FailureCode(code="CONTEXT_CHECK", check=check.name))
        elif check.name in (SCALE_CHECK, SANITY_CHECK, RATIO_CHECK):
            if check.name == RATIO_CHECK and any(c.code == "COMPOSITION_SCALE" for c in codes):
                continue
            # Over / under from the measured box when there is one (numbers, never VLM prose).
            ratio = data.get("ratio") if check.name == RATIO_CHECK else None
            from_ratio = (
                ("over" if ratio > 1 else "under") if isinstance(ratio, int | float) else None
            )
            direction = (
                _str(comp_signals.get("scale_direction"))
                or _str(data.get("direction"))
                or from_ratio
            )
            long_side = comp_signals.get("long_side") or data.get("long_side")
            codes.append(
                FailureCode(
                    code="COMPOSITION_SCALE",
                    check=check.name,
                    value=float(long_side) if isinstance(long_side, int | float) else None,
                    got=direction,
                )
            )
        elif check.name == INTEGRATION_CHECK:
            codes.append(FailureCode(code="COMPOSITION_INTEGRATION", check=check.name))
        elif check.name in ("ocr_cer", "vlm_readback"):
            if any(c.code == "TEXT_MISMATCH" for c in codes):
                continue
            codes.append(
                FailureCode(
                    code="TEXT_MISMATCH",
                    check=check.name,
                    value=check.value,
                    got=_str(text_signals.get("best_read")) or "",
                )
            )
        elif check.name == "critical_tokens":
            missing = _list(text_signals, "critical_tokens_missing")
            codes.append(
                FailureCode(
                    code="TEXT_TOKENS_MISSING", check=check.name, tokens=[str(t) for t in missing]
                )
            )
        elif check.name in ("stray_text", "vlm_stray_text"):
            words = _list(data, "words") or _list(data, "reads")
            tokens = [
                str(cast(dict[str, Any], w).get("text", "")) for w in words if isinstance(w, dict)
            ]
            codes.append(FailureCode(code="STRAY_TEXT", check=check.name, tokens=tokens))
        elif check.name == "overlay_pending":
            codes.append(FailureCode(code="OVERLAY_PENDING", check=check.name))
    return codes


def product_zone_overlap(evaluation: Evaluation, zone: NormBox) -> float:
    """Fraction of the product box (VLM box_2d, 0-1000) that lies inside the text zone."""
    product = evaluation.dimensions.get("product")
    box = _list(product.signals, "ad_box") if product else []
    if len(box) != 4:
        return 0.0
    ymin, xmin, ymax, xmax = (float(v) / 1000 for v in box)
    area = max(0.0, (xmax - xmin) * (ymax - ymin))
    if area <= 0:
        return 0.0
    ix = max(0.0, min(xmax, zone.x1) - max(xmin, zone.x0))
    iy = max(0.0, min(ymax, zone.y1) - max(ymin, zone.y0))
    return ix * iy / area


# --- prompt compilation ---------------------------------------------------------------------------

CONTEXT_PHRASES: dict[str, str] = {
    "ctx.season_cues": "the scene does not clearly show {season}",
    "ctx.no_season_contradiction": "the scene contains elements that contradict {season}",
    "ctx.geo_plausible": "the setting does not look like {country}",
    "ctx.no_geo_contradiction": "the scene shows signs of another country",
    "ctx.product_hero": "the product is not the clear focal point",
    "ctx.ad_composition": "the composition is cluttered",
    "ctx.brand_safe": "the image contains content that is not brand safe",
    "ctx.no_avoided_elements": "the image shows something this ad must avoid",
    "ctx.holiday_cue": "there are no tasteful cues of {holidays}",
}


def _join(items: list[str], empty: str) -> str:
    cleaned = [i.strip() for i in items if i.strip()]
    return ", ".join(cleaned) if cleaned else empty


def _quoted(items: list[str]) -> str:
    return ", ".join(f"«{escape_literal(t)}»" for t in items if t.strip())


def product_problems(codes: list[FailureCode]) -> list[str]:
    out: list[str] = []
    for c in codes:
        if c.code == "PRODUCT_COLOR_DRIFT":
            drift = f"product colour drifted (ΔE {c.value or 0:.0f})"
            if c.got and c.expected:
                drift += f": {product_color_name(c.got)} ({c.got}) instead of "
                drift += f"{product_color_name(c.expected)} ({c.expected})"
            out.append(drift)
        elif c.code == "PRODUCT_DUPLICATED":
            out.append(f"{int(c.value or 2)} copies of the product; keep only one")
        elif c.code == "PRODUCT_MISSING":
            out.append("the product is missing; add it back exactly as in the reference")
        elif c.code == "PRODUCT_CHECK":
            out.append(CHECKLIST_LABELS.get(c.check, c.check))
    return out


def render_repair_product(codes: list[FailureCode], facts: ReferenceFacts | None) -> str:
    colours = list(facts.dominant_colors) if facts else []
    labels = list(facts.visible_text) if facts else []
    return AD_REPAIR_PRODUCT.system.format(
        problems="; ".join(product_problems(codes)) or "the product does not match the reference",
        dominant_colors=product_colors_text(colours) or "as in the reference",
        colour_line=product_colour_line(colours),
        visible_text=("label text to preserve: " + _quoted(labels))
        if labels
        else "as in the reference",
    )


def render_repair_context(codes: list[FailureCode], spec: CreativeSpec) -> str:
    season = spec.season.effective_season.replace("_", " ")
    country = spec.geo.country_name
    holidays = ", ".join(spec.season.holidays) or "the holiday"
    failed = [c.check for c in codes if c.code == "CONTEXT_CHECK"]
    problems = [
        CONTEXT_PHRASES.get(cid, cid).format(season=season, country=country, holidays=holidays)
        for cid in failed
    ]
    remove: list[str] = []
    if {"ctx.no_season_contradiction", "ctx.no_geo_contradiction"} & set(failed):
        remove += spec.draft.contradiction_cues
    if {"ctx.brand_safe", "ctx.no_avoided_elements"} & set(failed):
        remove += spec.policy.avoid + spec.draft.cultural_avoid
    add = [*spec.draft.season_cues, *spec.draft.locale_cues]
    return AD_REPAIR_CONTEXT.system.format(
        country_name=country,
        effective_season=season,
        months_text=month_text(spec.season.months),
        problems="; ".join(problems) or "the scene does not match the season and place",
        remove=_join(remove, "anything that contradicts the season or place"),
        add=_join(add, f"{season} in {country}"),
    )


def composition_problems(codes: list[FailureCode], spec: CreativeSpec) -> list[str]:
    c = spec.composition
    expected = (
        f" (expected about {c.scale_min:.0%}-{c.scale_max:.0%})"
        if c.scale_min is not None and c.scale_max is not None
        else ""
    )
    out: list[str] = []
    scale = next((x for x in codes if x.code == "COMPOSITION_SCALE"), None)
    if scale is not None:
        size = {"over": "too large", "under": "too small"}.get(scale.got or "", "unrealistic")
        measured = (
            f": its longest side spans {scale.value:.0%} of the image height" if scale.value else ""
        )
        out.append(f"the product looks {size} for the scene{measured}{expected}")
    if any(x.code == "COMPOSITION_INTEGRATION" for x in codes):
        out.append(
            "the product looks pasted onto the scene rather than photographed in it (its "
            "lighting, shadow, perspective, focus or edges do not match the scene)"
        )
    return out


def render_repair_composition(
    codes: list[FailureCode], spec: CreativeSpec, facts: ReferenceFacts | None
) -> str:
    c = spec.composition
    size = facts.size() if facts else None
    size_cm = c.size_cm if c.size_cm is not None else (size.cm if size else None)
    what = (facts.category if facts and facts.category else "") or "product"
    refs = [r for r in c.scale_references if r.strip()]
    if size_cm and c.scale_min is not None and c.scale_max is not None:
        size_instruction = (
            f"at its realistic size: a real {what} about {size_cm:.0f} cm at its largest, so its "
            f"longest side spans about {c.scale_min:.0%}-{c.scale_max:.0%} of the image height"
        )
    elif size_cm:
        size_instruction = (
            f"at its realistic size: a real {what} about {size_cm:.0f} cm at its largest"
        )
    else:
        size_instruction = "at a realistic size"
    size_instruction += (
        f", in proportion to {_join(refs, '')} and the other objects around it"
        if refs
        else ", in proportion to the objects around it"
    )
    surface = c.resting_surface or (size.surface if size else "") or DEFAULT_SURFACE
    return AD_REPAIR_COMPOSITION.system.format(
        problems="; ".join(composition_problems(codes, spec))
        or "the product does not sit naturally in the scene",
        size_instruction=size_instruction,
        surface=surface,
    )


def render_repair_text(codes: list[FailureCode], spec: CreativeSpec) -> str:
    got = next((c.got for c in codes if c.code == "TEXT_MISMATCH" and c.got), "")
    stray = [t for c in codes if c.code == "STRAY_TEXT" for t in c.tokens]
    return AD_REPAIR_TEXT.system.format(
        zone_anchor=spec.text_zone.anchor,
        best_read=escape_line(got).strip() or "(unreadable)",
        n_lines=len(spec.required_text.lines),
        copy_lines=copy_lines(spec.required_text.lines, spec.required_text.raw),
        stray_block=STRAY_BLOCK.format(stray=_quoted(stray)) if stray else "",
    )


# --- the router -----------------------------------------------------------------------------------


class Repairer:
    def __init__(
        self,
        *,
        candidate_model: str,
        repair_model: str,
        max_repairs: int = 2,
        text_repair_attempts: int = 1,
    ) -> None:
        self.candidate_model = candidate_model
        self.repair_model = repair_model
        self.max_repairs = max_repairs
        self.text_repair_attempts = text_repair_attempts

    @classmethod
    def from_settings(cls, settings: Settings) -> "Repairer":
        return cls(
            candidate_model=settings.image_model_candidate,
            repair_model=settings.image_model_repair,
            max_repairs=settings.max_repairs,
            text_repair_attempts=settings.text_repair_attempts,
        )

    def _can_repair(self, state: RepairState) -> bool:
        return state.repairs_used < self.max_repairs and state.can_afford_image_call

    def _plan(
        self,
        evaluation: Evaluation,
        action: Action,
        row: str,
        reason: str,
        codes: list[FailureCode],
        *,
        prompt: Prompt | None = None,
        text: str | None = None,
        model: str | None = None,
        inputs: Inputs = "none",
        text_mode: Literal["native", "overlay"] = "native",
    ) -> RepairPlan:
        return RepairPlan(
            action=action,
            row=row,
            reason=reason,
            base_image_sha=evaluation.image_sha,
            model=model,
            prompt_name=prompt.name if prompt else None,
            prompt_version=prompt.version if prompt else None,
            prompt=text,
            inputs=inputs,
            text_mode=text_mode,
            codes=codes,
            counts_as_repair=model is not None,
            counts_as_text_repair=action == "repair_text",
        )

    def plan(
        self,
        evaluation: Evaluation,
        spec: CreativeSpec,
        *,
        facts: ReferenceFacts | None = None,
        state: RepairState | None = None,
    ) -> RepairPlan:
        state = state or RepairState()
        codes = failure_codes(evaluation)
        dims = evaluation.dimensions
        can = self._can_repair(state)
        overlay_mode = spec.required_text.mode == "overlay_only"
        plate_mode: Literal["native", "overlay"] = "overlay" if overlay_mode else "native"

        def fresh(mode: Literal["native", "overlay"]) -> str:
            return render_ad_prompt(prompt_fields(spec, facts, text_mode=mode))

        def stop(row: str, reason: str) -> RepairPlan:
            return self._plan(evaluation, "needs_review", row, reason, codes)

        if evaluation.passed:
            return self._plan(evaluation, "approve", "passed", "every dimension passed", codes)

        technical = dims.get("technical")
        if technical is None or technical.passed is not True:
            if not can:
                return stop("exhausted", "technical failure and no repair left")
            return self._plan(
                evaluation,
                "regenerate",
                "technical",
                "technical failure: generate a fresh candidate",
                codes,
                prompt=AD_GENERATE,
                text=fresh(plate_mode),
                model=self.candidate_model,
                inputs="reference",
                text_mode=plate_mode,
            )

        product, context = dims.get("product"), dims.get("context")
        if product is not None and product.passed is False:
            if not can:
                return stop("exhausted", "product still failing and no repair left")
            return self._plan(
                evaluation,
                "repair_product",
                "product",
                "product fidelity failed: restore the product from the reference",
                codes,
                prompt=AD_REPAIR_PRODUCT,
                text=render_repair_product(codes, facts),
                model=self.repair_model,
                inputs="reference+candidate",
                text_mode=plate_mode,
            )
        if context is not None and context.passed is False:
            if not can:
                return stop("exhausted", "context still failing and no repair left")
            return self._plan(
                evaluation,
                "repair_context",
                "context",
                "context failed: fix the listed contradictions and add the missing cues",
                codes,
                prompt=AD_REPAIR_CONTEXT,
                text=render_repair_context(codes, spec),
                model=self.repair_model,
                inputs="reference+candidate",
                text_mode=plate_mode,
            )
        composition = dims.get("composition")
        if composition is not None and composition.passed is False:
            if not can:
                return stop("exhausted", "composition still failing and no repair left")
            return self._plan(
                evaluation,
                "repair_composition",
                "composition",
                "composition failed: re-render the product at a realistic size, grounded and "
                "relit to match the scene",
                codes,
                prompt=AD_REPAIR_COMPOSITION,
                text=render_repair_composition(codes, spec, facts),
                model=self.repair_model,
                inputs="reference+candidate",
                text_mode=plate_mode,
            )
        # A missing composition dimension is an evaluation from before ev-0.6 (not judged);
        # a present but unverified one fails closed like product and context.
        if (
            product is None
            or context is None
            or product.passed is None
            or context.passed is None
            or (composition is not None and composition.passed is None)
        ):
            return stop(
                "unverified",
                "product, context or composition unverified (vision judge unavailable)",
            )

        # Only text is left (failed or unverified).
        in_zone = product_zone_overlap(evaluation, spec.text_zone.box) > ZONE_OVERLAP_MAX
        stray = any(c.code == "STRAY_TEXT" for c in codes)
        overlay_ok = not in_zone and not stray
        if stray or in_zone:
            if can:
                why = "stray text outside the zone" if stray else "the product overlaps the zone"
                return self._plan(
                    evaluation,
                    "clean_plate",
                    "text_outside_zone",
                    f"{why}: generate a clean plate with an empty zone, then overlay",
                    codes,
                    prompt=AD_GENERATE,
                    text=fresh("overlay"),
                    model=self.candidate_model,
                    inputs="reference",
                    text_mode="overlay",
                )
            return stop("exhausted", "text can't be overlaid (stray text or product in the zone)")
        if overlay_mode or any(c.code == "OVERLAY_PENDING" for c in codes):
            return self._plan(
                evaluation, "overlay", "overlay_only", "overlay-only text on a clean plate", codes
            )
        text = dims.get("text")
        wrong_in_zone = text is not None and text.passed is False
        if (
            wrong_in_zone
            and can
            and state.text_repairs_used < self.text_repair_attempts
            and overlay_ok
        ):
            return self._plan(
                evaluation,
                "repair_text",
                "text_in_zone",
                "the headline is wrong inside the zone: one text edit, then overlay",
                codes,
                prompt=AD_REPAIR_TEXT,
                text=render_repair_text(codes, spec),
                model=self.repair_model,
                inputs="candidate",
            )
        return self._plan(
            evaluation,
            "overlay",
            "text_exhausted",
            "text still failing (or unverified): deterministic overlay",
            codes,
        )

    @staticmethod
    def request(
        plan: RepairPlan, spec: CreativeSpec, *, reference: bytes, candidate: bytes
    ) -> ImageRequest:
        """The image call for a plan that calls a model (reference first, candidate second)."""
        if plan.prompt is None or plan.model is None:
            raise ValueError(f"plan {plan.action!r} makes no image call")
        images = {
            "reference": (reference,),
            "reference+candidate": (reference, candidate),
            "candidate": (candidate,),
            "none": (),
        }[plan.inputs]
        scene = spec.scene()
        if plan.text_mode == "overlay":
            scene = FakeScene(
                palette=scene.palette,
                headline_lines=(),
                zone=scene.zone,
                band_color=scene.band_color,
                text_color=scene.text_color,
                product_scale=scene.product_scale,
            )
        return ImageRequest(
            prompt=plan.prompt,
            images=images,
            aspect_ratio=spec.aspect_ratio,
            scene=scene,
            label=plan.action,
        )
