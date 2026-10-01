"""`golden repair-probe`: the live repair path on the real failing drafts of a golden run.

In a golden run a brief draws N first-round candidates and the orchestrator ships any passing one,
so a failing draft whose sibling passed never reaches a repair (v3: 6 failing drafts, 1 repair
route taken). The probe takes every first-round candidate of the latest golden run that did not
pass and runs the production repair loop (`Orchestrator.loop`: routing table, repair models,
per-run budget, stall guard, overlay) with that draft as the only pool entry:

- A probe run is a new `origin=golden` run with `batch_label=repair-probe:<pipeline version>` on a
  copy of the brief without a `golden_key`, so export (latest run per golden brief) and the golden
  batch spend never see it. `config.probe` names the source run and candidate.
- The spec is the source run's stored spec (same plan, no new planner call); product facts are the
  product's. The draft is stored as the probe's attempt-0 candidate (same image row) and
  re-evaluated with the configured (frozen) evaluator; judge and OCR reads are cached, so the
  verdict matches the source run's.
- The per-run budget is charged for the N first-round image calls before the loop, so repairs get
  the headroom they would have had in the real run, not more.
"""

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from backend.core.errors import AppError
from backend.db import models_adstudio as m
from backend.db import repositories as repo
from backend.domain.adstudio.events import RunStatusEvent, event_payload
from backend.domain.adstudio.pipeline import (
    PIPELINE_VERSION,
    Orchestrator,
    RunLog,
    Scored,
    _finish,  # pyright: ignore[reportPrivateUsage]
    _load,  # pyright: ignore[reportPrivateUsage]
)
from backend.domain.adstudio.planner import parse_facts
from backend.domain.adstudio.spec import CreativeSpec
from backend.golden.context import GoldenContext
from backend.golden.dataset import GoldenPaths, golden_key, load_golden
from backend.golden.runner import DONE_STATUSES
from backend.llm.ledger import bind_run

PROBE_PREFIX = "repair-probe:"


def probe_label() -> str:
    return f"{PROBE_PREFIX}{PIPELINE_VERSION}"


@dataclass
class ProbeStep:
    kind: str
    image_sha: str
    model: str | None
    verdict: str
    failed: list[str]
    unverified: list[str]


@dataclass
class ProbeOutcome:
    brief_id: str
    source_run_id: uuid.UUID
    source_candidate_id: uuid.UUID
    slot: int
    draft_failed: list[str]
    draft_unverified: list[str]
    probe_run_id: uuid.UUID | None = None
    status: str = "pending"
    reason: str | None = None
    outcome: str | None = None
    repairs: int = 0
    rows: list[str] = field(default_factory=list[str])
    steps: list[ProbeStep] = field(default_factory=list[ProbeStep])
    cost_usd: float = 0.0
    latency_ms: int | None = None
    note: str = ""


def _dims(evaluation: m.Evaluation) -> tuple[list[str], list[str]]:
    dims = {
        "technical": evaluation.technical_pass,
        "text": evaluation.text_pass,
        "product": evaluation.product_pass,
        "context": evaluation.context_pass,
        "composition": evaluation.composition_pass,
    }
    failed = [d for d, v in dims.items() if v is False]
    unverified = [d for d, v in dims.items() if v is None]
    return failed, unverified


async def failing_drafts(
    ctx: GoldenContext, paths: GoldenPaths, only: set[str] | None = None
) -> list[tuple[str, m.Run, m.Candidate, m.Evaluation]]:
    """First-round candidates of each brief's latest finished golden run that did not pass."""
    golden = load_golden(paths)
    client = ctx.deps.image_client.name
    out: list[tuple[str, m.Run, m.Candidate, m.Evaluation]] = []
    async with ctx.deps.sessionmaker() as session:
        for brief in golden.briefs:
            if only is not None and brief.id not in only:
                continue
            key = golden_key(brief.id, PIPELINE_VERSION, client)
            row = await session.scalar(select(m.Brief).where(m.Brief.golden_key == key))
            if row is None:
                continue
            run = await session.scalar(
                select(m.Run)
                .where(m.Run.brief_id == row.id, m.Run.status.in_(DONE_STATUSES))
                .order_by(m.Run.created_at.desc(), m.Run.id.desc())
                .limit(1)
            )
            if run is None:
                continue
            pairs = await session.execute(
                select(m.Candidate, m.Evaluation)
                .join(m.Evaluation, m.Evaluation.candidate_id == m.Candidate.id)
                .where(
                    m.Candidate.run_id == run.id,
                    m.Candidate.kind == "initial",
                    m.Candidate.attempt == 0,
                )
                .order_by(m.Candidate.slot)
            )
            for candidate, evaluation in pairs.tuples():
                if evaluation.verdict != "pass":
                    out.append((brief.id, run, candidate, evaluation))
    return out


async def _create_probe_run(
    ctx: GoldenContext, source: m.Run, candidate: m.Candidate, brief_id: str
) -> m.Run:
    async with ctx.deps.sessionmaker() as session:
        brief = await session.get(m.Brief, source.brief_id)
        if brief is None:
            raise AppError(404, "not-found", "source run's brief is missing")
        copy = await repo.create_brief(
            session,
            source.tenant_id,
            product_id=brief.product_id,
            geography_code=brief.geography_code,
            geography_detail=brief.geography_detail,
            season=brief.season,
            required_text=brief.required_text,
            aspect_ratio=brief.aspect_ratio,
            golden_key=None,
        )
        config: dict[str, Any] = {
            **source.config,
            "probe": {
                "brief_id": brief_id,
                "source_run_id": str(source.id),
                "source_candidate_id": str(candidate.id),
                "slot": candidate.slot,
            },
        }
        run = await repo.create_run(
            session,
            source.tenant_id,
            brief_id=copy.id,
            origin="golden",
            batch_label=probe_label(),
            status="queued",
            config=config,
            idempotency_key=f"{PROBE_PREFIX}{candidate.id}:{uuid.uuid4().hex[:8]}",
        )
        event = RunStatusEvent(run_id=run.id, status="queued")
        await repo.append_run_event(session, run.id, event.type, event_payload(event))
        run.spec = source.spec
        run.spec_version = source.spec_version
        await session.commit()
        return run


async def probe_one(
    ctx: GoldenContext, brief_id: str, source: m.Run, candidate: m.Candidate, ev: m.Evaluation
) -> ProbeOutcome:
    failed, unverified = _dims(ev)
    result = ProbeOutcome(brief_id, source.id, candidate.id, candidate.slot, failed, unverified)
    if source.spec is None or candidate.image_id is None:
        result.status, result.note = "skipped", "source run has no spec or the draft no image"
        return result
    run = await _create_probe_run(ctx, source, candidate, brief_id)
    result.probe_run_id = run.id
    with bind_run(run.id):  # ledger rows carry the run id (cost, budget), as in execute_run
        return await _probe_loop(ctx, result, run, source, candidate, ev)


async def _probe_loop(
    ctx: GoldenContext,
    result: ProbeOutcome,
    run: m.Run,
    source: m.Run,
    candidate: m.Candidate,
    ev: m.Evaluation,
) -> ProbeOutcome:
    deps = ctx.deps
    loaded = await _load(deps, run.id)
    log_ = RunLog(deps.sessionmaker, run.id)
    spec = CreativeSpec.model_validate(source.spec)
    facts = parse_facts(loaded.product.reference_facts)
    orch = Orchestrator(deps, log_, loaded, spec, facts)
    for _ in range(deps.settings.n_candidates):  # the real run's first round, already spent
        orch.budget.reserve(deps.settings.image_model_candidate)

    async with deps.sessionmaker() as session:
        image = await session.get(m.Image, candidate.image_id)
    if image is None:
        raise AppError(404, "not-found", "draft image row is missing")
    data = deps.blobs.read(image.sha256)
    seed, seed_image = await orch._store(  # pyright: ignore[reportPrivateUsage]
        sha256=image.sha256,
        width=image.width,
        height=image.height,
        size=image.bytes,
        mime=image.mime,
        kind="initial",
        attempt=0,
        slot=candidate.slot,
        parent=None,
        requested_model=candidate.requested_model,
        served_model=candidate.served_model or "",
        prompt_version=candidate.prompt_version or "",
        instruction=None,
        cached=True,
    )
    await log_.status("evaluating")
    evaluation = await deps.evaluator.evaluate(data, loaded.reference, orch.target)
    scored = Scored(seed, seed_image, data, evaluation, orch.order)
    orch.order += 1
    await orch._persist_evaluation(scored)  # pyright: ignore[reportPrivateUsage]
    orch.pool.append(scored)

    outcome = await orch.loop()
    fields: dict[str, Any] = {
        "first_attempt_pass": evaluation.passed,
        "repair_count": orch.repairs_used,
    }
    if outcome.status == "passed" and outcome.approved is not None:
        await _finish(
            deps,
            log_,
            loaded.run,
            "passed",
            outcome=outcome.outcome,
            approved_candidate_id=outcome.approved.candidate.id,
            best_candidate_id=outcome.approved.candidate.id,
            reason=outcome.reason,
            **fields,
        )
    else:
        best = outcome.best
        await _finish(
            deps,
            log_,
            loaded.run,
            "needs_review",
            best_candidate_id=best.candidate.id if best else None,
            reason=outcome.reason,
            **fields,
        )
    result.status, result.reason, result.outcome = outcome.status, outcome.reason, outcome.outcome
    result.repairs = orch.repairs_used
    for entry in orch.pool:
        dims = entry.evaluation.dimensions
        result.steps.append(
            ProbeStep(
                kind=entry.kind,
                image_sha=entry.image.sha256,
                model=entry.candidate.served_model,
                verdict="pass" if entry.evaluation.passed else "fail",
                failed=[d for d, v in dims.items() if v.passed is False],
                unverified=[d for d, v in dims.items() if v.passed is None],
            )
        )
    async with deps.sessionmaker() as session:
        finished = await session.get(m.Run, run.id)
        events = await session.scalars(
            select(m.RunEvent).where(m.RunEvent.run_id == run.id).order_by(m.RunEvent.seq)
        )
        result.rows = [str(e.payload.get("row")) for e in events if e.type == "repair.started"]
    if finished is not None:
        result.cost_usd = float(finished.cost_usd)
        result.latency_ms = finished.latency_ms
    if evaluation.passed != (ev.verdict == "pass"):
        result.note = f"re-evaluation disagrees with the source verdict ({ev.verdict})"
    return result


async def run_probe(
    ctx: GoldenContext, paths: GoldenPaths, only: set[str] | None = None
) -> list[ProbeOutcome]:
    """Sequential (each probe is a handful of image calls; the runs stay easy to read)."""
    drafts = await failing_drafts(ctx, paths, only)
    return [await probe_one(ctx, *item) for item in drafts]
