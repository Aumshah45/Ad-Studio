"""`golden run`: every brief of `briefs.yaml` through the same `execute_run` the API uses.

- Idempotent per `golden_key` (brief + pipeline version + image client): a brief whose latest run
  ended `passed` / `needs_review` (or was decided) is skipped; `failed` / `interrupted` /
  `cancelled` runs are re-run, so an interrupted batch resumes where it stopped. Image calls are
  cached (`fresh=false`), so a re-run does not pay twice for images it already has. `force=True`
  starts a new run even after a finished one; export takes the latest, so it supersedes it (used
  when a run's inputs were wrong, e.g. product facts from the offline judge).
- `origin=golden`, `batch_label=golden:<pipeline version>`; at most `concurrency` runs at once
  (default 2) through the in-process runner (heartbeats included).
- A batch budget (default $5, ai-design §11) stops new runs once the golden runs' ledger spend plus
  one per-run cap for each run in flight would exceed it. The API's $3/day cap is for UI traffic
  and is not applied here (docs/stack-lock.md).
"""

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import func, select

from backend.core.errors import AppError
from backend.db import models_adstudio as m
from backend.db import repositories as repo
from backend.domain.adstudio.events import RunStatusEvent, event_payload
from backend.domain.adstudio.overlay import ensure_overlay_supported
from backend.domain.adstudio.pipeline import (
    PIPELINE_VERSION,
    execute_run,
    mark_interrupted,
    pipeline_config,
)
from backend.domain.adstudio.products import create_product_from_upload
from backend.domain.adstudio.profile import ensure_reference_facts, needs_profile
from backend.domain.adstudio.runs import validate_required_text
from backend.golden.context import GoldenContext
from backend.golden.dataset import GoldenBrief, GoldenPaths, GoldenSet, golden_key, load_golden
from backend.guardrails.input import injection_signals
from backend.jobs.runner import STALE_AFTER_S, RunRunner

log = structlog.get_logger(__name__)
DONE_STATUSES = ("passed", "needs_review", "approved", "rejected")
RETRY_STATUSES = ("failed", "interrupted", "cancelled")
BATCH_PREFIX = "golden:"


@dataclass
class BriefOutcome:
    brief_id: str
    golden_key: str
    run_id: uuid.UUID | None = None
    status: str = "pending"
    outcome: str | None = None
    repair_count: int = 0
    cost_usd: float = 0.0
    latency_ms: int | None = None
    note: str = ""


def batch_label() -> str:
    return f"{BATCH_PREFIX}{PIPELINE_VERSION}"


async def ensure_products(
    ctx: GoldenContext, paths: GoldenPaths, golden: GoldenSet
) -> dict[str, uuid.UUID]:
    """Upload the golden reference photos (idempotent by content, like `POST /v1/products`)."""
    ids: dict[str, uuid.UUID] = {}
    for pid, product in golden.products.items():
        data = paths.product_file(golden, pid).read_bytes()
        async with ctx.deps.sessionmaker() as session:
            result = await create_product_from_upload(
                session,
                ctx.deps.blobs,
                ctx.tenant,
                name=f"{pid} {product.name}"[:120],
                data=data,
                max_bytes=ctx.settings.max_upload_bytes,
            )
        if not ctx.deps.blobs.exists(result.image.sha256):
            ctx.deps.blobs.put(result.data)  # the row exists but this blob store lacks the bytes
        ids[pid] = result.product.id
    return ids


@dataclass
class ProfileOutcome:
    product_id: str
    facts: dict[str, Any] | None
    refreshed: bool


async def profile_products(
    ctx: GoldenContext, paths: GoldenPaths, *, force: bool = False
) -> list[ProfileOutcome]:
    """Upload (idempotent) and (re-)profile every golden product with the configured judge: facts
    from an older profile version (pp-1 has no real-world size) or an offline judge are replaced,
    and `force` re-profiles current ones too (the judge call is cached by image + prompt)."""
    golden = load_golden(paths)
    ids = await ensure_products(ctx, paths, golden)
    vision = ctx.deps.evaluator.vision
    judge = vision.model if vision is not None and vision.configured() else None
    out: list[ProfileOutcome] = []
    for pid, product_id in ids.items():
        async with ctx.deps.sessionmaker() as session:
            product = await session.get(m.Product, product_id)
            if product is None:  # pragma: no cover - just created
                continue
            image = await repo.get_image(session, product.tenant_id, product.image_id)
            if image is None:  # pragma: no cover
                continue
            stale = force or needs_profile(product.reference_facts, judge)
            if force and judge is not None:
                product.reference_facts = None
            facts = await ensure_reference_facts(
                session, product, ctx.deps.blobs.read(image.sha256), vision
            )
        out.append(ProfileOutcome(pid, facts, stale and judge is not None))
    return out


async def golden_spend(ctx: GoldenContext) -> float:
    async with ctx.deps.sessionmaker() as session:
        total = await session.scalar(
            select(func.coalesce(func.sum(m.Run.cost_usd), 0)).where(
                m.Run.origin == "golden", m.Run.batch_label == batch_label()
            )
        )
    return float(total or 0)


async def latest_run(ctx: GoldenContext, brief_id: uuid.UUID) -> m.Run | None:
    async with ctx.deps.sessionmaker() as session:
        return await session.scalar(
            select(m.Run)
            .where(m.Run.brief_id == brief_id)
            .order_by(m.Run.created_at.desc(), m.Run.id.desc())
            .limit(1)
        )


async def find_brief(ctx: GoldenContext, key: str) -> m.Brief | None:
    async with ctx.deps.sessionmaker() as session:
        return await session.scalar(select(m.Brief).where(m.Brief.golden_key == key))


async def _create_brief(
    ctx: GoldenContext, brief: GoldenBrief, key: str, product_id: uuid.UUID, text: str
) -> m.Brief:
    async with ctx.deps.sessionmaker() as session:
        row = await repo.create_brief(
            session,
            ctx.tenant,
            product_id=product_id,
            geography_code=brief.geography.upper(),
            geography_detail=None,
            season=brief.season.strip(),
            required_text=text,
            aspect_ratio=brief.aspect_ratio,
            golden_key=key,
        )
        await session.commit()
        return row


async def _create_run(ctx: GoldenContext, brief: GoldenBrief, row: m.Brief, attempt: int) -> m.Run:
    deps = ctx.deps
    resolution = await deps.planner.resolve(row.geography_code, None, row.season)
    config = pipeline_config(
        ctx.settings,
        deps.image_client,
        fresh=False,
        resolution=resolution,
        input_flags=injection_signals(row.required_text),
    )
    config["golden"] = {"brief_id": brief.id, "golden_key": row.golden_key, "attempt": attempt}
    async with deps.sessionmaker() as session:
        run = await repo.create_run(
            session,
            ctx.tenant,
            brief_id=row.id,
            origin="golden",
            batch_label=batch_label(),
            status="queued",
            config=config,
            idempotency_key=f"{BATCH_PREFIX}{row.golden_key}:{attempt}:{uuid.uuid4().hex[:8]}",
        )
        event = RunStatusEvent(run_id=run.id, status="queued")
        await repo.append_run_event(session, run.id, event.type, event_payload(event))
        await session.commit()
        return run


async def _run_count(ctx: GoldenContext, brief_id: uuid.UUID) -> int:
    async with ctx.deps.sessionmaker() as session:
        n = await session.scalar(select(func.count()).where(m.Run.brief_id == brief_id))
    return int(n or 0)


def _fill(outcome: BriefOutcome, run: m.Run) -> BriefOutcome:
    outcome.run_id = run.id
    outcome.status = run.status
    outcome.outcome = run.outcome
    outcome.repair_count = run.repair_count
    outcome.cost_usd = float(run.cost_usd)
    outcome.latency_ms = run.latency_ms
    return outcome


async def golden_run(
    ctx: GoldenContext,
    paths: GoldenPaths,
    *,
    only: set[str] | None = None,
    concurrency: int = 2,
    budget_usd: float = 5.0,
    force: bool = False,
) -> list[BriefOutcome]:
    deps = ctx.deps
    if not deps.image_client.configured():
        raise AppError(
            503,
            "image-unconfigured",
            "Image model not configured",
            "Set GOOGLE_API_KEY in .env, or pass --fake for a dry run with the fake clients.",
        )
    golden = load_golden(paths)
    briefs = [b for b in golden.briefs if only is None or b.id in only]
    products = await ensure_products(ctx, paths, golden)
    client = deps.image_client.name
    runner = RunRunner(
        lambda rid: execute_run(rid, deps),
        sessionmaker=deps.sessionmaker,
        max_concurrent=concurrency,
        queue_cap=max(20, len(briefs)),
    )
    semaphore = asyncio.Semaphore(max(1, concurrency))
    in_flight = 0
    budget_lock = asyncio.Lock()

    async def one(brief: GoldenBrief) -> BriefOutcome:
        nonlocal in_flight
        key = golden_key(brief.id, PIPELINE_VERSION, client)
        outcome = BriefOutcome(brief.id, key)
        try:
            text = validate_required_text(brief.required_text)
            ensure_overlay_supported(text)
        except AppError as exc:
            outcome.status, outcome.note = "skipped", f"invalid brief: {exc.type}"
            return outcome
        row = await find_brief(ctx, key) or await _create_brief(
            ctx, brief, key, products[brief.product], text
        )
        previous = await latest_run(ctx, row.id)
        if previous is not None and previous.status in DONE_STATUSES and not force:
            outcome.note = "done (idempotent skip)"
            return _fill(outcome, previous)
        if previous is not None and previous.status not in (*RETRY_STATUSES, *DONE_STATUSES):
            beat = previous.heartbeat_at or previous.created_at
            if datetime.now(UTC) - beat < timedelta(seconds=STALE_AFTER_S):
                outcome.note = "in progress in another process"
                return _fill(outcome, previous)
            await mark_interrupted(deps, previous.id)  # a crashed batch: resume it
        async with semaphore:
            async with budget_lock:
                spent = await golden_spend(ctx)
                if spent + (in_flight + 1) * ctx.settings.ad_budget_usd > budget_usd:
                    outcome.status = "skipped"
                    outcome.note = f"batch budget: ${spent:.2f} spent of ${budget_usd:.2f}"
                    return outcome
                in_flight += 1
            try:
                try:
                    run = await _create_run(ctx, brief, row, await _run_count(ctx, row.id) + 1)
                except AppError as exc:
                    outcome.status, outcome.note = "skipped", f"{exc.type}: {exc.detail or ''}"
                    return outcome
                log.info("golden_run_started", brief=brief.id, run_id=str(run.id))
                runner.submit(run.id)
                await runner.join(run.id, timeout_s=ctx.settings.ad_wallclock_s + 300)
            finally:
                in_flight -= 1
        async with deps.sessionmaker() as session:
            done = await session.get(m.Run, run.id)
        return _fill(outcome, done) if done is not None else outcome

    try:
        return list(await asyncio.gather(*(one(b) for b in briefs)))
    finally:
        await runner.shutdown()
