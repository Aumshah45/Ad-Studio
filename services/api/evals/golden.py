"""The golden eval: replay the evaluator over E-nat, E-final and E-plant from the verdict snapshot,
compute the suites and write the report (`make eval`); or record the snapshot (`make eval-record`).
"""

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.settings import Settings
from backend.domain.adstudio.evalreport import (
    CheckRecord,
    Criterion,
    EvalReportData,
    ItemRecord,
    PipelineMetrics,
    RateMetric,
    StabilityMetrics,
)
from backend.domain.adstudio.evaluator.core import EVALUATOR_VERSION
from backend.domain.adstudio.planner import Planner
from backend.golden.dataset import REPO_DIR, GoldenPaths, dataset_version, read_outputs
from backend.golden.harness import (
    CaseResult,
    EvalCase,
    GoldenDataError,
    IncompleteRecordingError,
    StaleSnapshotError,
    build_cases,
    evaluate_cases,
    image_size,
    record_engine,
    replay_engine,
    without_readback,
    write_verdicts,
)
from backend.llm.cache import FileCache
from backend.llm.prompts.ad_inspect import AD_INSPECT
from backend.llm.prompts.composition_judge import COMPOSITION_JUDGE
from backend.llm.prompts.context_judge import CONTEXT_JUDGE
from backend.llm.prompts.product_profile import PRODUCT_PROFILE
from evals.netguard import block_network
from evals.report import REPORTS_DIR, meets, read_baseline, write_golden_report
from evals.suites import ablation_layers, evaluator_meta, pipeline_quality


def repo_relative(path: Path) -> str:
    """Repo-relative when the path lies inside the repository, so committed reports never record a
    local directory; a path outside it (a scratch dir) is kept as given."""
    try:
        return path.resolve().relative_to(REPO_DIR.resolve()).as_posix()
    except ValueError:
        return str(path)


# ai-design §9.6: the planner's effective season on the hemisphere cases.
HEMISPHERE_CASES: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("AU", "December", "summer", ()),
    ("CA", "December", "winter", ()),
    ("NZ", "July", "winter", ()),
    ("BR", "summer", "summer", ()),
    ("ZA", "June", "winter", ()),
    ("AR", "September", "spring", ()),
    ("JP", "April", "spring", ()),
    ("SG", "December", "tropical_wet", ()),
    ("KE", "April", "tropical_wet", ()),
    ("AE", "August", "hot", ()),
    ("AU", "Christmas", "summer", ("Christmas",)),
    ("IN", "Diwali", "tropical_dry", ("Diwali",)),
)
FAKE_MARKERS = ("test:", "fake")


def item_record(result: CaseResult, deterministic: CaseResult | None = None) -> ItemRecord:
    """`deterministic` is the same case evaluated with the read-back off (ablation A1)."""
    case, ev = result.case, result.evaluation
    checks = [
        CheckRecord(
            dimension=c.dimension,
            name=c.name,
            method=c.method,
            passed=c.passed,
            value=c.value,
            threshold=c.threshold,
            evidence=c.evidence[:300],
            note=bool(c.data and c.data.get("note")),
        )
        for c in ev.checks
    ]
    evidence = [c.evidence for c in checks if c.passed is not True and c.evidence][:3]
    planted = case.planted
    return ItemRecord(
        id=case.id,
        set=case.set,
        brief_id=case.brief_id,
        product_id=case.product_id,
        split=case.split,
        image_sha=case.image_sha,
        source_id=planted.source_id if planted else None,
        source_sha=planted.source_sha if planted else None,
        mutation=planted.mutation if planted else None,
        label_source=case.label_source,
        labels=case.labels,
        verdict=ev.verdict,
        dimensions={d: r.passed for d, r in ev.dimensions.items()},
        evidence=evidence,
        checks=checks,
        layers=ablation_layers.layer_verdicts(
            ev, deterministic.evaluation if deterministic else None
        ),
    )


async def hemisphere_accuracy() -> RateMetric:
    planner = Planner(None)
    ok = 0
    for code, season, expected, holidays in HEMISPHERE_CASES:
        resolution = await planner.resolve(code, None, season)
        if (
            resolution.season.effective_season == expected
            and tuple(resolution.season.holidays) == holidays
        ):
            ok += 1
    n = len(HEMISPHERE_CASES)
    return RateMetric(n=n, ok=ok, rate=ok / n)


def stability(before: Sequence[ItemRecord], after: Sequence[ItemRecord]) -> StabilityMetrics:
    """Flipped vision-check verdicts between two judge runs of the same items."""
    first = {i.id: i for i in before}
    items = checks = flips = 0
    for item in after:
        other = first.get(item.id)
        if other is None:
            continue
        items += 1
        mine = {c.name: c.passed for c in item.checks if c.method == "vlm"}
        theirs = {c.name: c.passed for c in other.checks if c.method == "vlm"}
        for name in mine.keys() & theirs.keys():
            checks += 1
            flips += int(mine[name] != theirs[name])
    return StabilityMetrics(
        available=items > 0,
        items=items,
        checks=checks,
        flips=flips,
        flip_rate=flips / checks if checks else None,
    )


def git_sha() -> str | None:
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv
            ["git", "rev-parse", "--short=12", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def criteria(
    meta: evaluator_meta.MetaResult,
    pipeline: PipelineMetrics,
    hemisphere: RateMetric,
    network_attempts: int,
    baseline: dict[str, float],
) -> list[Criterion]:
    """Every PRD success criterion (docs/prd.md) as target / baseline / now / met."""
    per = meta.per_dimension
    ha = meta.human_agreement
    rows: list[tuple[str, str, str, tuple[str, float] | None, float | None, str]] = []
    for dim in evaluator_meta.TARGET_DIMENSIONS:
        rows.append(
            (
                f"recall.{dim}",
                "Evaluator catches failures",
                f"Recall, {dim} (E-nat ∪ E-plant)",
                (">=", 0.90),
                per[dim].recall,
                f"n={per[dim].n}",
            )
        )
    for dim in evaluator_meta.TARGET_DIMENSIONS:
        rows.append(
            (
                f"precision.{dim}",
                "Evaluator doesn't cry wolf",
                f"Precision, {dim} (E-nat ∪ E-plant)",
                (">=", 0.85),
                per[dim].precision,
                f"n={per[dim].n}",
            )
        )
    rows += [
        (
            "human_agreement",
            "Evaluator agrees with humans",
            "Overall pass/fail agreement on E-nat",
            (">=", 0.85),
            ha.agreement,
            f"{ha.n} human-labelled" if ha.n else "no human labels yet",
        ),
        (
            "first_attempt_pass_rate",
            "Quality gate works",
            "First-attempt pass rate",
            None,
            pipeline.first_attempt_pass_rate,
            "reported",
        ),
        (
            "after_repair_pass_rate",
            "Quality gate works",
            "After-repair pass rate",
            (">=", 0.90),
            pipeline.after_repair_pass_rate,
            f"{pipeline.shipped}/{pipeline.briefs}",
        ),
        (
            "text_exact_rate",
            "Text is guaranteed",
            "Shipped ads with exact text",
            (">=", 1.0),
            pipeline.text_exact_rate,
            f"native-text rate {pipeline.native_text_rate:.0%}, B20 excluded"
            if pipeline.native_text_rate is not None
            else "",
        ),
        (
            "resolution_ok_rate",
            "Resolution constraint",
            "Outputs with long edge ≤ 1024 px",
            (">=", 1.0),
            pipeline.resolution_ok_rate,
            f"{pipeline.images_checked} images",
        ),
        (
            "hemisphere_accuracy",
            "Context refinement is correct",
            "Planner effective season, hemisphere cases",
            (">=", 1.0),
            hemisphere.rate,
            f"{hemisphere.ok}/{hemisphere.n}",
        ),
        (
            "network_calls",
            "Evaluator tests are reproducible",
            "Network connections attempted during `make eval`",
            ("<=", 0.0),
            float(network_attempts),
            "cached verdicts, sockets blocked",
        ),
        (
            "cost_per_approved_ad",
            "Cost",
            "$ per approved ad (ledger)",
            ("<=", 0.25),
            pipeline.cost_per_approved_ad,
            "",
        ),
        (
            "p50_latency_ms",
            "Latency",
            "p50 time to an approved ad",
            ("<=", 60_000.0),
            pipeline.p50_latency_ms,
            "",
        ),
    ]
    out: list[Criterion] = []
    for cid, name, metric, target, now, note in rows:
        base = baseline.get(cid, now) if baseline else now
        out.append(
            Criterion(
                id=cid,
                criterion=name,
                metric=metric,
                target=_target_text(cid, target),
                baseline=base,
                now=now,
                met=meets(now, target) if target else None,
                note=note,
            )
        )
    return out


def _target_text(cid: str, target: tuple[str, float] | None) -> str:
    if target is None:
        return "report"
    op, value = target
    if cid == "cost_per_approved_ad":
        return f"{op} ${value:.2f}"
    if cid.endswith("latency_ms"):
        return f"{op} {value / 1000:.0f} s"
    if cid == "network_calls":
        return "0"
    return f"{op} {value:.0%}"


@dataclass
class EvalOutcome:
    report: EvalReportData
    path: Path


async def run_eval(
    paths: GoldenPaths,
    settings: Settings,
    *,
    reports_dir: Path = REPORTS_DIR,
    assume_pass: bool = False,
    reset_baseline: bool = False,
    now: datetime | None = None,
) -> EvalOutcome:
    """`make eval`: replay only, sockets blocked, then the report files."""
    if not read_outputs(paths):
        raise GoldenDataError(
            f"no golden outputs in {paths.outputs} yet: run `make golden-run golden-export` "
            "(or `make golden-dryrun` for the fake chain); no report written"
        )
    warnings: list[str] = []
    with block_network() as guard:
        batch = await build_cases(paths, assume_pass=assume_pass)
        warnings += batch.warnings
        engine = replay_engine(paths.verdicts, settings)
        results = await evaluate_cases(engine, batch.cases, concurrency=4)
        # Ablation A1: the deterministic text layer (read-back off), from the same snapshot.
        det = {
            r.case.id: r
            for r in await evaluate_cases(without_readback(engine), batch.cases, concurrency=4)
        }
        records = [item_record(r, det.get(r.case.id)) for r in results]
        stab = StabilityMetrics()
        if paths.verdicts_rerun.exists():
            rerun = replay_engine(paths.verdicts_rerun, settings)
            ids = set(FileCache(paths.verdicts_rerun).meta.get("items") or [])
            subset = [c for c in batch.cases if c.id in ids]
            try:
                again = [item_record(r) for r in await evaluate_cases(rerun, subset)]
                stab = stability(records, again)
            except StaleSnapshotError as exc:
                warnings.append(f"judge stability skipped: {exc}")
        hemisphere = await hemisphere_accuracy()
    meta = evaluator_meta.compute(records, paths.version)
    outputs = read_outputs(paths)
    finals = {i.id: i for i in meta.items if i.set == "final"}
    sizes = [image_size(c.image) for c in batch.cases if c.set in ("nat", "final")]
    pipeline = pipeline_quality.compute(outputs, finals, sizes)
    if batch.pure:
        warnings.append(
            f"{batch.reproduced}/{batch.pure} pure planted items regenerated byte-identical"
        )
    snapshot_version = engine.meta.get("evaluator_version")
    if snapshot_version and snapshot_version != EVALUATOR_VERSION:
        warnings.append(
            f"stale snapshot: verdicts recorded with {snapshot_version}, evaluator is "
            f"{EVALUATOR_VERSION}; run `make eval-record`"
        )
    reports_dir.mkdir(parents=True, exist_ok=True)
    version = paths.version
    baseline = read_baseline(reports_dir, version)
    crit = criteria(meta, pipeline, hemisphere, len(guard.attempts), baseline)
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    models = sorted({o.model or "" for o in outputs} | {o.requested_model or "" for o in outputs})
    vision = engine.meta.get("vision") or {}
    ocr = engine.meta.get("ocr") or {}
    ocr2 = engine.meta.get("ocr2") or {}
    dry = any(any(m in str(x) for m in FAKE_MARKERS) for x in [*models, vision.get("model", "")])
    dry = dry or any(o.image_client == "fake" for o in outputs)
    counts = {
        "nat": sum(1 for c in batch.cases if c.set == "nat"),
        "final": sum(1 for c in batch.cases if c.set == "final"),
        "plant": sum(1 for c in batch.cases if c.set == "plant"),
        "human_labelled": sum(1 for c in batch.cases if c.label_source == "human"),
    }
    data = EvalReportData(
        golden_version=version,
        report_file=f"{stamp}-{version}.md" if version else f"{stamp}.md",
        created_at=now or datetime.now(UTC),
        evaluator_version=EVALUATOR_VERSION,
        dataset_version=dataset_version(paths),
        git_sha=git_sha(),
        dry_run=dry,
        provenance={
            "image_client": sorted({o.image_client for o in outputs}),
            "image_models": [m for m in models if m],
            "vision_client": vision.get("client"),
            "vision_model": vision.get("model"),
            "ocr": f"tesseract {ocr.get('version', '?')}"
            + (f" + apple vision {ocr2.get('version', '?')}" if ocr2 else ""),
            "ocr_engine": "tesseract+apple_vision" if ocr2 else "tesseract",
            "ocr_version": ocr.get("version"),
            "ocr2_version": ocr2.get("version") if ocr2 else None,
            "ocr_languages": sorted(str(x) for x in ocr.get("langs") or []),
            "image_prompt_versions": sorted(
                {o.prompt_version for o in outputs if o.prompt_version}
            ),
            "pipeline_versions": sorted({o.pipeline_version for o in outputs}),
            "prompt_versions": {
                p.name: p.version
                for p in (AD_INSPECT, CONTEXT_JUDGE, COMPOSITION_JUDGE, PRODUCT_PROFILE)
            },
            "snapshot_recorded_at": engine.meta.get("recorded_at"),
            "snapshot_evaluator_version": snapshot_version,
            "snapshot": repo_relative(paths.verdicts),
            "snapshot_entries": len(FileCache(paths.verdicts).entries),
            "network_attempts": len(guard.attempts),
            "assume_pass_labels": assume_pass,
        },
        counts=counts,
        per_dimension=meta.per_dimension,
        per_split=meta.per_split,
        human_agreement=meta.human_agreement,
        agreement=meta.agreement,
        planted=meta.planted,
        known_good=meta.known_good,
        pipeline=pipeline,
        stability=stab,
        hemisphere=hemisphere,
        criteria=crit,
        targets={c.id: c.target for c in crit},
        warnings=warnings,
        layers=ablation_layers.compute(meta.items),
        disputed=meta.disputed,
        per_dimension_undisputed=meta.per_dimension_undisputed,
        items=meta.items,
    )
    path = write_golden_report(data, reports_dir=reports_dir, reset_baseline=reset_baseline)
    return EvalOutcome(data, path)


def attach_comparison(outcomes: list[EvalOutcome], *, reports_dir: Path = REPORTS_DIR) -> None:
    """ADR-007: the v1 -> v2 comparison goes on the newest version's report (rewritten)."""
    from evals.suites.version_compare import compare

    if len(outcomes) < 2:
        return
    comparison = compare([o.report for o in outcomes])
    newest = max(outcomes, key=lambda o: o.report.golden_version or "")
    newest.report.comparison = comparison
    # Ablation A1 pooled over every version (v1 + v2 natural and planted items).
    newest.report.layers_pooled = ablation_layers.compute(
        [i for o in outcomes for i in o.report.items]
    )
    newest.report.layers_pooled_versions = [o.report.golden_version or "?" for o in outcomes]
    write_golden_report(newest.report, reports_dir=reports_dir)


@dataclass
class RecordOutcome:
    entries: int
    hits_snapshot: int
    hits_db: int
    live: int
    rerun_entries: int = 0


async def record(
    paths: GoldenPaths,
    settings: Settings,
    *,
    sessionmaker: async_sessionmaker[AsyncSession] | None,
    rerun: int = 0,
) -> RecordOutcome:
    """`make eval-record`: evaluate every item, calling OCR / the judge only for answers that
    neither the snapshot nor the DB cache holds; the snapshot is rewritten with exactly the keys
    the evaluator needs. `rerun` > 0 also judges that many E-nat items again with the caches
    bypassed (judge stability)."""
    batch = await build_cases(paths)
    engine = record_engine(
        settings,
        base=paths.verdicts,
        sessionmaker=sessionmaker,
        allow_live=True,
    )
    await evaluate_cases(engine, batch.cases, concurrency=2)
    await evaluate_cases(without_readback(engine), batch.cases, concurrency=2)  # ablation A1
    rec = engine.recorder
    assert rec is not None
    # A failed live call leaves the old entry for that key out; keep the old snapshot's entries
    # then, so a partial re-record never loses answers it already had.
    entries = write_verdicts(paths.verdicts, engine, keep_existing=bool(rec.failed))
    if rec.failed:
        raise IncompleteRecordingError(paths.verdicts, rec.failed, kept=entries)
    await verify_snapshot(paths.verdicts, settings, batch.cases)
    outcome = RecordOutcome(entries, rec.hits_snapshot, rec.hits_db, rec.live)
    if rerun > 0:
        chosen: list[EvalCase] = sorted(
            (c for c in batch.cases if c.set == "nat"), key=lambda c: c.id
        )[:rerun]
        # Caches bypassed (the point is a second opinion), but the calls still go in the ledger.
        again = record_engine(
            settings, base=None, sessionmaker=sessionmaker, allow_live=True, use_db_cache=False
        )
        await evaluate_cases(again, chosen, concurrency=2)
        assert again.recorder is not None
        if again.recorder.failed:
            raise IncompleteRecordingError(
                paths.verdicts_rerun, again.recorder.failed, kept=len(again.recorder.touched)
            )
        outcome.rerun_entries = write_verdicts(
            paths.verdicts_rerun,
            again,
            keep_existing=False,
            extra_meta={"items": [c.id for c in chosen]},
        )
        await verify_snapshot(paths.verdicts_rerun, settings, chosen)
    return outcome


async def verify_snapshot(snapshot: Path, settings: Settings, cases: Sequence[EvalCase]) -> None:
    """Replay what was just recorded exactly as `make eval` will (offline, sockets blocked): any
    key the replay derives that the recording did not store raises `StaleSnapshotError` here,
    at record time, instead of on the next `make eval`."""
    with block_network():
        engine = replay_engine(snapshot, settings)
        await evaluate_cases(engine, cases, concurrency=4)
        await evaluate_cases(without_readback(engine), cases, concurrency=4)
