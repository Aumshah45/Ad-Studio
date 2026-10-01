"""The golden eval report (`evals/reports/<ts>.json`, `latest.json`, `eval_reports.metrics`).

One typed shape for the writer (`python -m evals`), the importer and `/v1/evals/*`. For the
evaluator the positive class is FAIL (ai-design §9.2): TP = the evaluator failed a dimension the
label says fails.
"""

import uuid
from datetime import datetime
from typing import Any, Literal, cast

from pydantic import BaseModel, Field

Outcome = Literal["tp", "fp", "fn", "tn"]
REPORT_VERSION = 1


class Confusion(BaseModel):
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    @property
    def n(self) -> int:
        return self.tp + self.fp + self.fn + self.tn


class DimensionMetrics(BaseModel):
    n: int = 0
    confusion: Confusion = Field(default_factory=Confusion)
    precision: float | None = None
    recall: float | None = None
    f1: float | None = None
    not_assessed: int = Field(
        default=0, description="Items where this dimension was not evaluated (technical failed)"
    )
    unlabelled: int = 0


class AgreementMetrics(BaseModel):
    n: int = 0
    agree: int = 0
    agreement: float | None = None
    kappa: float | None = None


class PlantedClassMetrics(BaseModel):
    mutation: str
    expected: list[str]
    n: int
    caught: int
    rate: float | None
    missed: list[str] = Field(default_factory=list[str])


class PipelineMetrics(BaseModel):
    briefs: int = 0
    status_counts: dict[str, int] = Field(default_factory=dict[str, int])
    first_attempt_pass_rate: float | None = None
    after_repair_pass_rate: float | None = None
    shipped: int = 0
    text_exact_rate: float | None = None
    text_exact_ocr: int = 0
    text_exact_construction: int = 0
    native_text_rate: float | None = None
    native_text_n: int = 0
    mean_repairs: float | None = None
    total_cost_usd: float = 0.0
    cost_per_approved_ad: float | None = None
    p50_latency_ms: float | None = None
    p95_latency_ms: float | None = None
    resolution_ok_rate: float | None = None
    images_checked: int = 0
    # Final images whose product check carries a note (small label subtext degraded; not a fail).
    minor_label_detail_rate: float | None = None
    minor_label_detail_n: int = 0
    # ev-0.8: final images whose product carries a hue-drift note (a chromatic dominant colour's
    # hue angle moved >= product.hue_drift_note_deg; measured only, never a fail), over the final
    # images where a chromatic colour could be measured; with the rate per product.
    hue_drift_rate: float | None = None
    hue_drift_n: int = 0
    hue_drift_by_product: dict[str, float] = Field(default_factory=dict[str, float])


class StabilityMetrics(BaseModel):
    available: bool = False
    items: int = 0
    checks: int = 0
    flips: int = 0
    flip_rate: float | None = None


class RateMetric(BaseModel):
    n: int = 0
    ok: int = 0
    rate: float | None = None


class Criterion(BaseModel):
    id: str
    criterion: str
    metric: str
    target: str
    baseline: float | None = None
    now: float | None = None
    met: bool | None = None
    note: str = ""


class CheckRecord(BaseModel):
    dimension: str
    name: str
    method: str = "deterministic"
    passed: bool | None = None
    value: float | None = None
    threshold: float | None = None
    evidence: str = ""
    note: bool = False  # a non-failing note (e.g. minor label detail degraded)


# Ablation A1: {layer (deterministic | vlm | combined): {dimension: evaluator passes?}}.
LayerVerdicts = dict[str, dict[str, bool | None]]


class ItemRecord(BaseModel):
    id: str
    set: Literal["nat", "final", "plant"]
    brief_id: str
    product_id: str
    split: Literal["calibration", "heldout"]
    image_sha: str
    source_id: str | None = None
    source_sha: str | None = None
    mutation: str | None = None
    label_source: Literal["human", "planted", "assumed", "none"]
    labels: dict[str, bool | None] | None = None
    verdict: Literal["pass", "fail", "unverified"]
    dimensions: dict[str, bool | None]
    outcomes: dict[str, Outcome | None] = Field(default_factory=dict[str, Outcome | None])
    evidence: list[str] = Field(default_factory=list[str])
    checks: list[CheckRecord] = Field(default_factory=list[CheckRecord])
    layers: LayerVerdicts = Field(
        default_factory=LayerVerdicts,
        description="Ablation A1: each layer's verdict per dimension (missing = not assessed)",
    )


class VersionSummary(BaseModel):
    """One golden version's headline numbers for the v1 -> v2 comparison (ADR-007)."""

    version: str
    evaluator_version: str
    pipeline_versions: list[str] = Field(default_factory=list[str])
    counts: dict[str, int] = Field(default_factory=dict[str, int])
    human_pass_rates: dict[str, dict[str, RateMetric]] = Field(
        default_factory=dict[str, dict[str, RateMetric]],
        description="set (nat|final) -> dimension -> share of human-labelled images passing",
    )
    evaluator_pass_rates: dict[str, dict[str, RateMetric]] = Field(
        default_factory=dict[str, dict[str, RateMetric]],
        description="set (nat|final) -> dimension -> share of images the evaluator passes",
    )
    per_dimension: dict[str, DimensionMetrics] = Field(default_factory=dict[str, DimensionMetrics])
    human_agreement: AgreementMetrics = Field(default_factory=AgreementMetrics)
    after_repair_pass_rate: float | None = None
    first_attempt_pass_rate: float | None = None
    cost_per_approved_ad: float | None = None


class GoldenComparison(BaseModel):
    """v1 (baseline, orchestrator-3) vs v2 (ADR-007, orchestrator-4), in version order."""

    versions: list[VersionSummary]


class EvalReportData(BaseModel):
    report_version: int = REPORT_VERSION
    golden_version: str | None = Field(
        default=None, description="Golden dataset version (v1 | v2); null for a scratch dir"
    )
    comparison: GoldenComparison | None = Field(
        default=None, description="v1 -> v2 comparison (on the newest version's report)"
    )
    report_file: str
    created_at: datetime
    evaluator_version: str
    dataset_version: str
    git_sha: str | None = None
    dry_run: bool = Field(description="True when any image or verdict came from a fake client")
    provenance: dict[str, Any] = Field(default_factory=dict[str, Any])
    counts: dict[str, int] = Field(default_factory=dict[str, int])
    per_dimension: dict[str, DimensionMetrics]
    per_split: dict[str, dict[str, DimensionMetrics]] = Field(
        default_factory=dict[str, dict[str, DimensionMetrics]]
    )
    human_agreement: AgreementMetrics = Field(default_factory=AgreementMetrics)
    agreement: dict[str, dict[str, AgreementMetrics]] = Field(
        default_factory=dict[str, dict[str, AgreementMetrics]]
    )
    planted: list[PlantedClassMetrics] = Field(default_factory=list[PlantedClassMetrics])
    known_good: RateMetric = Field(default_factory=RateMetric)
    pipeline: PipelineMetrics = Field(default_factory=PipelineMetrics)
    stability: StabilityMetrics = Field(default_factory=StabilityMetrics)
    hemisphere: RateMetric = Field(default_factory=RateMetric)
    criteria: list[Criterion] = Field(default_factory=list[Criterion])
    targets: dict[str, str] = Field(default_factory=dict[str, str])
    warnings: list[str] = Field(default_factory=list[str])
    # Ablation A1 (evals/suites/ablation_layers.py): {layer: {dimension: P/R/F1}} on this version,
    # and pooled over every reported version on the newest report.
    layers: dict[str, dict[str, DimensionMetrics]] = Field(
        default_factory=dict[str, dict[str, DimensionMetrics]]
    )
    layers_pooled: dict[str, dict[str, DimensionMetrics]] = Field(
        default_factory=dict[str, dict[str, DimensionMetrics]]
    )
    layers_pooled_versions: list[str] = Field(default_factory=list[str])
    # The human's disputed rulings (agent log, Stage 5: "kept as labelled, reported as disputed"):
    # P/R with the disputed (item, dimension) pairs left out, next to the as-labelled numbers.
    disputed: list[str] = Field(default_factory=list[str])
    per_dimension_undisputed: dict[str, DimensionMetrics] = Field(
        default_factory=dict[str, DimensionMetrics]
    )
    items: list[ItemRecord] = Field(default_factory=list[ItemRecord])


# --- API views ------------------------------------------------------------------------------------


class DimensionSummary(BaseModel):
    precision: float | None
    recall: float | None
    f1: float | None
    n: int
    confusion: Confusion


class EvalProvenance(BaseModel):
    """Where the report's numbers come from (docs/ux.md `ReportProvenance`)."""

    evaluator_version: str = ""
    git_sha: str | None = None
    judge_client: str | None = Field(default=None, description="Vision judge client (fake|google)")
    judge_model: str | None = None
    ocr_engine: str | None = None
    ocr_version: str | None = None
    ocr_languages: list[str] = Field(
        default_factory=list[str],
        description="Tesseract language packs installed when the verdict snapshot was recorded",
    )
    image_clients: list[str] = Field(default_factory=list[str])
    image_models: list[str] = Field(default_factory=list[str])
    image_prompt_versions: list[str] = Field(
        default_factory=list[str], description="`ad_generate`/`ad_repair` versions in the outputs"
    )
    prompt_versions: dict[str, str] = Field(
        default_factory=dict[str, str], description="Evaluator prompt name -> version"
    )
    snapshot_file: str | None = Field(default=None, description="Verdict snapshot file name")
    snapshot_entries: int | None = None
    snapshot_recorded_at: str | None = None
    snapshot_evaluator_version: str | None = None
    network_attempts: int | None = None
    assume_pass_labels: bool = False


def provenance_of(data: EvalReportData) -> EvalProvenance:
    """Typed view of `data.provenance`; tolerant of older reports (`ocr: "tesseract 5.5.3"`)."""
    p = data.provenance

    def text(key: str) -> str | None:
        v = p.get(key)
        return str(v) if v not in (None, "") else None

    def strings(key: str) -> list[str]:
        v = p.get(key)
        if isinstance(v, str):
            return [v]
        return [str(x) for x in cast(list[Any], v)] if isinstance(v, list) else []

    engine, version = text("ocr_engine"), text("ocr_version")
    legacy = text("ocr")
    if legacy and not (engine and version):
        head, _, tail = legacy.partition(" ")
        engine = engine or head or None
        version = version or (tail if tail and tail != "?" else None)
    raw_prompts = p.get("prompt_versions")
    prompts: dict[str, str] = (
        {str(k): str(v) for k, v in cast(dict[Any, Any], raw_prompts).items()}
        if isinstance(raw_prompts, dict)
        else {}
    )
    snapshot = text("snapshot")
    entries = p.get("snapshot_entries")
    attempts = p.get("network_attempts")
    return EvalProvenance(
        evaluator_version=data.evaluator_version,
        git_sha=data.git_sha,
        judge_client=text("vision_client"),
        judge_model=text("vision_model"),
        ocr_engine=engine,
        ocr_version=version,
        ocr_languages=strings("ocr_languages"),
        image_clients=strings("image_client"),
        image_models=strings("image_models"),
        image_prompt_versions=strings("image_prompt_versions"),
        prompt_versions=prompts,
        snapshot_file=snapshot.replace("\\", "/").rsplit("/", 1)[-1] if snapshot else None,
        snapshot_entries=int(entries) if isinstance(entries, int) else None,
        snapshot_recorded_at=text("snapshot_recorded_at"),
        snapshot_evaluator_version=text("snapshot_evaluator_version"),
        network_attempts=int(attempts) if isinstance(attempts, int) else None,
        assume_pass_labels=bool(p.get("assume_pass_labels")),
    )


FAKE_DRY_RUN_WARNING = (
    "FAKE DRY RUN: images and verdicts come from the fake image and vision clients; these numbers "
    "test the plumbing, not model quality."
)


def summary_warnings(
    data: EvalReportData,
    *,
    current_evaluator_version: str | None = None,
    current_dataset_version: str | None = None,
) -> list[str]:
    """The report's own warnings plus what a reader must know before trusting the numbers."""
    out: list[str] = []
    if data.dry_run:
        out.append(FAKE_DRY_RUN_WARNING)
    prov = provenance_of(data)
    natural = data.counts.get("nat", 0) + data.counts.get("final", 0)
    human = data.counts.get("human_labelled", 0)
    if natural and human == 0:
        out.append(
            "Missing labels: no natural output has a human label, so human agreement and "
            "natural-item precision/recall are not measured."
        )
    elif human < natural:
        out.append(
            f"Missing labels: {natural - human} of {natural} natural outputs are unlabelled."
        )
    if prov.assume_pass_labels:
        out.append("Unlabelled first-attempt outputs were assumed all-pass (dry run only).")
    if prov.network_attempts:
        out.append(f"{prov.network_attempts} network attempts were blocked during the eval.")
    if (
        prov.snapshot_evaluator_version
        and prov.snapshot_evaluator_version != data.evaluator_version
    ):
        out.append(
            f"Stale snapshot: verdicts were recorded with {prov.snapshot_evaluator_version}, "
            f"the report used {data.evaluator_version}; run `make eval-record`."
        )
    if current_evaluator_version and current_evaluator_version != data.evaluator_version:
        out.append(
            f"Stale report: the evaluator is now {current_evaluator_version} "
            f"(report: {data.evaluator_version}); run `make eval`."
        )
    if current_dataset_version and current_dataset_version != data.dataset_version:
        out.append(
            f"Stale report: the golden files changed since this report (dataset "
            f"{data.dataset_version} -> {current_dataset_version}); run `make eval`."
        )
    out += [w for w in data.warnings if w not in out]
    return out


class EvalSummary(BaseModel):
    """The latest eval report (architecture API contract `EvalSummary`, plus the PRD criteria)."""

    report_id: uuid.UUID
    created_at: datetime
    evaluator_version: str
    dataset_version: str
    git_sha: str | None
    dry_run: bool
    report_file: str
    golden_version: str | None = Field(
        default=None, description="Golden dataset version (v1 | v2) of this report"
    )
    comparison: GoldenComparison | None = Field(
        default=None,
        description="v1 -> v2: human and evaluator pass rates per dimension (incl. composition) "
        "and evaluator P/R per version; on the newest version's report",
    )
    per_dimension: dict[str, DimensionSummary]
    per_split: dict[str, dict[str, DimensionSummary]]
    human_agreement: float | None
    human_agreement_n: int
    human_kappa: float | None
    first_attempt_pass_rate: float | None
    after_repair_pass_rate: float | None
    native_text_rate: float | None
    text_exact_rate: float | None
    cost_per_approved_ad: float | None
    p50_latency_to_approved_ms: float | None
    p95_latency_to_approved_ms: float | None
    judge_stability_flip_rate: float | None
    planted: list[PlantedClassMetrics]
    known_good: RateMetric
    criteria: list[Criterion]
    targets: dict[str, str]
    counts: dict[str, int]
    # Defaults keep these optional in the generated TS types (additive to the contract); the API
    # always fills them.
    pipeline: PipelineMetrics = Field(
        default_factory=PipelineMetrics,
        description="Pipeline quality on the golden runs: mean_repairs, status_counts (runs by "
        "status), pass rates, cost and latency",
    )
    agreement_by_dimension: dict[str, dict[str, AgreementMetrics]] = Field(
        default_factory=dict[str, dict[str, AgreementMetrics]],
        description="Human agreement per image set (nat|final) and dimension, with Cohen's kappa",
    )
    stability: StabilityMetrics = Field(default_factory=StabilityMetrics)
    hemisphere: RateMetric = Field(default_factory=RateMetric)
    provenance: EvalProvenance = Field(default_factory=EvalProvenance)
    warnings: list[str] = Field(
        default_factory=list[str],
        description="FAKE DRY RUN, missing labels, stale snapshot/report and the report's own",
    )


class EvalItem(BaseModel):
    """One labelled item with its evaluator verdict (planted-failure gallery and drill-down)."""

    id: str
    origin: Literal["natural", "planted"]
    set: Literal["nat", "final", "plant"]
    brief_id: str
    product_id: str = Field(description="Golden product key (P1..P5)")
    product_name: str | None = None
    geography_code: str | None = Field(default=None, description="From data/golden/briefs.yaml")
    season: str | None = None
    required_text: str | None = None
    aspect_ratio: str | None = None
    tags: list[str] = Field(default_factory=list[str])
    run_id: uuid.UUID | None = Field(
        default=None, description="The golden run that produced this image (natural items)"
    )
    split: Literal["calibration", "heldout"]
    image_id: uuid.UUID | None
    image_url: str | None
    source_image_url: str | None
    mutation: str | None
    label_source: str
    label: dict[str, bool | None] | None
    verdict: Literal["pass", "fail", "unverified"]
    dimensions: dict[str, bool | None]
    outcomes: dict[str, Outcome | None]
    evidence: list[str]


def _dim_summary(d: DimensionMetrics) -> DimensionSummary:
    return DimensionSummary(
        precision=d.precision, recall=d.recall, f1=d.f1, n=d.n, confusion=d.confusion
    )


def summary_of(
    report_id: uuid.UUID, data: EvalReportData, warnings: list[str] | None = None
) -> EvalSummary:
    p = data.pipeline
    return EvalSummary(
        report_id=report_id,
        created_at=data.created_at,
        evaluator_version=data.evaluator_version,
        dataset_version=data.dataset_version,
        git_sha=data.git_sha,
        dry_run=data.dry_run,
        report_file=data.report_file,
        golden_version=data.golden_version,
        comparison=data.comparison,
        per_dimension={k: _dim_summary(v) for k, v in data.per_dimension.items()},
        per_split={
            split: {k: _dim_summary(v) for k, v in dims.items()}
            for split, dims in data.per_split.items()
        },
        human_agreement=data.human_agreement.agreement,
        human_agreement_n=data.human_agreement.n,
        human_kappa=data.human_agreement.kappa,
        first_attempt_pass_rate=p.first_attempt_pass_rate,
        after_repair_pass_rate=p.after_repair_pass_rate,
        native_text_rate=p.native_text_rate,
        text_exact_rate=p.text_exact_rate,
        cost_per_approved_ad=p.cost_per_approved_ad,
        p50_latency_to_approved_ms=p.p50_latency_ms,
        p95_latency_to_approved_ms=p.p95_latency_ms,
        judge_stability_flip_rate=data.stability.flip_rate,
        planted=data.planted,
        known_good=data.known_good,
        criteria=data.criteria,
        targets=data.targets,
        counts=data.counts,
        pipeline=p,
        agreement_by_dimension=data.agreement,
        stability=data.stability,
        hemisphere=data.hemisphere,
        provenance=provenance_of(data),
        warnings=summary_warnings(data) if warnings is None else warnings,
    )
