"""Golden v1 -> v2 comparison (ADR-007): what the composition work changed.

Per version: the human pass rate per dimension on E-nat and E-final (human-labelled images only;
composition only where the label has it, i.e. rubric v2), the evaluator's pass rate per dimension
(composition included), the evaluator's precision / recall per dimension and the headline pipeline
numbers. v1 is the orchestrator-3 run (the labelled baseline), v2 the orchestrator-4 run.
"""

from collections.abc import Sequence

from backend.domain.adstudio.evalreport import (
    EvalReportData,
    GoldenComparison,
    RateMetric,
    VersionSummary,
)
from evals.suites.evaluator_meta import DIMENSIONS

NAME = "version_compare"
SETS = ("nat", "final")


def _rate(values: list[bool]) -> RateMetric:
    n = len(values)
    ok = sum(values)
    return RateMetric(n=n, ok=ok, rate=ok / n if n else None)


def summarize(data: EvalReportData) -> VersionSummary:
    human: dict[str, dict[str, RateMetric]] = {}
    judged: dict[str, dict[str, RateMetric]] = {}
    for image_set in SETS:
        items = [i for i in data.items if i.set == image_set]
        labelled = [i for i in items if i.label_source == "human" and i.labels]
        human[image_set] = {
            d: _rate([bool(v) for i in labelled if (v := (i.labels or {}).get(d)) is not None])
            for d in DIMENSIONS
        }
        judged[image_set] = {
            d: _rate([i.dimensions[d] is True for i in items if d in i.dimensions])
            for d in DIMENSIONS
        }
    p = data.pipeline
    return VersionSummary(
        version=data.golden_version or "scratch",
        evaluator_version=data.evaluator_version,
        pipeline_versions=sorted(
            {str(v) for v in data.provenance.get("pipeline_versions") or [] if v}
        ),
        counts=dict(data.counts),
        human_pass_rates=human,
        evaluator_pass_rates=judged,
        per_dimension=dict(data.per_dimension),
        human_agreement=data.human_agreement,
        after_repair_pass_rate=p.after_repair_pass_rate,
        first_attempt_pass_rate=p.first_attempt_pass_rate,
        cost_per_approved_ad=p.cost_per_approved_ad,
    )


def compare(reports: Sequence[EvalReportData]) -> GoldenComparison:
    ordered = sorted(reports, key=lambda r: r.golden_version or "")
    return GoldenComparison(versions=[summarize(r) for r in ordered])


def _pct(m: RateMetric | None) -> str:
    if m is None or m.rate is None:
        return "—"
    return f"{m.rate:.0%} ({m.ok}/{m.n})"


def _num(value: float | None, pct: bool = False) -> str:
    if value is None:
        return "—"
    return f"{value:.0%}" if pct else f"{value:.2f}"


def render_markdown(comparison: GoldenComparison) -> list[str]:
    versions = comparison.versions
    names = [v.version for v in versions]
    head = "| Metric | " + " | ".join(names) + " |"
    rule = "|---|" + "---|" * len(names)
    lines = [
        "## Golden " + " → ".join(names) + " (ADR-007)",
        "",
        "v1 is the labelled orchestrator-3 run (fixed 45–60% product scale); v2 is orchestrator-4 "
        "(real-world size and framing, photographed-in-scene prompt, composition repair). Human "
        "rates count human-labelled images only; composition is human-labelled from rubric v2.",
        "",
        "### Human pass rate per dimension",
        "",
        head,
        rule,
    ]
    for image_set in SETS:
        for dim in DIMENSIONS:
            cells = [_pct(v.human_pass_rates.get(image_set, {}).get(dim)) for v in versions]
            lines.append(f"| E-{image_set} {dim} | " + " | ".join(cells) + " |")
    lines += ["", "### Evaluator pass rate per dimension", "", head, rule]
    for image_set in SETS:
        for dim in DIMENSIONS:
            cells = [_pct(v.evaluator_pass_rates.get(image_set, {}).get(dim)) for v in versions]
            lines.append(f"| E-{image_set} {dim} | " + " | ".join(cells) + " |")
    lines += ["", "### Evaluator precision / recall (positive = FAIL)", "", head, rule]
    for dim in DIMENSIONS:
        cells = []
        for v in versions:
            m = v.per_dimension.get(dim)
            cells.append(
                "—"
                if m is None or m.n == 0
                else f"P {_num(m.precision)} / R {_num(m.recall)} (n={m.n})"
            )
        lines.append(f"| {dim} | " + " | ".join(cells) + " |")
    lines += ["", "### Pipeline", "", head, rule]
    lines.append(
        "| Evaluator version | " + " | ".join(v.evaluator_version for v in versions) + " |"
    )
    lines.append(
        "| Pipeline version | "
        + " | ".join(", ".join(v.pipeline_versions) or "—" for v in versions)
        + " |"
    )
    lines.append(
        "| Human agreement (E-nat overall) | "
        + " | ".join(
            f"{_num(v.human_agreement.agreement, True)} (n={v.human_agreement.n})" for v in versions
        )
        + " |"
    )
    lines.append(
        "| First-attempt pass rate | "
        + " | ".join(_num(v.first_attempt_pass_rate, True) for v in versions)
        + " |"
    )
    lines.append(
        "| After-repair pass rate | "
        + " | ".join(_num(v.after_repair_pass_rate, True) for v in versions)
        + " |"
    )
    lines.append(
        "| $ per approved ad | "
        + " | ".join(
            "—" if v.cost_per_approved_ad is None else f"${v.cost_per_approved_ad:.3f}"
            for v in versions
        )
        + " |"
    )
    return [*lines, ""]
