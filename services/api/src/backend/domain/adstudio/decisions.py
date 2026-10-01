"""Human release (reconciliation "Human release", production-readiness D6), run history and run
metrics for the status page.

The gate only proposes: `passed` means "ready for review", and nothing is exported until a human
approves. Approving a `needs_review` run overrides the gate, so it needs a reason and writes a
`labels` row (`labeller = human:<actor>`) the meta-eval can learn from; rejecting a `passed` run
is the opposite override and is labelled too. Every decision writes a `run_decisions` audit row
and a `run.status` event, in the same transaction as the status change.
"""

import statistics
import uuid

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.errors import AppError
from backend.db import models_adstudio as m
from backend.db import repositories as repo
from backend.domain.adstudio.evaluator.schemas import DIMENSION_ORDER
from backend.domain.adstudio.events import RunStatusEvent, event_payload
from backend.domain.adstudio.schemas import (
    DecisionCreate,
    DecisionView,
    Page,
    RunSummary,
    image_url,
)

DECIDABLE = ("passed", "needs_review")
LABEL_RUBRIC = "decision-1"
MIN_REASON_CHARS = 3


def _reason(body: DecisionCreate) -> str | None:
    reason = (body.reason or "").strip()
    return reason or None


async def decide(
    session: AsyncSession, tenant_id: str, run_id: uuid.UUID, body: DecisionCreate
) -> DecisionView:
    run = await repo.get_run_for_update(session, tenant_id, run_id)
    if run is None:
        raise AppError(404, "not-found", "Run not found")
    if run.status not in DECIDABLE:
        raise AppError(
            409,
            "run-not-decidable",
            "Run can't be decided",
            f"The run is `{run.status}`; only `passed` or `needs_review` runs take a decision.",
        )
    reason = _reason(body)
    approve = body.action == "approve"
    is_override = (approve and run.status == "needs_review") or (
        not approve and run.status == "passed"
    )
    if approve and run.status == "needs_review" and len(reason or "") < MIN_REASON_CHARS:
        raise AppError(
            422,
            "override-reason-required",
            "Reason required",
            "Approving a run the gate held (needs_review) overrides the evaluator; say why.",
        )
    candidate_id = run.approved_candidate_id or run.best_candidate_id
    candidate = (
        (await repo.candidates_by_id(session, [candidate_id])).get(candidate_id)
        if candidate_id
        else None
    )
    if approve and (candidate is None or candidate.image_id is None):
        raise AppError(
            409,
            "nothing-to-approve",
            "Nothing to approve",
            "The run produced no image to approve; reject it or run the brief again.",
        )

    previous = run.status
    status = "approved" if approve else "rejected"
    label_id: uuid.UUID | None = None
    if is_override and candidate is not None and candidate.image_id is not None:
        verdict = approve  # the human's call on the whole ad
        label = await repo.upsert_label(
            session,
            image_id=candidate.image_id,
            labeller=f"human:{body.actor}",
            run_id=run.id,
            text_ok=True if verdict else None,
            product_ok=True if verdict else None,
            context_ok=True if verdict else None,
            composition_ok=True if verdict else None,
            overall_ok=verdict,
            rubric_version=LABEL_RUBRIC,
            notes=reason,
        )
        label_id = label.id
    decision = await repo.create_decision(
        session,
        run.id,
        action=body.action,
        reason=reason,
        actor=body.actor,
        previous_status=previous,
        is_override=is_override,
    )
    approved_id = candidate.id if approve and candidate is not None else run.approved_candidate_id
    await repo.update_run(session, run.id, status=status, approved_candidate_id=approved_id)
    if approve and candidate is not None:
        await repo.update_candidate(session, candidate.id, status="approved")
    event = RunStatusEvent(run_id=run.id, status=status)
    await repo.append_run_event(session, run.id, event.type, event_payload(event))
    await session.commit()
    return DecisionView(
        id=decision.id,
        run_id=run.id,
        action=body.action,
        reason=reason,
        actor=body.actor,
        previous_status=previous,
        status=status,
        is_override=is_override,
        approved_candidate_id=approved_id,
        label_id=label_id,
        created_at=decision.created_at,
    )


# --- history -------------------------------------------------------------------------------------


async def list_run_summaries(
    session: AsyncSession,
    tenant_id: str,
    *,
    origin: str | None,
    status: str | None,
    limit: int,
    cursor: str | None,
) -> Page[RunSummary]:
    page = await repo.list_runs(
        session, tenant_id, origin=origin, status=status, limit=limit, cursor=cursor
    )
    briefs = await repo.briefs_by_id(session, tenant_id, [r.brief_id for r in page.items])
    shown = {r.id: r.approved_candidate_id or r.best_candidate_id for r in page.items}
    candidates = await repo.candidates_by_id(session, [c for c in shown.values() if c])
    items: list[RunSummary] = []
    for run in page.items:
        brief = briefs[run.brief_id]
        shown_id = shown[run.id]
        cand = candidates.get(shown_id) if shown_id else None
        items.append(
            RunSummary(
                id=run.id,
                origin=run.origin,
                status=run.status,
                outcome=run.outcome,
                product_id=brief.product_id,
                geography_code=brief.geography_code,
                season=brief.season,
                required_text=brief.required_text,
                aspect_ratio=brief.aspect_ratio,
                golden_key=brief.golden_key,
                first_attempt_pass=run.first_attempt_pass,
                repair_count=run.repair_count,
                cost_usd=float(run.cost_usd),
                latency_ms=run.latency_ms,
                approved_candidate_id=run.approved_candidate_id,
                best_candidate_id=run.best_candidate_id,
                thumbnail_url=image_url(cand.image_id) if cand and cand.image_id else None,
                created_at=run.created_at,
                finished_at=run.finished_at,
            )
        )
    return Page[RunSummary](items=items, next_cursor=page.next_cursor)


# --- run metrics (status page) -------------------------------------------------------------------

FINISHED = m.RUN_TERMINAL_STATUSES + m.RUN_DECIDED_STATUSES


class RunMetrics(BaseModel):
    """Run numbers over the most recent `window` runs (architecture "Observability")."""

    window: int
    count: int = Field(description="Runs in the window")
    by_status: dict[str, int]
    finished: int = Field(description="Runs that reached a terminal or decided status")
    approved: int = Field(description="Runs a human approved")
    approved_rate: float | None = Field(description="approved / finished")
    gate_pass_rate: float | None = Field(
        description="Runs whose gate passed a candidate (outcome set) / finished"
    )
    first_attempt_pass_rate: float | None = Field(
        description="A first-round candidate passed / runs with candidates"
    )
    mean_repairs: float | None
    overlay_rate: float | None = Field(description="outcome overlay / runs with an outcome")
    override_count: int = Field(description="Approved runs the gate had held (needs_review)")
    total_cost_usd: float
    cost_per_approved_ad: float | None = Field(
        description="Spend of every finished run / approved runs"
    )
    cost_per_gate_pass: float | None = Field(
        description="Spend of every finished run / runs whose gate passed"
    )
    p50_ms_to_approved: float | None = Field(
        description="Pipeline latency (run created -> gate passed) of gate-passed runs, p50"
    )
    p95_ms_to_approved: float | None
    active: int = 0
    queued: int = 0
    dimension_pass_rates: dict[str, float | None] = Field(
        default_factory=dict[str, float | None],
        description="Per dimension (technical, text, product, context, composition): passing / "
        "judged candidate evaluations of the window's runs (unverified and older evaluations "
        "without the dimension are left out)",
    )


def _rate(n: int, d: int) -> float | None:
    return round(n / d, 4) if d else None


def _percentile(values: list[int], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return float(ordered[idx])


async def run_metrics(
    session: AsyncSession, tenant_id: str, *, window: int, active: int = 0, queued: int = 0
) -> RunMetrics:
    runs = list(await repo.recent_runs(session, tenant_id, limit=window))
    by_status: dict[str, int] = {}
    for run in runs:
        by_status[run.status] = by_status.get(run.status, 0) + 1
    done = [r for r in runs if r.status in FINISHED]
    approved = [r for r in done if r.status == "approved"]
    gate_passed = [r for r in done if r.outcome is not None]
    first = [r.first_attempt_pass for r in done if r.first_attempt_pass is not None]
    total = sum(float(r.cost_usd) for r in done)
    latest = await repo.latest_decisions(session, [r.id for r in approved])
    overrides = sum(1 for d in latest.values() if d.is_override)
    latencies = [r.latency_ms for r in gate_passed if r.latency_ms is not None]
    evaluations = await repo.evaluations_for_runs(session, [r.id for r in runs])
    pass_rates: dict[str, float | None] = {}
    for dim in DIMENSION_ORDER:
        values = [v for e in evaluations if (v := getattr(e, f"{dim}_pass")) is not None]
        pass_rates[dim] = _rate(sum(1 for v in values if v), len(values))
    return RunMetrics(
        window=window,
        count=len(runs),
        by_status=by_status,
        finished=len(done),
        approved=len(approved),
        approved_rate=_rate(len(approved), len(done)),
        gate_pass_rate=_rate(len(gate_passed), len(done)),
        first_attempt_pass_rate=_rate(sum(first), len(first)),
        mean_repairs=round(statistics.fmean(r.repair_count for r in done), 3) if done else None,
        overlay_rate=_rate(sum(r.outcome == "overlay" for r in gate_passed), len(gate_passed)),
        override_count=overrides,
        total_cost_usd=round(total, 6),
        cost_per_approved_ad=round(total / len(approved), 6) if approved else None,
        cost_per_gate_pass=round(total / len(gate_passed), 6) if gate_passed else None,
        p50_ms_to_approved=_percentile(latencies, 50),
        p95_ms_to_approved=_percentile(latencies, 95),
        active=active,
        queued=queued,
        dimension_pass_rates=pass_rates,
    )
