"""Runs: create (202, idempotent), history, snapshot, the SSE event stream with Last-Event-ID
replay, the human decision (approve / reject, audited) and cancel."""

import asyncio
import hashlib
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.responses import StreamingResponse

from backend.core.errors import AppError
from backend.core.settings import Settings
from backend.db import models_adstudio as m
from backend.db import repositories as repo
from backend.db.session import get_session
from backend.domain.adstudio.budget import ensure_daily_budget
from backend.domain.adstudio.decisions import decide, list_run_summaries
from backend.domain.adstudio.events import FINISHED, RunEvent, event_from_row
from backend.domain.adstudio.image_clients import ImageClient
from backend.domain.adstudio.overlay import ensure_overlay_supported
from backend.domain.adstudio.pipeline import PipelineDeps, mark_cancelled, pipeline_config
from backend.domain.adstudio.runs import (
    create_run,
    events_url,
    run_detail,
    validate_required_text,
)
from backend.domain.adstudio.schemas import (
    DecisionCreate,
    DecisionView,
    Page,
    RunAccepted,
    RunCancelled,
    RunCreate,
    RunDetail,
    RunSummary,
)
from backend.guardrails.input import injection_signals
from backend.http.deps import get_app_settings, get_tenant
from backend.http.limits import IpRateLimiter, Slot, client_ip, get_run_limiter
from backend.http.sse import sse_response
from backend.jobs.runner import Reservation, RunRunner

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/v1/runs", tags=["runs"])
PROBLEM: dict[str, Any] = {"content": {"application/problem+json": {}}}
SSE_POLL_S = 0.5


def get_runner(request: Request) -> RunRunner:
    return request.app.state.runner


def get_image_client(request: Request) -> ImageClient:
    return request.app.state.pipeline.image_client


def get_pipeline(request: Request) -> PipelineDeps:
    return request.app.state.pipeline


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        200: {"model": RunAccepted, "description": "Replay of the same Idempotency-Key and body"},
        404: PROBLEM,
        409: PROBLEM,
        422: PROBLEM,
        429: PROBLEM,
        503: PROBLEM,
    },
)
async def create_run_route(
    body: RunCreate,
    request: Request,
    response: Response,
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=128,
            description="Required. The same key and body return the original run.",
        ),
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[str, Depends(get_tenant)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    runner: Annotated[RunRunner, Depends(get_runner)],
    image_client: Annotated[ImageClient, Depends(get_image_client)],
    pipeline: Annotated[PipelineDeps, Depends(get_pipeline)],
    limiter: Annotated[IpRateLimiter, Depends(get_run_limiter)],
) -> RunAccepted:
    """Start a run for a brief. Returns 202 at once; follow `events_url` (SSE) for progress."""
    required_text = validate_required_text(body.required_text)
    ensure_overlay_supported(required_text)  # 422 unsupported-script: the guarantee needs glyphs
    ip = client_ip(request)
    slots: list[Slot] = []
    reservations: list[Reservation] = []

    async def prepare() -> dict[str, Any]:
        if not image_client.configured():
            raise AppError(
                503,
                "image-unconfigured",
                "Image model not configured",
                "Set GOOGLE_API_KEY in .env, or IMAGE_CLIENT=fake for a local run.",
            )
        # 429 run-queue-full. The slot is held until `submit`, so a committed run is never
        # refused by the queue afterwards (which would orphan it as `queued`).
        reservations.append(runner.reserve())
        # 429 rate-limited: RUNS_PER_HOUR_PER_IP / RUNS_CONCURRENT_PER_IP. Only a new run takes a
        # slot; a replay of the same Idempotency-Key never reaches `prepare`.
        slots.append(limiter.reserve(ip))
        # 429 daily-budget-exceeded: the ledger's spend today has reached DAILY_BUDGET_USD.
        await ensure_daily_budget(session, settings.daily_budget_usd)
        # 422 unknown-geography / unknown-season before any spend (the model only on a miss).
        resolution = await pipeline.planner.resolve(
            body.geography_code, body.geography_detail, body.season
        )
        return pipeline_config(
            settings,
            image_client,
            fresh=body.fresh,
            resolution=resolution,
            input_flags=injection_signals(required_text),
        )

    try:
        result = await create_run(
            session,
            tenant_id,
            body,
            idempotency_key=idempotency_key,
            required_text=required_text,
            prepare=prepare,
        )
        run = result.run
        reservation = reservations[0] if reservations else None
        if result.created:
            runner.submit(run.id, reservation)
        elif reservation is not None:  # lost an idempotency race: the winner enqueued the run
            reservation.release()
    except BaseException:
        for reservation in reservations:
            reservation.release()
        for slot in slots:  # 422 / 429 / 503 after the reservation: give the slot back
            limiter.release(ip, slot)
        raise
    for slot in slots:
        if result.created:
            limiter.bind(slot, run.id)
        else:  # lost an idempotency race: the other request holds the slot
            limiter.release(ip, slot)
    if result.created:
        # The customer's copy is logged only as a length and a hash (PII stays out of logs).
        log.info(
            "run_created",
            run_id=str(run.id),
            required_text_chars=len(required_text),
            required_text_sha256=hashlib.sha256(required_text.encode()).hexdigest()[:16],
        )
    else:
        response.status_code = status.HTTP_200_OK
    return RunAccepted(
        run_id=run.id,
        brief_id=run.brief_id,
        status=run.status,
        events_url=events_url(run.id),
        run_url=f"/v1/runs/{run.id}",
    )


RunStatusFilter = Literal[
    "queued",
    "planning",
    "generating",
    "evaluating",
    "repairing",
    "fallback",
    "passed",
    "needs_review",
    "failed",
    "interrupted",
    "cancelled",
    "approved",
    "rejected",
]


@router.get("", responses={422: PROBLEM})
async def list_runs(
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[str, Depends(get_tenant)],
    origin: Annotated[Literal["ui", "golden"] | None, Query()] = None,
    status: Annotated[RunStatusFilter | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(description="`next_cursor` of the previous page")] = None,
) -> Page[RunSummary]:
    """Run history, newest first (cursor paging over created_at, id)."""
    return await list_run_summaries(
        session, tenant_id, origin=origin, status=status, limit=limit, cursor=cursor
    )


@router.post("/{run_id}/decision", responses={404: PROBLEM, 409: PROBLEM, 422: PROBLEM})
async def decide_run(
    run_id: uuid.UUID,
    body: DecisionCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[str, Depends(get_tenant)],
) -> DecisionView:
    """Human release: approve or reject a `passed` / `needs_review` run (audited).

    Approving a `needs_review` run overrides the gate: `reason` is required, and the decision is
    also stored as a human label on the approved image.
    """
    return await decide(session, tenant_id, run_id, body)


@router.post("/{run_id}/cancel", responses={404: PROBLEM, 409: PROBLEM})
async def cancel_run(
    run_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[str, Depends(get_tenant)],
    runner: Annotated[RunRunner, Depends(get_runner)],
    pipeline: Annotated[PipelineDeps, Depends(get_pipeline)],
) -> RunCancelled:
    """Stop an active run: its task is cancelled and the run ends `cancelled`."""
    run = await repo.get_run(session, tenant_id, run_id)
    if run is None:
        raise AppError(404, "not-found", "Run not found")
    if run.status not in m.RUN_ACTIVE_STATUSES:
        raise AppError(
            409,
            "run-not-active",
            "Run is not active",
            f"The run is `{run.status}`; only a queued or running run can be cancelled.",
        )
    pipeline.cancel_requests.add(run_id)
    try:
        await runner.cancel(run_id)
        # A queued task dies before `execute_run` starts, and an orphaned run has no task:
        # record the end here (a no-op if the task already did).
        await mark_cancelled(pipeline, run_id)
    finally:
        pipeline.cancel_requests.discard(run_id)
    session.expire_all()
    final = await repo.get_run(session, tenant_id, run_id)
    status = final.status if final is not None else "cancelled"
    return RunCancelled(run_id=run_id, status=status, cancelled=status == "cancelled")


@router.get("/{run_id}", responses={404: PROBLEM})
async def get_run(
    run_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[str, Depends(get_tenant)],
) -> RunDetail:
    """The authoritative snapshot of a run (reload-safe; the UI rebuilds its timeline from it)."""
    return await run_detail(session, tenant_id, run_id)


async def tail_events(
    sessionmaker: async_sessionmaker[AsyncSession],
    run_id: uuid.UUID,
    after_seq: int,
    *,
    poll_s: float = SSE_POLL_S,
) -> AsyncIterator[RunEvent]:
    """Replay events after `after_seq`, then poll the log until `run.finished`."""
    last = after_seq
    while True:
        async with sessionmaker() as session:
            rows = list(await repo.list_run_events(session, run_id, after_seq=last))
            run = await session.get(m.Run, run_id) if not rows else None
        finished = False
        for row in rows:
            yield event_from_row(run_id, row.seq, row.payload)
            last = row.seq
            finished = finished or row.type == FINISHED
        if finished:
            # Close after `run.finished`; a replay also carries anything logged after it (the
            # human decision's `run.status`).
            return
        if run is not None and run.status in m.RUN_TERMINAL_STATUSES + m.RUN_DECIDED_STATUSES:
            # Terminal with nothing left after `last`: the finish event was already delivered.
            return
        await asyncio.sleep(poll_s)


def _parse_last_event_id(value: str | None) -> int:
    if value is None or not value.strip():
        return 0
    try:
        return max(0, int(value.strip()))
    except ValueError as exc:
        raise AppError(
            422, "validation-error", "Invalid Last-Event-ID", "Expected an integer."
        ) from exc


@router.get(
    "/{run_id}/events",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": "RunEvent stream (see GET /v1/meta/run-events for the schema)",
        },
        404: PROBLEM,
    },
)
async def run_events(
    run_id: uuid.UUID,
    request: Request,
    tenant_id: Annotated[str, Depends(get_tenant)],
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    after: Annotated[
        int | None,
        Query(ge=0, description="Alternative to Last-Event-ID for clients without headers"),
    ] = None,
) -> StreamingResponse:
    """SSE: replays events with `seq > Last-Event-ID`, then tails until `run.finished`, then closes.

    Keep-alive comments every 15 s. Works after the run has ended (full replay).
    """
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.db.sessionmaker
    async with sessionmaker() as session:
        if await repo.get_run(session, tenant_id, run_id) is None:
            raise AppError(404, "not-found", "Run not found")
    after_seq = after if after is not None else _parse_last_event_id(last_event_id)
    poll_s = float(getattr(request.app.state, "sse_poll_s", SSE_POLL_S))
    return sse_response(tail_events(sessionmaker, run_id, after_seq, poll_s=poll_s))
