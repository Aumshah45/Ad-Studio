"""Reference-product fidelity (ai-design §5.3): deterministic colour + a VLM checklist (veto only).

The VLM (`ad_inspect`) locates the product (normalised `box_2d`) and answers the checklist. Code
crops that box and measures CIEDE2000 against the reference crop. Pass rule: exactly one product
AND delta_e <= `delta_e_max` AND no gating checklist item is `no`/`unsure`. Items listed in
`note_checks` (small label subtext) never fail: a `no`/`unsure` becomes a note on a passing check
("minor label detail degraded"), surfaced in the scorecard and counted in the eval report. The
dimension can only pass when the deterministic colour check itself passed: a favourable VLM can't
pardon a colour drift, and with no judge the product can't be located, so the dimension is
`unverified` (fails closed).
"""

from PIL import Image

from backend.domain.adstudio.evaluator.color import Box, compare_colours
from backend.domain.adstudio.evaluator.config import ProductConfig
from backend.domain.adstudio.evaluator.schemas import CheckResult, DimensionResult
from backend.domain.adstudio.evaluator.schemas import dimension_from_checks as _dimension
from backend.domain.adstudio.vision import AdInspection, DetectedProduct, valid_box
from backend.llm.prompts.ad_inspect import CHECKLIST_IDS

CHECKLIST_LABELS: dict[str, str] = {
    "same_product_type": "not the same type of product",
    "shape_proportions_preserved": "shape or proportions changed",
    "colors_preserved": "colours changed",
    "logo_preserved": "logo missing or altered",
    "main_label_text_preserved": "brand name or main label text missing or altered",
    "label_subtext_preserved": "minor label detail degraded",
    "not_distorted": "product distorted",
    "mostly_visible": "product cropped or hidden",
    "single_instance": "more than one copy of the product",
    "is_hero": "product is not the focal point",
}


def _area(box: Box) -> int:
    return (box[2] - box[0]) * (box[3] - box[1])


def primary_box(products: list[DetectedProduct]) -> Box | None:
    """The instance the VLM says matches the reference (largest first), else the largest."""
    boxes = [(p, b) for p in products if (b := valid_box(p.box_2d)) is not None]
    if not boxes:
        return None
    preferred = [b for p, b in boxes if p.matches_reference == "yes"] or [b for _, b in boxes]
    return max(preferred, key=_area)


def product_boxes(inspection: AdInspection | None) -> list[Box]:
    if inspection is None:
        return []
    return [b for p in inspection.products if (b := valid_box(p.box_2d)) is not None]


def unverified_product(reason: str) -> tuple[list[CheckResult], DimensionResult]:
    check = CheckResult(
        dimension="product", name="product_locate", method="vlm", passed=None, evidence=reason
    )
    return [check], _dimension("product", [check], score=0.0, low_confidence=True)


def evaluate_product(
    img: Image.Image,
    reference: Image.Image | None,
    *,
    reference_box: Box | None,
    inspection: AdInspection | None,
    unavailable_reason: str,
    cfg: ProductConfig,
) -> tuple[list[CheckResult], DimensionResult]:
    if reference is None:
        return unverified_product("No reference image; product fidelity can't be checked.")
    if inspection is None:
        return unverified_product(unavailable_reason)

    boxes = product_boxes(inspection)
    count = max(inspection.product_count, len(boxes))
    count_ok = 1 <= count <= cfg.max_count
    checks: list[CheckResult] = [
        CheckResult(
            dimension="product",
            name="product_count",
            method="vlm",
            passed=count_ok,
            value=float(count),
            threshold=float(cfg.max_count),
            evidence=(
                ""
                if count_ok
                else (
                    "The product was not found in the ad."
                    if count == 0
                    else f"{count} instances of the product found; exactly one is allowed."
                )
            ),
            data={"boxes": [list(b) for b in boxes]},
        )
    ]

    ad_box = primary_box(inspection.products)
    signals: dict[str, object] = {"product_count": count}
    hint: list[str] = []
    if count != 1:
        hint.append(f"{count} instances of product found" if count else "product missing")
    score = 0.0
    if ad_box is None:
        checks.append(
            CheckResult(
                dimension="product",
                name="color_delta_e",
                passed=None,
                evidence="No product box to measure colour against the reference.",
            )
        )
    else:
        cmp = compare_colours(reference, reference_box, img, ad_box, cfg)
        ok = cmp.delta_e <= cfg.delta_e_max and cmp.worst <= cfg.cluster_delta_e_max
        score = max(0.0, 1.0 - min(cmp.delta_e / cfg.delta_e_max, 1.0))
        worst_i = max(range(len(cmp.per_colour)), key=lambda i: cmp.per_colour[i])
        evidence = (
            f"Colour drift ΔE {cmp.delta_e:.1f} (worst colour ΔE {cmp.worst:.1f}): "
            f"{cmp.ad_colors[worst_i]} vs reference {cmp.reference_colors[worst_i]}."
        )
        checks.append(
            CheckResult(
                dimension="product",
                name="color_delta_e",
                passed=ok,
                value=cmp.delta_e,
                threshold=cfg.delta_e_max,
                evidence="" if ok else evidence,
                data={
                    "ad_box": list(ad_box),
                    "reference_box": list(reference_box) if reference_box else None,
                    "reference_colors": cmp.reference_colors,
                    "ad_colors": cmp.ad_colors,
                    "weights": cmp.weights,
                    "per_colour": cmp.per_colour,
                    "worst_colour_delta_e": cmp.worst,
                    "mask_fraction": [cmp.reference_mask, cmp.ad_mask],
                },
            )
        )
        drift = cmp.max_hue_drift
        drifted = drift is not None and drift >= cfg.hue_drift_note_deg
        worst_hue = (
            max(
                (i for i, d in enumerate(cmp.hue_drift) if d is not None),
                key=lambda i: cmp.hue_drift[i] or 0.0,
            )
            if drift is not None
            else None
        )
        checks.append(
            CheckResult(
                dimension="product",
                name="hue_drift",
                passed=True,  # measured only, never a gate (user ruling)
                value=drift,
                threshold=cfg.hue_drift_note_deg,
                evidence=(
                    f"Note (not a failure): hue drift {drift:.0f}° — "
                    f"{cmp.ad_colors[worst_hue]} vs reference {cmp.reference_colors[worst_hue]}."
                    if drifted and worst_hue is not None and drift is not None
                    else ""
                ),
                data={
                    "note": drifted,
                    "severity": "note",
                    "hue_drift": cmp.hue_drift,
                    "assessed": drift is not None,
                },
            )
        )
        signals |= {
            "hue_drift_deg": drift,
            "delta_e": cmp.delta_e,
            "worst_colour_delta_e": cmp.worst,
            "reference_colors": cmp.reference_colors,
            "ad_colors": cmp.ad_colors,
            "ad_box": list(ad_box),
        }
        if not ok:
            hint.append(
                f"colour drift ΔE {max(cmp.delta_e, cmp.worst):.0f}: {cmp.ad_colors[worst_i]} "
                f"instead of {cmp.reference_colors[worst_i]}"
            )

    answers = {c.id: c for c in inspection.checklist}
    low_confidence = False
    notes: list[str] = []
    for item in CHECKLIST_IDS:
        answer = answers.get(item)
        if item in cfg.note_checks:
            degraded = answer is not None and answer.verdict != "yes"
            note = ""
            if degraded and answer is not None:
                notes.append(CHECKLIST_LABELS[item])
                note = f"Note (not a failure): {CHECKLIST_LABELS[item]}. {answer.evidence}"
            checks.append(
                CheckResult(
                    dimension="product",
                    name=f"vlm_{item}",
                    method="vlm",
                    passed=True,  # a note, never a gate
                    evidence=note.strip()[:300],
                    data={
                        "verdict": answer.verdict if answer else None,
                        "note": degraded,
                        "severity": "note",
                    },
                )
            )
            continue
        if answer is None:
            passed: bool | None = None
            evidence = f"The judge gave no answer for {item}."
        elif answer.verdict == "yes":
            passed, evidence = True, ""
        elif answer.verdict == "unsure":
            low_confidence = True
            passed = False if cfg.unsure_fails else None
            evidence = f"Judge unsure: {CHECKLIST_LABELS[item]}? {answer.evidence}".strip()
        else:
            passed = False
            evidence = f"{CHECKLIST_LABELS[item]}: {answer.evidence}".strip()
        if passed is False:
            hint.append(CHECKLIST_LABELS[item])
        checks.append(
            CheckResult(
                dimension="product",
                name=f"vlm_{item}",
                method="vlm",
                passed=passed,
                evidence=evidence[:300],
                data={"verdict": answer.verdict if answer else None},
            )
        )
    signals["notes"] = notes
    dim = _dimension(
        "product",
        checks,
        score=score,
        low_confidence=low_confidence,
        signals=signals,
        repair_hint="; ".join(hint) or None,
    )
    if dim.passed:
        dim.repair_hint = None
    return checks, dim
