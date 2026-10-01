"""Composition (ADR-007): is the product realistically sized and naturally integrated in the scene?

Two must-pass VLM checks (`composition_judge`, evidence first, temperature 0), compiled by code
from the spec and the product profile:
- `comp.realistic_scale`: the product's apparent size is plausible next to named scene objects,
  given its real-world size (the evidence names the objects compared);
- `comp.natural_integration`: light direction and colour temperature, grounding (contact shadow),
  perspective, focus / depth of field and edges (no cut-out outline or halo) match the scene.
`unsure` fails, flagged low_confidence (like context).

ev-0.7 (`composition_judge` v2, analysis R3): before answering, the judge names the scene object
whose real size is most certain, gives its size in cm and the product's size in cm implied by it.
`comp.scale_ratio` is code on those numbers: implied / expected (the product profile's size) must
lie within `scale_ratio_min`-`scale_ratio_max` (tuned on the calibration products only); no
estimate -> not applicable. The judge also lists up to 3 signs the product was pasted in; with
`pasted_signs_max` set, more signs than that fail `comp.natural_integration`.

One deterministic signal supports them: `comp.scale_sanity`, the product box's area fraction (from
`ad_inspect`) against the range expected for the product's size and the scene's framing
(`sizing.py`). Outside the range it is a note on a passing check; only an extreme deviation
(`area_extreme_over` / `area_extreme_under`) fails. No judge -> the VLM checks are `unverified`
(the run fails closed to needs_review).
"""

from dataclasses import dataclass
from typing import Any, Literal

from backend.domain.adstudio.evaluator.color import Box
from backend.domain.adstudio.evaluator.config import CompositionConfig
from backend.domain.adstudio.evaluator.schemas import CheckResult, DimensionResult
from backend.domain.adstudio.evaluator.schemas import dimension_from_checks as _dimension
from backend.domain.adstudio.sizing import (
    FRAMING_TEXT,
    Framing,
    as_framing,
    default_framing,
    expected_area_range,
    plan_scale,
)
from backend.domain.adstudio.vision import CompositionVerdict, ContextCheck, ScaleEstimate

SCALE_CHECK = "comp.realistic_scale"
INTEGRATION_CHECK = "comp.natural_integration"
SANITY_CHECK = "comp.scale_sanity"
RATIO_CHECK = "comp.scale_ratio"
VLM_CHECKS = (SCALE_CHECK, INTEGRATION_CHECK)


@dataclass(frozen=True)
class CompositionTarget:
    """What the product should look like in the scene (spec composition + product profile)."""

    category: str = ""
    size_cm: float | None = None
    size_class: str | None = None
    framing: str | None = None  # the spec's framing; None for pre-ADR-007 specs
    scale_min: float | None = None
    scale_max: float | None = None
    scale_references: tuple[str, ...] = ()
    resting_surface: str | None = None

    def expected_scale(self) -> tuple[Framing, float, float, Literal["spec", "default"]] | None:
        """(framing, scale_min, scale_max, source): the spec's, else the default framing for the
        product's size (older specs); None when the size is unknown."""
        if self.size_cm is None:
            return None
        framing = as_framing(self.framing)
        if framing is not None and self.scale_min is not None and self.scale_max is not None:
            return framing, self.scale_min, self.scale_max, "spec"
        plan = plan_scale(self.size_cm, default_framing(self.size_cm))
        return plan.framing, plan.scale_min, plan.scale_max, "default"


def _refs(items: tuple[str, ...]) -> str:
    cleaned = [i.strip() for i in items if i.strip()]
    return f" (for example {', '.join(cleaned)})" if cleaned else ""


def composition_checks(target: CompositionTarget) -> list[ContextCheck]:
    what = target.category.strip() or "product"
    size = (
        f"about {target.size_cm:.0f} cm at its largest in real life"
        if target.size_cm
        else "the size such a product usually is in real life"
    )
    expected = target.expected_scale()
    framing = f" The photo is meant to be {FRAMING_TEXT[expected[0]]}." if expected else ""
    return [
        ContextCheck(
            id=SCALE_CHECK,
            question=(
                f"The product is a {what}, {size}.{framing} Compare its apparent size with the "
                f"other objects in the scene{_refs(target.scale_references)} and anything else of "
                "known size (furniture, tableware, plants, hands). Is the product's size plausible "
                "relative to those objects? Answer no if it looks noticeably too big (e.g. "
                "towering over a chair or a table) or too small for its surroundings."
            ),
            severity="must",
            pass_on="yes",  # noqa: S106 - a verdict, not a password
        ),
        ContextCheck(
            id=INTEGRATION_CHECK,
            question=(
                "Does the product look photographed in this scene rather than pasted onto it? All "
                "of these must hold: it is lit by the scene's light (same light direction and "
                "colour temperature); it rests on a surface with a contact shadow and does not "
                "float; its perspective and camera angle match the scene; its focus matches the "
                "depth of field of objects at the same distance; and it has no cut-out outline, "
                "halo or fringe of another background."
            ),
            severity="must",
            pass_on="yes",  # noqa: S106 - a verdict, not a password
        ),
    ]


def scale_sanity(
    target: CompositionTarget,
    box: Box | None,
    size: tuple[int, int],
    cfg: CompositionConfig,
) -> CheckResult:
    """The product box's area fraction vs the expected range (supporting evidence)."""
    expected = target.expected_scale()
    if box is None or expected is None:
        why = "no product box" if box is None else "the product's real-world size is unknown"
        return CheckResult(
            dimension="composition",
            name=SANITY_CHECK,
            passed=True,
            evidence=f"Not applicable ({why}).",
            data={"applicable": False},
        )
    framing, smin, smax, source = expected
    w, h = size
    ymin, xmin, ymax, xmax = box
    bw, bh = (xmax - xmin) / 1000 * w, (ymax - ymin) / 1000 * h
    area = (xmax - xmin) * (ymax - ymin) / 1_000_000
    long_side = max(bw, bh) / h
    ratio = min(bw, bh) / max(bw, bh) if max(bw, bh) > 0 else 1.0
    lo, hi = expected_area_range(smin, smax, aspect=w / h, product_aspect=ratio)
    over, under = area / hi if hi else 0.0, area / lo if lo else 0.0
    direction: str | None = "over" if area > hi else ("under" if area < lo else None)
    extreme = over > cfg.area_extreme_over or under < cfg.area_extreme_under
    size_cm = target.size_cm or 0.0
    measured = (
        f"Product box covers {area:.0%} of the image (longest side {long_side:.0%} of the "
        f"height); expected about {lo:.0%}-{hi:.0%} ({smin:.0%}-{smax:.0%}) for a "
        f"{size_cm:.0f} cm product in a {framing.replace('_', '-')} shot"
    )
    if extreme:
        evidence = f"{measured}: far too {'large' if direction == 'over' else 'small'}."
    elif direction is not None:
        evidence = (
            f"Note (not a failure): {measured}; somewhat too "
            f"{'large' if direction == 'over' else 'small'}."
        )
    else:
        evidence = f"{measured}."
    data: dict[str, Any] = {
        "applicable": True,
        "area": round(area, 4),
        "expected_area": [round(lo, 4), round(hi, 4)],
        "long_side": round(long_side, 3),
        "expected_scale": [smin, smax],
        "framing": framing,
        "framing_source": source,
        "size_cm": size_cm,
        "direction": direction,
        "ratio": round(over if direction == "over" else under, 3) if direction else 1.0,
        "note": bool(direction) and not extreme,
        "ad_box": list(box),
    }
    return CheckResult(
        dimension="composition",
        name=SANITY_CHECK,
        passed=not extreme,
        value=round(area, 4),
        threshold=round(hi, 4),
        evidence=evidence,
        data=data,
    )


def scale_ratio(
    target: CompositionTarget, estimate: ScaleEstimate | None, cfg: CompositionConfig
) -> CheckResult:
    """implied / expected product size from the judge's measurement against its most certain
    scene object; fails outside [scale_ratio_min, scale_ratio_max]."""
    expected = target.size_cm
    implied = estimate.product_implied_cm if estimate else None
    ref_cm = estimate.reference_size_cm if estimate else None
    obj = (estimate.reference_object if estimate else "").strip()[:80]
    if not expected or not implied or implied <= 0 or not obj:
        why = (
            "the product's real-world size is unknown"
            if not expected
            else "the judge named no scene object of known size"
        )
        return CheckResult(
            dimension="composition",
            name=RATIO_CHECK,
            method="vlm",
            passed=True,
            evidence=f"Not applicable ({why}).",
            data={"applicable": False, "reference_object": obj or None},
        )
    ratio = implied / expected
    lo, hi = cfg.scale_ratio_min, cfg.scale_ratio_max
    inside = lo <= ratio <= hi
    measured = (
        f"Next to the {obj} ({ref_cm:.0f} cm)" if ref_cm else f"Next to the {obj}"
    ) + f", the product reads as about {implied:.0f} cm; its real size is {expected:.0f} cm"
    verdict = (
        f"ratio {ratio:.2f}, within {lo:.2f}-{hi:.2f}."
        if inside
        else f"ratio {ratio:.2f}: too {'large' if ratio > hi else 'small'} "
        f"(allowed {lo:.2f}-{hi:.2f})."
    )
    return CheckResult(
        dimension="composition",
        name=RATIO_CHECK,
        method="vlm",
        passed=inside,
        value=round(ratio, 3),
        threshold=hi if ratio > hi else lo,
        evidence=f"{measured}: {verdict}",
        data={
            "applicable": True,
            "reference_object": obj,
            "reference_size_cm": ref_cm,
            "product_implied_cm": implied,
            "expected_cm": expected,
            "ratio": round(ratio, 3),
            "range": [lo, hi],
        },
    )


def unverified_composition(
    reason: str, sanity: CheckResult | None = None
) -> tuple[list[CheckResult], DimensionResult]:
    check = CheckResult(
        dimension="composition",
        name="composition_judge",
        method="vlm",
        passed=None,
        evidence=reason,
    )
    checks = [check, *([sanity] if sanity is not None else [])]
    return checks, _dimension("composition", checks, score=0.0, low_confidence=True)


def evaluate_composition(
    checks: list[ContextCheck],
    verdict: CompositionVerdict | None,
    sanity: CheckResult,
    *,
    unavailable_reason: str,
    cfg: CompositionConfig,
    target: CompositionTarget | None = None,
) -> tuple[list[CheckResult], DimensionResult]:
    if verdict is None:
        return unverified_composition(unavailable_reason, sanity)
    target = target or CompositionTarget()
    answers = {v.id: v for v in verdict.checks}
    results: list[CheckResult] = []
    low_confidence = False
    signs = [s.strip()[:120] for s in verdict.pasted_signs if s.strip()][:3]
    too_many_signs = cfg.pasted_signs_max is not None and len(signs) > cfg.pasted_signs_max
    for check in checks:
        answer = answers.get(check.id)
        if answer is None:
            passed: bool | None = None
            evidence = "The judge gave no answer for this check."
        elif answer.verdict == "unsure":
            low_confidence = True
            passed = False if cfg.unsure_fails else None
            evidence = f"Judge unsure. {answer.evidence}".strip()
        else:
            passed = answer.verdict == check.pass_on
            evidence = answer.evidence
        data: dict[str, Any] = {
            "severity": check.severity,
            "pass_on": check.pass_on,
            "question": check.question,
            "verdict": answer.verdict if answer else None,
        }
        if check.id == INTEGRATION_CHECK:
            data["pasted_signs"] = signs
            if too_many_signs and passed is not False:
                passed = False
                evidence = f"{len(signs)} signs of pasting: " + "; ".join(signs)
        results.append(
            CheckResult(
                dimension="composition",
                name=check.id,
                method="vlm",
                passed=passed,
                evidence=evidence[:300],
                data=data,
            )
        )
    ratio_check = scale_ratio(target, verdict.scale, cfg)
    gating = [*results, ratio_check, sanity]
    vlm_passed = sum(1 for r in results if r.passed is True)
    failed = [c.name for c in gating if c.passed is False]
    sdata = sanity.data or {}
    dim = _dimension(
        "composition",
        gating,
        score=vlm_passed / len(results) if results else 0.0,
        low_confidence=low_confidence,
        signals={
            "realistic_scale": next((r.passed for r in results if r.name == SCALE_CHECK), None),
            "natural_integration": next(
                (r.passed for r in results if r.name == INTEGRATION_CHECK), None
            ),
            "scale_ratio": (ratio_check.data or {}).get("ratio"),
            "scale_reference": (ratio_check.data or {}).get("reference_object"),
            "pasted_signs": signs,
            "scale_direction": sdata.get("direction"),
            "long_side": sdata.get("long_side"),
            "expected_scale": sdata.get("expected_scale"),
            "notes": [sanity.evidence] if sdata.get("note") else [],
        },
        repair_hint=("failed composition checks: " + ", ".join(failed)) if failed else None,
    )
    return gating, dim
