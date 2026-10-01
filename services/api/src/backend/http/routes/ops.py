"""Operational summary over the model-call ledger, plus run metrics (architecture
"Observability": approved rate, first-attempt pass, repairs, overlay rate, $/approved ad,
p50/p95 time to an approvable ad)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models_calls import ModelCall
from backend.db.session import get_session
from backend.domain.adstudio.decisions import RunMetrics, run_metrics
from backend.http.deps import get_tenant

router = APIRouter(prefix="/v1/ops", tags=["ops"])


class OpsSummary(BaseModel):
    window: int
    count: int
    by_kind: dict[str, int]
    p50_latency_ms: float | None
    p95_latency_ms: float | None
    total_cost_usd: float
    avg_cost_usd: float | None
    cache_hit_rate: float | None
    fallback_rate: float | None
    error_rate: float | None
    runs: RunMetrics | None = Field(
        default=None, description="Run metrics over the most recent `run_window` runs"
    )


def _percentile(values: list[int], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return float(ordered[idx])


def _rate(n: int, d: int) -> float | None:
    return round(n / d, 4) if d else None


@router.get("/summary")
async def summary(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[str, Depends(get_tenant)],
    window: Annotated[int, Query(ge=1, le=10_000)] = 200,
    run_window: Annotated[int, Query(ge=1, le=10_000)] = 200,
) -> OpsSummary:
    rows = (
        await session.scalars(select(ModelCall).order_by(ModelCall.created_at.desc()).limit(window))
    ).all()
    by_kind: dict[str, int] = {}
    for r in rows:
        by_kind[r.kind] = by_kind.get(r.kind, 0) + 1
    ok_latencies = [r.latency_ms for r in rows if r.status == "ok" and not r.cached]
    total = sum(r.est_cost_usd for r in rows)
    n = len(rows)
    runner = request.app.state.runner
    runs = await run_metrics(
        session, tenant_id, window=run_window, active=runner.active, queued=runner.queued
    )
    return OpsSummary(
        window=window,
        count=n,
        by_kind=by_kind,
        p50_latency_ms=_percentile(ok_latencies, 50),
        p95_latency_ms=_percentile(ok_latencies, 95),
        total_cost_usd=round(total, 6),
        avg_cost_usd=round(total / n, 6) if n else None,
        cache_hit_rate=_rate(sum(r.cached for r in rows), n),
        fallback_rate=_rate(sum(r.fallback_used for r in rows), n),
        error_rate=_rate(sum(r.status != "ok" for r in rows), n),
        runs=runs,
    )
