"""Tenant-scoped repositories for the ad studio tables.

Every function takes `tenant_id` (or reaches the tenant through a parent row) so swapping the demo
tenant for OIDC claims later is a dependency change, not a query rewrite. Functions flush but never
commit: the caller owns the transaction.
"""

import base64
import binascii
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Select, and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.errors import AppError
from backend.db.models_adstudio import (
    RUN_ACTIVE_STATUSES,
    Brief,
    Candidate,
    Evaluation,
    EvaluationCheck,
    Image,
    Label,
    Product,
    Run,
    RunDecision,
    RunEvent,
)
from backend.db.models_calls import ModelCall

DEFAULT_LIMIT = 20
MAX_LIMIT = 100


# --- cursor pagination over (created_at DESC, id DESC) -------------------------------------------


@dataclass(frozen=True)
class Cursor:
    created_at: datetime
    id: uuid.UUID

    def encode(self) -> str:
        raw = json.dumps([self.created_at.isoformat(), str(self.id)]).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @classmethod
    def decode(cls, token: str) -> "Cursor":
        try:
            raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
            created, ident = json.loads(raw)
            return cls(datetime.fromisoformat(created), uuid.UUID(ident))
        except (ValueError, TypeError, binascii.Error) as exc:
            raise AppError(
                422, "validation-error", "Invalid cursor", "Cursor is malformed."
            ) from exc


@dataclass(frozen=True)
class PageResult[T]:
    items: list[T]
    next_cursor: str | None


async def _page[M: (Product, Run)](
    session: AsyncSession, stmt: Select[tuple[M]], model: type[M], limit: int, cursor: str | None
) -> PageResult[M]:
    limit = max(1, min(limit, MAX_LIMIT))
    if cursor:
        c = Cursor.decode(cursor)
        stmt = stmt.where(
            or_(
                model.created_at < c.created_at,
                and_(model.created_at == c.created_at, model.id < c.id),
            )
        )
    stmt = stmt.order_by(model.created_at.desc(), model.id.desc()).limit(limit + 1)
    rows = list((await session.scalars(stmt)).all())
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = Cursor(rows[-1].created_at, rows[-1].id).encode()
    return PageResult(rows, next_cursor)


# --- images --------------------------------------------------------------------------------------


async def get_image(session: AsyncSession, tenant_id: str, image_id: uuid.UUID) -> Image | None:
    return await session.scalar(
        select(Image).where(Image.tenant_id == tenant_id, Image.id == image_id)
    )


async def get_image_by_sha(session: AsyncSession, tenant_id: str, sha256: str) -> Image | None:
    return await session.scalar(
        select(Image).where(Image.tenant_id == tenant_id, Image.sha256 == sha256)
    )


async def get_or_create_image(
    session: AsyncSession,
    tenant_id: str,
    *,
    sha256: str,
    source: str,
    mime: str,
    width: int,
    height: int,
    size: int,
) -> tuple[Image, bool]:
    """Idempotent by `(tenant_id, sha256)`. Returns (image, created)."""
    existing = await get_image_by_sha(session, tenant_id, sha256)
    if existing is not None:
        return existing, False
    image = Image(
        tenant_id=tenant_id,
        sha256=sha256,
        source=source,
        mime=mime,
        width=width,
        height=height,
        bytes=size,
    )
    try:
        # A savepoint, so losing the race to a concurrent insert of the same bytes rolls back only
        # this insert, not the caller's transaction.
        async with session.begin_nested():
            session.add(image)
    except IntegrityError:
        winner = await get_image_by_sha(session, tenant_id, sha256)
        if winner is None:
            raise
        return winner, False
    return image, True


# --- products ------------------------------------------------------------------------------------


async def get_product(
    session: AsyncSession, tenant_id: str, product_id: uuid.UUID
) -> Product | None:
    return await session.scalar(
        select(Product).where(Product.tenant_id == tenant_id, Product.id == product_id)
    )


async def get_product_by_image(
    session: AsyncSession, tenant_id: str, image_id: uuid.UUID
) -> Product | None:
    return await session.scalar(
        select(Product).where(Product.tenant_id == tenant_id, Product.image_id == image_id)
    )


async def create_product(
    session: AsyncSession,
    tenant_id: str,
    *,
    name: str,
    image_id: uuid.UUID,
    reference_facts: dict[str, Any] | None = None,
    facts_version: str | None = None,
) -> Product:
    product = Product(
        tenant_id=tenant_id,
        name=name,
        image_id=image_id,
        reference_facts=reference_facts,
        facts_version=facts_version,
    )
    session.add(product)
    await session.flush()
    await session.refresh(product)
    return product


async def list_products(
    session: AsyncSession, tenant_id: str, *, limit: int = DEFAULT_LIMIT, cursor: str | None = None
) -> PageResult[Product]:
    stmt = select(Product).where(Product.tenant_id == tenant_id)
    return await _page(session, stmt, Product, limit, cursor)


# --- briefs and runs -----------------------------------------------------------------------------


async def create_brief(session: AsyncSession, tenant_id: str, **fields: Any) -> Brief:
    brief = Brief(tenant_id=tenant_id, **fields)
    session.add(brief)
    await session.flush()
    return brief


async def create_run(
    session: AsyncSession, tenant_id: str, *, brief_id: uuid.UUID, **fields: Any
) -> Run:
    run = Run(tenant_id=tenant_id, brief_id=brief_id, **fields)
    session.add(run)
    await session.flush()
    await session.refresh(run)
    return run


async def get_brief(session: AsyncSession, tenant_id: str, brief_id: uuid.UUID) -> Brief | None:
    return await session.scalar(
        select(Brief).where(Brief.tenant_id == tenant_id, Brief.id == brief_id)
    )


async def get_run(session: AsyncSession, tenant_id: str, run_id: uuid.UUID) -> Run | None:
    return await session.scalar(select(Run).where(Run.tenant_id == tenant_id, Run.id == run_id))


async def get_run_for_update(
    session: AsyncSession, tenant_id: str, run_id: uuid.UUID
) -> Run | None:
    """The run row locked until the transaction ends (decisions serialise per run)."""
    return await session.scalar(
        select(Run).where(Run.tenant_id == tenant_id, Run.id == run_id).with_for_update()
    )


async def briefs_by_id(
    session: AsyncSession, tenant_id: str, brief_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, Brief]:
    if not brief_ids:
        return {}
    rows = (
        await session.scalars(
            select(Brief).where(Brief.tenant_id == tenant_id, Brief.id.in_(brief_ids))
        )
    ).all()
    return {row.id: row for row in rows}


async def recent_runs(session: AsyncSession, tenant_id: str, *, limit: int) -> Sequence[Run]:
    stmt = (
        select(Run)
        .where(Run.tenant_id == tenant_id)
        .order_by(Run.created_at.desc(), Run.id.desc())
        .limit(limit)
    )
    return (await session.scalars(stmt)).all()


async def evaluations_for_runs(
    session: AsyncSession, run_ids: Sequence[uuid.UUID]
) -> Sequence[Evaluation]:
    if not run_ids:
        return []
    stmt = select(Evaluation).where(Evaluation.run_id.in_(list(run_ids)))
    return (await session.scalars(stmt)).all()


async def get_run_by_idempotency_key(session: AsyncSession, tenant_id: str, key: str) -> Run | None:
    return await session.scalar(
        select(Run).where(Run.tenant_id == tenant_id, Run.idempotency_key == key)
    )


async def list_runs(
    session: AsyncSession,
    tenant_id: str,
    *,
    origin: str | None = None,
    status: str | None = None,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> PageResult[Run]:
    stmt = select(Run).where(Run.tenant_id == tenant_id)
    if origin:
        stmt = stmt.where(Run.origin == origin)
    if status:
        stmt = stmt.where(Run.status == status)
    return await _page(session, stmt, Run, limit, cursor)


# --- run events (append-only log, replayed by SSE) -----------------------------------------------


async def append_run_event(
    session: AsyncSession, run_id: uuid.UUID, type: str, payload: dict[str, Any]
) -> RunEvent:
    """Append with the next `seq` for the run. Callers serialise appends per run (one runner task
    owns a run), and the `(run_id, seq)` primary key rejects any concurrent duplicate."""
    last = await session.scalar(select(func.max(RunEvent.seq)).where(RunEvent.run_id == run_id))
    event = RunEvent(run_id=run_id, seq=(last or 0) + 1, type=type, payload=payload)
    session.add(event)
    await session.flush()
    return event


async def list_run_events(
    session: AsyncSession, run_id: uuid.UUID, *, after_seq: int = 0
) -> Sequence[RunEvent]:
    stmt = (
        select(RunEvent)
        .where(RunEvent.run_id == run_id, RunEvent.seq > after_seq)
        .order_by(RunEvent.seq)
    )
    return (await session.scalars(stmt)).all()


async def update_run(session: AsyncSession, run_id: uuid.UUID, **fields: Any) -> None:
    await session.execute(update(Run).where(Run.id == run_id).values(**fields))


async def stale_active_runs(session: AsyncSession, *, before: datetime) -> Sequence[Run]:
    """Non-terminal runs whose heartbeat (or creation, if none) is older than `before`."""
    stmt = select(Run).where(
        Run.status.in_(RUN_ACTIVE_STATUSES),
        func.coalesce(Run.heartbeat_at, Run.created_at) < before,
    )
    return (await session.scalars(stmt)).all()


async def run_cost_usd(
    session: AsyncSession, run_id: uuid.UUID, *, exclude_kind: str | None = None
) -> float:
    """Exact cost of a run: the ledger rows stamped with its id (cached calls cost 0)."""
    stmt = select(func.coalesce(func.sum(ModelCall.est_cost_usd), 0.0)).where(
        ModelCall.run_id == run_id
    )
    if exclude_kind is not None:
        stmt = stmt.where(ModelCall.kind != exclude_kind)
    total = await session.scalar(stmt)
    return float(total or 0.0)


# --- candidates and evaluations ------------------------------------------------------------------


async def create_candidate(session: AsyncSession, run_id: uuid.UUID, **fields: Any) -> Candidate:
    candidate = Candidate(run_id=run_id, **fields)
    session.add(candidate)
    await session.flush()
    await session.refresh(candidate)
    return candidate


async def update_candidate(session: AsyncSession, candidate_id: uuid.UUID, **fields: Any) -> None:
    await session.execute(update(Candidate).where(Candidate.id == candidate_id).values(**fields))


async def candidates_by_id(
    session: AsyncSession, candidate_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, Candidate]:
    if not candidate_ids:
        return {}
    rows = (await session.scalars(select(Candidate).where(Candidate.id.in_(candidate_ids)))).all()
    return {row.id: row for row in rows}


async def list_candidates(session: AsyncSession, run_id: uuid.UUID) -> Sequence[Candidate]:
    stmt = (
        select(Candidate)
        .where(Candidate.run_id == run_id)
        .order_by(Candidate.attempt, Candidate.slot, Candidate.created_at)
    )
    return (await session.scalars(stmt)).all()


async def upsert_evaluation(
    session: AsyncSession,
    *,
    image_id: uuid.UUID,
    run_id: uuid.UUID | None,
    candidate_id: uuid.UUID | None,
    evaluator_version: str,
    checks: Sequence[dict[str, Any]],
    **fields: Any,
) -> Evaluation:
    """One evaluation per (image, run, candidate, evaluator version): re-evaluating replaces its
    checks."""
    existing = await session.scalar(
        select(Evaluation).where(
            Evaluation.image_id == image_id,
            Evaluation.run_id == run_id if run_id is not None else Evaluation.run_id.is_(None),
            Evaluation.candidate_id == candidate_id
            if candidate_id is not None
            else Evaluation.candidate_id.is_(None),
            Evaluation.evaluator_version == evaluator_version,
        )
    )
    if existing is None:
        evaluation = Evaluation(
            image_id=image_id,
            run_id=run_id,
            candidate_id=candidate_id,
            evaluator_version=evaluator_version,
            **fields,
        )
        session.add(evaluation)
        await session.flush()
    else:
        evaluation = existing
        for name, value in {"candidate_id": candidate_id, **fields}.items():
            setattr(evaluation, name, value)
        old = (
            await session.scalars(
                select(EvaluationCheck).where(EvaluationCheck.evaluation_id == evaluation.id)
            )
        ).all()
        for row in old:
            await session.delete(row)
        await session.flush()
    for check in checks:
        session.add(EvaluationCheck(evaluation_id=evaluation.id, **check))
    await session.flush()
    return evaluation


async def evaluations_for_candidates(
    session: AsyncSession, candidate_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, tuple[Evaluation, list[EvaluationCheck]]]:
    """The latest evaluation per candidate with its checks."""
    if not candidate_ids:
        return {}
    evals = (
        await session.scalars(
            select(Evaluation)
            .where(Evaluation.candidate_id.in_(candidate_ids))
            .order_by(Evaluation.created_at)
        )
    ).all()
    latest: dict[uuid.UUID, Evaluation] = {}
    for ev in evals:
        if ev.candidate_id is not None:
            latest[ev.candidate_id] = ev
    ids = [ev.id for ev in latest.values()]
    checks = (
        await session.scalars(
            select(EvaluationCheck)
            .where(EvaluationCheck.evaluation_id.in_(ids))
            .order_by(EvaluationCheck.dimension, EvaluationCheck.check_name)
        )
    ).all()
    by_eval: dict[uuid.UUID, list[EvaluationCheck]] = {i: [] for i in ids}
    for check in checks:
        by_eval[check.evaluation_id].append(check)
    return {cid: (ev, by_eval[ev.id]) for cid, ev in latest.items()}


async def images_by_id(
    session: AsyncSession, tenant_id: str, image_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, Image]:
    if not image_ids:
        return {}
    rows = (
        await session.scalars(
            select(Image).where(Image.tenant_id == tenant_id, Image.id.in_(image_ids))
        )
    ).all()
    return {row.id: row for row in rows}


# --- human decisions and labels ------------------------------------------------------------------


async def create_decision(session: AsyncSession, run_id: uuid.UUID, **fields: Any) -> RunDecision:
    decision = RunDecision(run_id=run_id, **fields)
    session.add(decision)
    await session.flush()
    await session.refresh(decision)
    return decision


async def list_decisions(session: AsyncSession, run_id: uuid.UUID) -> Sequence[RunDecision]:
    stmt = select(RunDecision).where(RunDecision.run_id == run_id).order_by(RunDecision.created_at)
    return (await session.scalars(stmt)).all()


async def latest_decisions(
    session: AsyncSession, run_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, RunDecision]:
    if not run_ids:
        return {}
    rows = (
        await session.scalars(
            select(RunDecision)
            .where(RunDecision.run_id.in_(run_ids))
            .order_by(RunDecision.created_at)
        )
    ).all()
    return {row.run_id: row for row in rows}


async def upsert_label(
    session: AsyncSession, *, image_id: uuid.UUID, labeller: str, **fields: Any
) -> Label:
    """One label per (image, labeller): a later label from the same person replaces it."""
    label = await session.scalar(
        select(Label).where(Label.image_id == image_id, Label.labeller == labeller)
    )
    if label is None:
        label = Label(image_id=image_id, labeller=labeller, **fields)
        session.add(label)
    else:
        for name, value in fields.items():
            setattr(label, name, value)
    await session.flush()
    return label
