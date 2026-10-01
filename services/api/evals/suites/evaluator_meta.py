"""Evaluator meta-eval (ai-design §9.2): does the evaluator agree with the labels?

Inputs are `ItemRecord`s (labels + the evaluator's per-dimension verdicts). Positive class = FAIL.
- Per-dimension P/R/F1 and confusion matrices on E-nat ∪ E-plant (labelled items only). An
  `unverified` dimension counts as a FAIL (it fails closed); a dimension the evaluator did not
  assess (a technical failure short-circuits the rest) is left out and counted as `not_assessed`.
- The same split into calibration (P1–P3) and held-out (P4–P5) products (§9.4).
- Human agreement on E-nat (overall verdict: every dimension passes) and per-dimension agreement
  with Cohen's κ on E-nat and E-final.
- Planted catch rate per class (the expected dimension flagged) and known-good controls passing.
"""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from backend.domain.adstudio.evalreport import (
    AgreementMetrics,
    DimensionMetrics,
    ItemRecord,
    Outcome,
    PlantedClassMetrics,
    RateMetric,
)
from evals.metrics import agreement, cohen_kappa, confusion, outcome, prf
from evals.report import Target

NAME = "evaluator_meta"
CORE_DIMENSIONS = ("technical", "text", "product", "context")
DIMENSIONS = (*CORE_DIMENSIONS, "composition")  # composition: ADR-007 (rubric v2 labels)
TARGET_DIMENSIONS = ("text", "product", "context", "composition")
META_SETS = ("nat", "plant")  # the labelled set the P/R targets are measured on (§9.2)
TARGETS: dict[str, Target] = {
    **{f"recall.{d}": (">=", 0.90) for d in TARGET_DIMENSIONS},
    **{f"precision.{d}": (">=", 0.85) for d in TARGET_DIMENSIONS},
    "human_agreement": (">=", 0.85),
    "known_good": (">=", 1.0),
}


# The analysis' disputed rulings the human kept as originally labelled and reports as disputed
# (TECHNICAL_REPORT.md §8; analysis-2026-09-26.md L3/L5). Planted items derived from a disputed
# image inherit its label for that dimension, so they are disputed too. Labels are never changed;
# the report shows P/R both as labelled (the criterion) and with these pairs left out.
DISPUTED: dict[str, dict[str, str]] = {
    "v1": {
        "B12-nat": "text",  # drawn quote marks around "Primavera" (all readers see them)
        "B06-nat": "product",  # the label reads "GBOUNDUNT"
    },
    "v2": {
        "B13-nat": "text",  # the line-break marker drawn: "Only / AED 49"
        "B16-nat": "text",  # a stray opening quote: "‘Merry Christmas, Mate!"
    },
}


def disputed_pairs(items: Iterable[ItemRecord], version: str | None) -> set[tuple[str, str]]:
    """(item id, dimension) pairs whose label is a disputed pass ruling (or inherits one)."""
    rulings = DISPUTED.get(version or "", {})
    out: set[tuple[str, str]] = set()
    for item in items:
        for origin in (item.id, item.source_id):
            dim = rulings.get(origin or "")
            if dim and item.labels and item.labels.get(dim) is True:
                out.add((item.id, dim))
    return out


def pred_fail(item: ItemRecord, dim: str) -> bool | None:
    """Evaluator says FAIL (unverified counts as FAIL); None when the dimension wasn't assessed."""
    if dim not in item.dimensions:
        return None
    return item.dimensions[dim] is not True


def label_fail(item: ItemRecord, dim: str) -> bool | None:
    if item.labels is None:
        return None
    value = item.labels.get(dim)
    return None if value is None else value is False


def labelled_dims(item: ItemRecord) -> tuple[str, ...]:
    """The dimensions this item's label covers: the four core ones, plus composition once it is
    labelled (rubric v2). Rubric-1 labels predate composition, so it is left out of both sides of
    the overall comparison rather than counted as a disagreement."""
    if item.labels is None:
        return ()
    extra = ("composition",) if item.labels.get("composition") is not None else ()
    return (*CORE_DIMENSIONS, *extra)


def label_overall(item: ItemRecord) -> bool | None:
    """Human overall verdict: pass when every labelled dimension passes; None when a core
    dimension is unlabelled."""
    if item.labels is None:
        return None
    values = [item.labels.get(d) for d in labelled_dims(item)]
    if any(v is False for v in values):
        return False
    return True if all(v is True for v in values) else None


def pred_overall(item: ItemRecord) -> bool:
    """The evaluator's overall verdict on the same dimensions the label covers (a technical
    failure fails the whole image)."""
    dims = labelled_dims(item) or DIMENSIONS
    if item.dimensions.get("technical") is not True:
        return False
    return all(pred_fail(item, d) is False for d in dims if d in item.dimensions)


def with_outcomes(item: ItemRecord) -> ItemRecord:
    outcomes: dict[str, Outcome | None] = {}
    for dim in DIMENSIONS:
        lf, pf = label_fail(item, dim), pred_fail(item, dim)
        outcomes[dim] = None if lf is None or pf is None else outcome(lf, pf)
    return item.model_copy(update={"outcomes": outcomes})


def dimension_metrics(
    items: Iterable[ItemRecord], dim: str, skip: set[tuple[str, str]] | None = None
) -> DimensionMetrics:
    pairs: list[tuple[bool, bool]] = []
    not_assessed = unlabelled = 0
    for item in items:
        if skip and (item.id, dim) in skip:
            continue
        lf, pf = label_fail(item, dim), pred_fail(item, dim)
        if lf is None:
            unlabelled += 1
        elif pf is None:
            not_assessed += 1
        else:
            pairs.append((lf, pf))
    c = confusion(pairs)
    precision, recall, f1 = prf(c)
    return DimensionMetrics(
        n=len(pairs),
        confusion=c,
        precision=precision,
        recall=recall,
        f1=f1,
        not_assessed=not_assessed,
        unlabelled=unlabelled,
    )


def _agreement(pairs: Sequence[tuple[bool, bool]]) -> AgreementMetrics:
    agree, rate = agreement(pairs)
    return AgreementMetrics(n=len(pairs), agree=agree, agreement=rate, kappa=cohen_kappa(pairs))


def human_agreement(items: Iterable[ItemRecord]) -> AgreementMetrics:
    """E-nat overall pass/fail: evaluator verdict `pass` vs the human's all-dimensions pass."""
    pairs: list[tuple[bool, bool]] = []
    for item in items:
        if item.set != "nat":
            continue
        human = label_overall(item)
        if human is None or item.label_source != "human":
            continue
        pairs.append((human, pred_overall(item)))
    return _agreement(pairs)


def dimension_agreement(items: Iterable[ItemRecord], image_set: str) -> dict[str, AgreementMetrics]:
    out: dict[str, AgreementMetrics] = {}
    chosen = [i for i in items if i.set == image_set and i.label_source == "human"]
    for dim in DIMENSIONS:
        pairs: list[tuple[bool, bool]] = []
        for item in chosen:
            lf, pf = label_fail(item, dim), pred_fail(item, dim)
            if lf is not None and pf is not None:
                pairs.append((not lf, not pf))
        out[dim] = _agreement(pairs)
    return out


def planted_classes(items: Iterable[ItemRecord]) -> tuple[list[PlantedClassMetrics], RateMetric]:
    groups: dict[str, list[ItemRecord]] = defaultdict(list)
    for item in items:
        if item.set == "plant" and item.mutation:
            groups[item.mutation].append(item)
    classes: list[PlantedClassMetrics] = []
    known = RateMetric()
    for mutation in sorted(groups):
        members = groups[mutation]
        if mutation == "control_good":
            ok = [i for i in members if i.verdict == "pass"]
            known = RateMetric(n=len(members), ok=len(ok), rate=len(ok) / len(members))
            continue
        expected = sorted(
            {d for i in members for d in DIMENSIONS if i.labels and i.labels.get(d) is False}
        )
        caught: list[ItemRecord] = []
        missed: list[str] = []
        for item in members:
            fails = [d for d in DIMENSIONS if item.labels and item.labels.get(d) is False]
            if fails and all(pred_fail(item, d) is True for d in fails):
                caught.append(item)
            else:
                missed.append(item.id)
        classes.append(
            PlantedClassMetrics(
                mutation=mutation,
                expected=expected,
                n=len(members),
                caught=len(caught),
                rate=len(caught) / len(members) if members else None,
                missed=missed,
            )
        )
    return classes, known


@dataclass
class MetaResult:
    items: list[ItemRecord]
    per_dimension: dict[str, DimensionMetrics]
    per_split: dict[str, dict[str, DimensionMetrics]]
    human_agreement: AgreementMetrics
    agreement: dict[str, dict[str, AgreementMetrics]]
    planted: list[PlantedClassMetrics]
    known_good: RateMetric
    disputed: list[str]
    per_dimension_undisputed: dict[str, DimensionMetrics]


def compute(items: Sequence[ItemRecord], version: str | None = None) -> MetaResult:
    """Everything the report needs from the labelled items (items get their outcomes filled)."""
    records = [with_outcomes(i) for i in items]
    meta = [i for i in records if i.set in META_SETS]
    planted, known = planted_classes(records)
    skip = disputed_pairs(meta, version)
    return MetaResult(
        items=records,
        per_dimension={d: dimension_metrics(meta, d) for d in DIMENSIONS},
        per_split={
            split: {
                d: dimension_metrics([i for i in meta if i.split == split], d) for d in DIMENSIONS
            }
            for split in ("calibration", "heldout")
        },
        human_agreement=human_agreement(records),
        agreement={s: dimension_agreement(records, s) for s in ("nat", "final")},
        planted=planted,
        known_good=known,
        disputed=sorted(f"{item}:{dim}" for item, dim in skip),
        per_dimension_undisputed={d: dimension_metrics(meta, d, skip) for d in DIMENSIONS},
    )
