"""Eval report writer: Metric | Target | Baseline | Now with pass/fail per target."""

import json
import operator
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

Metric = float | int | str
Target = tuple[str, float]
OPS: dict[str, Callable[[float, float], bool]] = {
    ">=": operator.ge,
    ">": operator.gt,
    "<=": operator.le,
    "<": operator.lt,
    "==": operator.eq,
}
REPORTS_DIR = Path(__file__).parent / "reports"


def is_numeric(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def meets(value: Metric | None, target: Target) -> bool | None:
    """True/False against the target; None when the metric is missing or not numeric."""
    if value is None or not is_numeric(value):
        return None
    op, threshold = target
    return OPS[op](float(value), threshold)


def build_rows(
    metrics: dict[str, Metric], targets: dict[str, Target], baseline: dict[str, Metric]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name in sorted(set(metrics) | set(targets)):
        target = targets.get(name)
        now = metrics.get(name)
        rows.append(
            {
                "metric": name,
                "target": f"{target[0]} {target[1]}" if target else "",
                "baseline": baseline.get(name),
                "now": now,
                "pass": meets(now, target) if target else None,
            }
        )
    return rows


def _cell(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "pass" if value else "FAIL"
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


def write_report(
    metrics: dict[str, Metric],
    targets: dict[str, Target],
    *,
    reports_dir: Path = REPORTS_DIR,
    reset_baseline: bool = False,
    now: datetime | None = None,
) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    baseline_path = reports_dir / "baseline.json"
    has_numeric = any(is_numeric(v) for v in metrics.values())
    if has_numeric and (reset_baseline or not baseline_path.exists()):
        baseline_path.write_text(json.dumps(metrics, indent=2, sort_keys=True))
    baseline: dict[str, Metric] = (
        json.loads(baseline_path.read_text()) if baseline_path.exists() else {}
    )
    rows = build_rows(metrics, targets, baseline)
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        f"# Eval report {stamp}",
        "",
        "| Metric | Target | Baseline | Now | Pass |",
        "|---|---|---|---|---|",
        *(
            f"| {r['metric']} | {r['target'] or '—'} | {_cell(r['baseline'])} | {_cell(r['now'])}"
            f" | {_cell(r['pass'])} |"
            for r in rows
        ),
    ]
    md_path = reports_dir / f"{stamp}.md"
    md_path.write_text("\n".join(lines) + "\n")
    (reports_dir / f"{stamp}.json").write_text(
        json.dumps({"metrics": metrics, "rows": rows}, indent=2, default=str)
    )
    return md_path


# --- golden eval report (slice 17) ----------------------------------------------------------------


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.1%}"


def _num(value: float | None, digits: int = 3) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def _crit_value(cid: str, value: float | None) -> str:
    if value is None:
        return "—"
    if cid == "cost_per_approved_ad":
        return f"${value:.3f}"
    if cid.endswith("latency_ms"):
        return f"{value / 1000:.1f} s"
    if cid == "network_calls":
        return str(int(value))
    return _pct(value)


def _met(value: bool | None) -> str:
    return "—" if value is None else ("met" if value else "**not met**")


def _bool(value: bool | None) -> str:
    return "—" if value is None else ("pass" if value else "FAIL")


def _layers_table(layers: dict[str, dict[str, Any]]) -> list[str]:
    lines = [
        "| Dimension | Layer | n | TP | FP | FN | TN | Precision | Recall | F1 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    dims = list(next(iter(layers.values())).keys()) if layers else []
    for dim in dims:
        for layer, per_dim in layers.items():
            m = per_dim[dim]
            if m.n == 0:
                lines.append(f"| {dim} | {layer} | 0 | — | — | — | — | n/a | n/a | n/a |")
                continue
            c = m.confusion
            lines.append(
                f"| {dim} | {layer} | {m.n} | {c.tp} | {c.fp} | {c.fn} | {c.tn} "
                f"| {_num(m.precision)} | {_num(m.recall)} | {_num(m.f1)} |"
            )
    return lines


def _layers_section(d: Any) -> list[str]:
    """Ablation A1 (evals/suites/ablation_layers.py)."""
    layers = getattr(d, "layers", None) or {}
    if not layers:
        return []
    lines = [
        "",
        "## Evaluator layers (ablation A1)",
        "",
        "Labelled E-nat ∪ E-plant items scored by each layer alone (positive class = FAIL, "
        "unverified = FAIL). *deterministic*: OCR ensemble without the read-back, CIEDE2000 "
        "colour, box-area sanity, technical; *vlm*: read-back, product count and checklist, "
        "context rubric, composition judge; *combined*: the shipped evaluator. n/a: the layer has "
        "no check for that dimension. The colour and area checks measure inside the VLM's "
        "product box.",
        "",
        f"**{d.golden_version or 'this set'}**",
        "",
        *_layers_table(layers),
    ]
    pooled = getattr(d, "layers_pooled", None) or {}
    if pooled:
        lines += [
            "",
            f"**Pooled: {' + '.join(getattr(d, 'layers_pooled_versions', []) or [])}**",
            "",
            *_layers_table(pooled),
        ]
    return lines


def render_golden_markdown(data: Any) -> str:
    """Markdown for an `EvalReportData` (typed as Any to keep this module import-light)."""
    d = data
    lines: list[str] = [f"# Eval report {d.report_file.removesuffix('.md')}", ""]
    if d.golden_version:
        lines += [f"Golden dataset **{d.golden_version}** (`data/golden/{d.golden_version}/`).", ""]
    if d.dry_run:
        lines += [
            "> **FAKE DRY RUN.** Images and/or verdicts come from the fake image and vision "
            "clients "
            "(no API key). These numbers prove the tooling end to end; they say nothing about the "
            "real pipeline or evaluator.",
            "",
        ]
    prov = d.provenance
    lines += [
        f"- Evaluator `{d.evaluator_version}` · dataset `{d.dataset_version}` · git "
        f"`{d.git_sha or 'n/a'}` · created {d.created_at.isoformat(timespec='seconds')}",
        f"- Image client `{prov.get('image_client')}`, models {prov.get('image_models')}; vision "
        f"judge `{prov.get('vision_model')}` ({prov.get('vision_client')}); OCR "
        f"`{prov.get('ocr')}`",
        f"- Items: {d.counts.get('nat', 0)} E-nat, {d.counts.get('final', 0)} E-final, "
        f"{d.counts.get('plant', 0)} E-plant; human-labelled: {d.counts.get('human_labelled', 0)}; "
        f"verdict snapshot entries: {prov.get('snapshot_entries')}",
        f"- Replay: `cache/verdicts.json` only (FileCache, misses fail), sockets blocked; "
        f"network connections attempted: {prov.get('network_attempts', 0)}",
        "",
        "## PRD success criteria",
        "",
        "| Criterion | Metric | Target | Baseline | Now | Status |",
        "|---|---|---|---|---|---|",
    ]
    for c in d.criteria:
        note = f" ({c.note})" if c.note else ""
        lines.append(
            f"| {c.criterion} | {c.metric}{note} | {c.target} | {_crit_value(c.id, c.baseline)} "
            f"| {_crit_value(c.id, c.now)} | {_met(c.met)} |"
        )
    lines += [
        "",
        "## Evaluator meta-eval (positive class = FAIL; E-nat ∪ E-plant, labelled items)",
        "",
        "| Dimension | n | TP | FP | FN | TN | Precision | Recall | F1 | Not assessed |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for dim, m in d.per_dimension.items():
        c = m.confusion
        lines.append(
            f"| {dim} | {m.n} | {c.tp} | {c.fp} | {c.fn} | {c.tn} | {_num(m.precision)} "
            f"| {_num(m.recall)} | {_num(m.f1)} | {m.not_assessed} |"
        )
    disputed = list(getattr(d, "disputed", []) or [])
    if disputed:
        lines += [
            "",
            "### Disputed rulings",
            "",
            "The human kept these rulings as labelled and asked for them to be reported as "
            "disputed (TECHNICAL_REPORT.md §8). Labels are unchanged: the table above and the "
            "criteria use them as labelled. Here the disputed (item, dimension) pairs are left "
            "out: " + ", ".join(f"`{x}`" for x in disputed) + ".",
            "",
            "| Dimension | n | TP | FP | FN | TN | Precision | Recall | F1 |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for dim, m in d.per_dimension_undisputed.items():
            c = m.confusion
            lines.append(
                f"| {dim} | {m.n} | {c.tp} | {c.fp} | {c.fn} | {c.tn} | {_num(m.precision)} "
                f"| {_num(m.recall)} | {_num(m.f1)} |"
            )
    lines += ["", "### Confusion matrices", ""]
    for dim, m in d.per_dimension.items():
        c = m.confusion
        lines += [
            f"**{dim}**",
            "",
            "| | evaluator FAIL | evaluator PASS |",
            "|---|---|---|",
            f"| label FAIL | {c.tp} (TP) | {c.fn} (FN) |",
            f"| label PASS | {c.fp} (FP) | {c.tn} (TN) |",
            "",
        ]
    lines += [
        "### Calibration (P1–P3) vs held-out (P4–P5)",
        "",
        "Thresholds are tuned on the calibration split only (ai-design §9.4).",
        "",
        "| Split | Dimension | n | Precision | Recall | F1 |",
        "|---|---|---|---|---|---|",
    ]
    for split, dims in d.per_split.items():
        for dim, m in dims.items():
            lines.append(
                f"| {split} | {dim} | {m.n} | {_num(m.precision)} | {_num(m.recall)} "
                f"| {_num(m.f1)} |"
            )
    lines += [
        "",
        "### Planted failures",
        "",
        "| Class | Expected dimension | n | Caught | Rate | Missed |",
        "|---|---|---|---|---|---|",
    ]
    for p in d.planted:
        lines.append(
            f"| {p.mutation} | {', '.join(p.expected)} | {p.n} | {p.caught} | {_pct(p.rate)} "
            f"| {', '.join(p.missed) or '—'} |"
        )
    kg = d.known_good
    lines += [
        "",
        f"Known-good controls passing every dimension: {kg.ok}/{kg.n} ({_pct(kg.rate)}).",
        "",
        "## Human agreement",
        "",
        f"Overall pass/fail on E-nat: {d.human_agreement.agree}/{d.human_agreement.n} "
        f"({_pct(d.human_agreement.agreement)}), Cohen's κ {_num(d.human_agreement.kappa)}.",
        "",
        "| Set | Dimension | n | Agreement | κ |",
        "|---|---|---|---|---|",
    ]
    for image_set, dims in d.agreement.items():
        for dim, a in dims.items():
            lines.append(f"| {image_set} | {dim} | {a.n} | {_pct(a.agreement)} | {_num(a.kappa)} |")
    lines += _layers_section(d)
    p = d.pipeline
    statuses = ", ".join(f"{k} {v}" for k, v in sorted(p.status_counts.items()))
    lines += [
        "",
        "## Pipeline quality (golden briefs)",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Briefs | {p.briefs} ({statuses}) |",
        f"| First-attempt pass rate | {_pct(p.first_attempt_pass_rate)} |",
        f"| After-repair pass rate | {_pct(p.after_repair_pass_rate)} ({p.shipped} shipped) |",
        f"| Shipped exact text | {_pct(p.text_exact_rate)} (OCR-exact {p.text_exact_ocr}, "
        f"by construction {p.text_exact_construction}) |",
        f"| Native-text rate (B20 excluded) | {_pct(p.native_text_rate)} of {p.native_text_n} |",
        f"| Mean repairs per brief | {_num(p.mean_repairs, 2)} |",
        f"| Total spend | ${p.total_cost_usd:.4f} |",
        f"| $ per approved ad | {_crit_value('cost_per_approved_ad', p.cost_per_approved_ad)} |",
        f"| Latency p50 / p95 | {_crit_value('p50_latency_ms', p.p50_latency_ms)} / "
        f"{_crit_value('p95_latency_ms', p.p95_latency_ms)} |",
        f"| Resolution ≤ 1024 px | {_pct(p.resolution_ok_rate)} of {p.images_checked} images |",
        f"| Minor label detail degraded (note, not a fail) | "
        f"{_pct(p.minor_label_detail_rate)} of {p.minor_label_detail_n} final images |",
        f"| Product hue drift ≥ 10° (note, not a fail) | {_pct(p.hue_drift_rate)} of "
        f"{p.hue_drift_n} final images with a chromatic colour"
        + (
            " (" + ", ".join(f"{k} {_pct(v)}" for k, v in p.hue_drift_by_product.items()) + ")"
            if p.hue_drift_by_product
            else ""
        )
        + " |",
        "",
        "## Judge stability",
        "",
    ]
    s = d.stability
    if s.available:
        lines.append(
            f"{s.flips} flipped verdicts over {s.checks} vision checks on {s.items} items judged "
            f"twice with the cache bypassed: flip rate {_pct(s.flip_rate)} (target ≤ 10%)."
        )
    else:
        lines.append("No double run recorded (`make eval-record RERUN=10` records one).")
    if d.comparison is not None and len(d.comparison.versions) > 1:
        from evals.suites.version_compare import render_markdown

        lines += ["", *render_markdown(d.comparison)]
    if d.warnings:
        lines += ["", "## Warnings", "", *(f"- {w}" for w in d.warnings)]
    lines += [
        "",
        "## Items",
        "",
        "| Item | Set | Brief | Mutation | Labels (tech/text/prod/ctx/comp) | Verdict | "
        "Evaluator (tech/text/prod/ctx/comp) | Top evidence |",
        "|---|---|---|---|---|---|---|---|",
    ]
    order = ("technical", "text", "product", "context", "composition")
    for it in d.items:
        labels = (
            "/".join(_bool((it.labels or {}).get(k)) for k in order) if it.labels else "unlabelled"
        )
        dims = "/".join(_bool(it.dimensions.get(k)) if k in it.dimensions else "·" for k in order)
        evidence = (it.evidence[0] if it.evidence else "").replace("|", "\\|")[:140]
        lines.append(
            f"| {it.id} | {it.set} | {it.brief_id} | {it.mutation or '—'} | {labels} "
            f"({it.label_source}) | {it.verdict} | {dims} | {evidence} |"
        )
    return "\n".join(lines) + "\n"


def write_golden_report(
    data: Any, *, reports_dir: Path = REPORTS_DIR, reset_baseline: bool = False
) -> Path:
    """`<stamp>[-<version>].md` + `.json` + `latest.json`; `baseline[-<version>].json` (the
    criteria's values) on the first report of that golden version or with `reset_baseline`."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    md_path = reports_dir / data.report_file
    md_path.write_text(render_golden_markdown(data), encoding="utf-8")
    payload = data.model_dump_json(indent=1)
    md_path.with_suffix(".json").write_text(payload, encoding="utf-8")
    (reports_dir / "latest.json").write_text(payload, encoding="utf-8")
    baseline_path = baseline_file(reports_dir, data.golden_version)
    if reset_baseline or not baseline_path.exists():
        baseline = {c.id: c.now for c in data.criteria if c.now is not None}
        if baseline:
            baseline_path.write_text(json.dumps(baseline, indent=2, sort_keys=True))
    return md_path


def baseline_file(reports_dir: Path, version: str | None) -> Path:
    return reports_dir / (f"baseline-{version}.json" if version else "baseline.json")


def read_baseline(reports_dir: Path = REPORTS_DIR, version: str | None = None) -> dict[str, float]:
    path = baseline_file(reports_dir, version)
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    return {k: float(v) for k, v in raw.items() if is_numeric(v)}
