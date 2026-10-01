"""Run creation (idempotent), required-text validation and the `RunDetail` snapshot."""

import hashlib
import json
import unicodedata
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.errors import AppError
from backend.db import models_adstudio as m
from backend.db import repositories as repo
from backend.domain.adstudio.events import RunStatusEvent, event_payload
from backend.domain.adstudio.schemas import (
    BriefView,
    CandidateView,
    CheckView,
    EvaluationView,
    RunCreate,
    RunDetail,
    RunView,
    image_url,
)
from backend.domain.adstudio.textpolicy import violates_text_policy

MAX_TEXT_CHARS = 80
MAX_TEXT_LINES = 3
# Invisible or direction-changing characters can't be rendered faithfully (RT-03). ZWJ/ZWNJ are
# allowed because some scripts need them.
_BIDI = set(range(0x202A, 0x202F)) | set(range(0x2066, 0x206A)) | {0x200E, 0x200F, 0x061C}
_ZERO_WIDTH = {0x200B, 0x2060, 0xFEFF, 0x180E}
_ALLOWED_FORMAT = {0x200C, 0x200D}
# Hidden line breaks: only "\n" separates lines, so these would bypass the 3-line rule.
_HIDDEN_BREAKS = {"Zl", "Zp"}


def _invalid_text(detail: str) -> AppError:
    return AppError(422, "invalid-text", "Invalid required text", detail)


def validate_required_text(raw: str) -> str:
    """NFC, outer whitespace trimmed, 1-80 chars, <= 3 lines, no control/bidi/tag/zero-width chars,
    no hidden line/paragraph separators, not on the brand-safety blocklist (422 `text-policy`).

    Returns the exact string that is stored and rendered; it is never rewritten after this.
    """
    text = unicodedata.normalize("NFC", raw.replace("\r\n", "\n")).strip()
    if not text:
        raise _invalid_text("The required text is empty.")
    if len(text) > MAX_TEXT_CHARS:
        raise _invalid_text(f"The required text has {len(text)} characters; the limit is 80.")
    if text.count("\n") + 1 > MAX_TEXT_LINES:
        raise _invalid_text("The required text has more than 3 lines.")
    for ch in text:
        cp = ord(ch)
        cat = unicodedata.category(ch)
        if ch == "\n":
            continue
        if cat == "Cc" or cp in _BIDI or cp in _ZERO_WIDTH or 0xE0000 <= cp <= 0xE007F:
            raise _invalid_text(
                f"The required text contains an invisible or control character (U+{cp:04X})."
            )
        if cat == "Cf" and cp not in _ALLOWED_FORMAT:
            raise _invalid_text(f"The required text contains a format character (U+{cp:04X}).")
        if cat in _HIDDEN_BREAKS:
            raise _invalid_text(
                f"The required text contains a hidden line break (U+{cp:04X}); use a new line."
            )
    if violates_text_policy(text):
        raise AppError(
            422,
            "text-policy",
            "Text not allowed",
            "The required text contains a term our brand-safety policy does not allow in ads.",
        )
    return text


def request_hash(body: RunCreate) -> str:
    canonical = json.dumps(body.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True)
class CreatedRun:
    run: m.Run
    created: bool


def _idempotency_conflict() -> AppError:
    return AppError(
        409,
        "idempotency-conflict",
        "Idempotency key reused",
        "This Idempotency-Key was used with a different request body.",
    )


async def _existing_run(
    session: AsyncSession, tenant_id: str, key: str, digest: str
) -> CreatedRun | None:
    existing = await repo.get_run_by_idempotency_key(session, tenant_id, key)
    if existing is None:
        return None
    if existing.request_hash != digest:
        raise _idempotency_conflict()
    return CreatedRun(existing, created=False)


async def create_run(
    session: AsyncSession,
    tenant_id: str,
    body: RunCreate,
    *,
    idempotency_key: str,
    required_text: str,
    prepare: Callable[[], Awaitable[dict[str, Any]]],
) -> CreatedRun:
    """Insert brief + run(queued) + the first `run.status` event, idempotent by key and body.

    The same key with the same body returns the original run; with a different body it is a 409.
    `prepare` runs only when a new run will be created (admission checks, season resolution) and
    returns the run's `config`, so a replayed request never gets a 429 or repeats a model call.
    """
    digest = request_hash(body)
    found = await _existing_run(session, tenant_id, idempotency_key, digest)
    if found is not None:
        return found
    product = await repo.get_product(session, tenant_id, body.product_id)
    if product is None:
        raise AppError(404, "not-found", "Product not found")
    config = await prepare()
    try:
        brief = await repo.create_brief(
            session,
            tenant_id,
            product_id=product.id,
            geography_code=body.geography_code.upper(),
            geography_detail=body.geography_detail,
            season=body.season.strip(),
            required_text=required_text,
            aspect_ratio=body.aspect_ratio,
        )
        run = await repo.create_run(
            session,
            tenant_id,
            brief_id=brief.id,
            origin="ui",
            status="queued",
            config=config,
            idempotency_key=idempotency_key,
            request_hash=digest,
        )
        event = RunStatusEvent(run_id=run.id, status="queued")
        await repo.append_run_event(session, run.id, event.type, event_payload(event))
        await session.commit()
    except IntegrityError:
        # A concurrent request with the same key won the race.
        await session.rollback()
        found = await _existing_run(session, tenant_id, idempotency_key, digest)
        if found is None:
            raise
        return found
    return CreatedRun(run, created=True)


def _check_view(c: m.EvaluationCheck) -> CheckView:
    return CheckView(
        dimension=c.dimension,
        check_name=c.check_name,
        method=c.method,
        value=c.value,
        threshold=c.threshold,
        passed=c.passed,
        evidence=c.evidence,
        evidence_data=c.evidence_data,
    )


def events_url(run_id: uuid.UUID) -> str:
    return f"/v1/runs/{run_id}/events"


async def run_detail(session: AsyncSession, tenant_id: str, run_id: uuid.UUID) -> RunDetail:
    run = await repo.get_run(session, tenant_id, run_id)
    brief = await repo.get_brief(session, tenant_id, run.brief_id) if run else None
    if run is None or brief is None:
        raise AppError(404, "not-found", "Run not found")
    candidates = list(await repo.list_candidates(session, run.id))
    images = await repo.images_by_id(
        session, tenant_id, [c.image_id for c in candidates if c.image_id is not None]
    )
    evals = await repo.evaluations_for_candidates(session, [c.id for c in candidates])
    views: list[CandidateView] = []
    for c in candidates:
        img = images.get(c.image_id) if c.image_id else None
        ev = evals.get(c.id)
        evaluation = None
        if ev is not None:
            row, checks = ev
            evaluation = EvaluationView(
                id=row.id,
                evaluator_version=row.evaluator_version,
                verdict=row.verdict,
                overall_pass=row.overall_pass,
                dimensions={
                    "technical": row.technical_pass,
                    "text": row.text_pass,
                    "product": row.product_pass,
                    "context": row.context_pass,
                    # ADR-007: only evaluations that judged composition (ev-0.6+) report it.
                    **(
                        {"composition": row.composition_pass}
                        if row.composition_pass is not None
                        or any(ch.dimension == "composition" for ch in checks)
                        else {}
                    ),
                },
                checks=[_check_view(ch) for ch in checks],
                latency_ms=row.latency_ms,
            )
        views.append(
            CandidateView(
                id=c.id,
                kind=c.kind,
                attempt=c.attempt,
                slot=c.slot,
                status=c.status,
                parent_candidate_id=c.parent_candidate_id,
                image_id=c.image_id,
                image_url=image_url(c.image_id) if c.image_id else None,
                width=img.width if img else None,
                height=img.height if img else None,
                requested_model=c.requested_model,
                served_model=c.served_model,
                prompt_version=c.prompt_version,
                repair_instruction=c.repair_instruction,
                created_at=c.created_at,
                evaluation=evaluation,
            )
        )
    cost = float(run.cost_usd)
    return RunDetail(
        run=RunView(
            id=run.id,
            origin=run.origin,
            status=run.status,
            outcome=run.outcome,
            first_attempt_pass=run.first_attempt_pass,
            repair_count=run.repair_count,
            cost_usd=cost,
            latency_ms=run.latency_ms,
            error=run.error,
            config=run.config,
            created_at=run.created_at,
            finished_at=run.finished_at,
        ),
        brief=BriefView(
            id=brief.id,
            product_id=brief.product_id,
            geography_code=brief.geography_code,
            geography_detail=brief.geography_detail,
            season=brief.season,
            required_text=brief.required_text,
            aspect_ratio=brief.aspect_ratio,
            golden_key=brief.golden_key,
        ),
        product_id=brief.product_id,
        spec=run.spec,
        spec_version=run.spec_version,
        candidates=views,
        approved_candidate_id=run.approved_candidate_id,
        best_candidate_id=run.best_candidate_id,
        cost_usd=cost,
        latency_ms=run.latency_ms,
        events_url=events_url(run.id),
    )
