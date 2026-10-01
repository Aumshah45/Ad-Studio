"""Pipeline quality over the golden briefs (ai-design §9.2, PRD success criteria).

From the E-final manifest rows (one per brief, with the run's status, outcome, repairs, cost and
latency from the ledger) and the replayed evaluation of each final image:
- first-attempt pass rate (a round-1 candidate passed every dimension) vs after-repair pass rate
  (the run ended `passed`, or a human approved it);
- shipped exact text: shipped ads whose OCR read equals the required text (CER 0 after
  normalisation), plus those verified by construction (no OCR pack for the script), reported
  separately; native-text rate (shipped without the overlay), B20 excluded (reconciliation);
- mean repairs per brief, $ per approved ad (all golden spend / shipped ads), p50/p95 latency of the
  shipped runs, and the 1024 px resolution cap over every stored output image;
- hue-drift rate (ev-0.8): final images whose product carries a hue-drift note (measured only).
- minor-label-detail rate: final images whose product check carries a note (small label subtext
  degraded, recorded but never a failure), over the evaluated final images.
"""

from collections import Counter
from collections.abc import Mapping, Sequence

from backend.domain.adstudio.evalreport import CheckRecord, ItemRecord, PipelineMetrics
from backend.golden.dataset import OutputItem
from evals.metrics import mean, percentile, ratio
from evals.report import Target

NAME = "pipeline_quality"
SHIPPED = ("passed", "approved")
MAX_EDGE = 1024
TARGETS: dict[str, Target] = {
    "after_repair_pass_rate": (">=", 0.90),
    "text_exact_rate": (">=", 1.0),
    "cost_per_approved_ad": ("<=", 0.25),
    "p50_latency_ms": ("<=", 60_000),
    "p95_latency_ms": ("<=", 120_000),
    "resolution_ok_rate": (">=", 1.0),
}


def exact_by_ocr(item: ItemRecord | None) -> bool:
    if item is None:
        return False
    return any(c.name == "ocr_cer" and c.passed is True and c.value == 0 for c in item.checks)


def _hue_measured(check: CheckRecord) -> bool:
    return check.name == "hue_drift" and check.value is not None


# The product checklist's note item (small label subtext degraded).
LABEL_NOTES = frozenset({"vlm_label_subtext_preserved"})


def compute(
    outputs: Sequence[OutputItem],
    evaluated: Mapping[str, ItemRecord],
    sizes: Sequence[tuple[int, int] | None],
) -> PipelineMetrics:
    finals = [o for o in outputs if o.set == "final"]
    briefs = len(finals)
    shipped = [o for o in finals if o.run.status in SHIPPED]
    exact_ocr = sum(1 for o in shipped if exact_by_ocr(evaluated.get(o.id)))
    exact_construction = sum(
        1
        for o in shipped
        if not exact_by_ocr(evaluated.get(o.id)) and o.text_verification == "construction"
    )
    native_pool = [o for o in shipped if not o.exclude_native_text]
    native = sum(1 for o in native_pool if o.run.outcome == "native")
    latencies = [float(o.run.latency_ms) for o in shipped if o.run.latency_ms is not None]
    total_cost = sum(o.run.cost_usd for o in finals)
    checked = [s for s in sizes if s is not None]
    final_items = [r for o in finals if (r := evaluated.get(o.id)) is not None]
    noted = sum(1 for r in final_items if any(c.note and c.name in LABEL_NOTES for c in r.checks))
    hue_assessed = [r for r in final_items if any(c.name == "hue_drift" for c in r.checks)]
    hue_assessed = [
        r
        for r in hue_assessed
        if any(c.name == "hue_drift" and c.value is not None for c in r.checks)
    ]
    hue_noted = [r for r in hue_assessed if any(c.name == "hue_drift" and c.note for c in r.checks)]
    by_product: dict[str, list[bool]] = {}
    for r in hue_assessed:
        by_product.setdefault(r.product_id, []).append(r in hue_noted)
    return PipelineMetrics(
        briefs=briefs,
        status_counts=dict(Counter(o.run.status for o in finals)),
        first_attempt_pass_rate=ratio(sum(1 for o in finals if o.run.first_attempt_pass), briefs),
        after_repair_pass_rate=ratio(len(shipped), briefs),
        shipped=len(shipped),
        text_exact_rate=ratio(exact_ocr + exact_construction, len(shipped)),
        text_exact_ocr=exact_ocr,
        text_exact_construction=exact_construction,
        native_text_rate=ratio(native, len(native_pool)),
        native_text_n=len(native_pool),
        mean_repairs=mean([float(o.run.repair_count) for o in finals]),
        total_cost_usd=round(total_cost, 6),
        cost_per_approved_ad=ratio(total_cost, len(shipped)),
        p50_latency_ms=percentile(latencies, 50),
        p95_latency_ms=percentile(latencies, 95),
        resolution_ok_rate=ratio(sum(1 for w, h in checked if max(w, h) <= MAX_EDGE), len(checked)),
        images_checked=len(checked),
        minor_label_detail_rate=ratio(noted, len(final_items)),
        minor_label_detail_n=len(final_items),
        hue_drift_rate=ratio(len(hue_noted), len(hue_assessed)),
        hue_drift_n=len(hue_assessed),
        hue_drift_by_product={
            pid: round(sum(v) / len(v), 3) for pid, v in sorted(by_product.items()) if v
        },
    )
