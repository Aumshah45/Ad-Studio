"""`golden import`, runs part: the exported manifest -> `runs` (origin=golden) with lineage.

The manifest keeps, per brief, the run's id and summary plus two candidates (E-nat, the first raw
candidate, and E-final, the run's output). For each run in the manifest:

- **linked** when this database already has it: the same run id, or a golden run that owns a
  candidate with the E-final image (a live `golden run` in this DB). Nothing is changed.
- **created** otherwise (a fresh DB, a teammate's export, the key-less dry run after the tests
  truncated the test DB): the brief (by `golden_key`), the run under its original id, the E-nat
  and E-final candidates (E-final's parent is E-nat when they differ, so the lineage reads
  first attempt -> output; intermediate repairs are not in the manifest), the pipeline's own
  evaluation per candidate (dimensions and verdict from the manifest; the checks from the latest
  eval report when its evaluator version matches), and replayable events ending in
  `run.finished`, so `/v1/runs?origin=golden`, `/v1/runs/{id}` and its SSE replay work.

Idempotent: a second import links every run it created the first time.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db import models_adstudio as m
from backend.db import repositories as repo
from backend.domain.adstudio.evalreport import CheckRecord
from backend.domain.adstudio.events import (
    CandidateCreatedEvent,
    DimensionView,
    EvaluationDoneEvent,
    RunFinishedEvent,
    RunStatusEvent,
    event_payload,
)
from backend.domain.adstudio.runs import validate_required_text
from backend.domain.adstudio.schemas import image_url
from backend.golden.dataset import GoldenSet, OutputItem

IMPORT_TAG = "golden-import"
_DIMS = ("technical", "text", "product", "context", "composition")


@dataclass
class RunImportResult:
    created: int = 0
    linked: int = 0


def group_runs(items: list[OutputItem]) -> dict[str, tuple[OutputItem, OutputItem]]:
    """run id -> (E-nat, E-final); runs missing either item are skipped."""
    by_run: dict[str, dict[str, OutputItem]] = {}
    for item in items:
        by_run.setdefault(item.run.run_id, {})[item.set] = item
    return {
        rid: (sets["nat"], sets["final"])
        for rid, sets in by_run.items()
        if "nat" in sets and "final" in sets
    }


async def _linked_run(
    session: AsyncSession, tenant: str, run_id: uuid.UUID, final_image: uuid.UUID
) -> m.Run | None:
    run = await session.get(m.Run, run_id)
    if run is not None:
        return run
    return await session.scalar(
        select(m.Run)
        .join(m.Candidate, m.Candidate.run_id == m.Run.id)
        .where(
            m.Run.tenant_id == tenant,
            m.Run.origin == "golden",
            m.Candidate.image_id == final_image,
        )
        .order_by(m.Run.created_at.desc())
        .limit(1)
    )


async def _brief(
    session: AsyncSession,
    tenant: str,
    golden: GoldenSet,
    item: OutputItem,
    product_id: uuid.UUID,
) -> m.Brief:
    existing = await session.scalar(select(m.Brief).where(m.Brief.golden_key == item.golden_key))
    if existing is not None:
        return existing
    brief = golden.brief(item.brief_id)
    return await repo.create_brief(
        session,
        tenant,
        product_id=product_id,
        geography_code=brief.geography.upper(),
        geography_detail=None,
        season=brief.season.strip(),
        required_text=validate_required_text(brief.required_text),
        aspect_ratio=brief.aspect_ratio,
        golden_key=item.golden_key,
    )


def _checks(
    item: OutputItem, report_checks: dict[str, tuple[str, list[CheckRecord]]]
) -> list[dict[str, Any]]:
    found = report_checks.get(item.image_sha)
    if found is None or found[0] != item.evaluator_version:
        return []
    rows: list[dict[str, Any]] = []
    for c in found[1]:
        data: dict[str, Any] | None = None
        if c.name == "color_delta_e" and item.product_box:
            data = {"ad_box": item.product_box}
        rows.append(
            {
                "dimension": c.dimension,
                "check_name": c.name,
                "method": c.method,
                "value": c.value,
                "threshold": c.threshold,
                "passed": c.passed,
                "evidence": c.evidence or None,
                "evidence_data": data,
            }
        )
    return rows


async def _candidate(
    session: AsyncSession,
    run: m.Run,
    item: OutputItem,
    image_id: uuid.UUID,
    parent: m.Candidate | None,
    report_checks: dict[str, tuple[str, list[CheckRecord]]],
) -> m.Candidate:
    cand = await repo.create_candidate(
        session,
        run.id,
        image_id=image_id,
        parent_candidate_id=parent.id if parent else None,
        kind=item.candidate_kind,
        attempt=item.attempt,
        slot=item.slot,
        requested_model=item.requested_model,
        served_model=item.model,
        prompt_version=item.prompt_version,
        status=item.candidate_status,
    )
    event = CandidateCreatedEvent(
        run_id=run.id,
        candidate_id=cand.id,
        attempt=cand.attempt,
        slot=cand.slot,
        kind=item.candidate_kind,  # type: ignore[arg-type]
        parent_candidate_id=cand.parent_candidate_id,
        image_id=image_id,
        image_url=image_url(image_id),
        width=item.width,
        height=item.height,
        model=item.model or item.requested_model or "unknown",
        cached=True,
    )
    await repo.append_run_event(session, run.id, event.type, event_payload(event))
    if item.evaluator_version and item.pipeline_dimensions:
        # Only the dimensions the pipeline's evaluator had (a pre-ev-0.6 run has no composition).
        dims = {d: item.pipeline_dimensions[d] for d in _DIMS if d in item.pipeline_dimensions}
        verdict = item.pipeline_verdict or "unverified"
        evaluation = await repo.upsert_evaluation(
            session,
            image_id=image_id,
            run_id=run.id,
            candidate_id=cand.id,
            evaluator_version=item.evaluator_version,
            checks=_checks(item, report_checks),
            text_pass=dims.get("text"),
            product_pass=dims.get("product"),
            context_pass=dims.get("context"),
            composition_pass=dims.get("composition"),
            technical_pass=dims.get("technical"),
            overall_pass=verdict == "pass",
            verdict=verdict,
        )
        done = EvaluationDoneEvent(
            run_id=run.id,
            candidate_id=cand.id,
            evaluation_id=evaluation.id,
            verdict=verdict,  # type: ignore[arg-type]
            dimensions={d: DimensionView(passed=v) for d, v in dims.items()},  # type: ignore[misc]
        )
        await repo.append_run_event(session, run.id, done.type, event_payload(done))
    return cand


async def _create_run(
    session: AsyncSession,
    tenant: str,
    golden: GoldenSet,
    run_id: uuid.UUID,
    nat: OutputItem,
    final: OutputItem,
    product_id: uuid.UUID,
    images: dict[str, uuid.UUID],
    report_checks: dict[str, tuple[str, list[CheckRecord]]],
) -> None:
    info = final.run
    brief = await _brief(session, tenant, golden, final, product_id)
    now = datetime.now(UTC)
    run = await repo.create_run(
        session,
        tenant,
        id=run_id,
        brief_id=brief.id,
        origin="golden",
        batch_label=f"golden:{final.pipeline_version}"[:64],
        status=info.status,
        outcome=info.outcome,
        config={
            "image_client": final.image_client,
            "pipeline_version": final.pipeline_version,
            "golden": {"brief_id": final.brief_id, "golden_key": final.golden_key},
            "imported": {"from": "outputs/manifest.jsonl", "at": now.isoformat()},
        },
        spec=final.spec or None,
        first_attempt_pass=info.first_attempt_pass,
        repair_count=info.repair_count,
        cost_usd=Decimal(str(info.cost_usd)),
        latency_ms=info.latency_ms,
        error={"type": info.reason} if info.reason else None,
        idempotency_key=f"{IMPORT_TAG}:{run_id}",
        finished_at=now,
    )
    status = RunStatusEvent(run_id=run.id, status="queued")
    await repo.append_run_event(session, run.id, status.type, event_payload(status))
    first = await _candidate(session, run, nat, images[nat.image_sha], None, report_checks)
    same = final.image_sha == nat.image_sha and (final.attempt, final.slot) == (
        nat.attempt,
        nat.slot,
    )
    last = (
        first
        if same
        else await _candidate(session, run, final, images[final.image_sha], first, report_checks)
    )
    approved = info.status in ("passed", "approved") and final.pipeline_verdict == "pass"
    run.approved_candidate_id = last.id if approved else None
    run.best_candidate_id = None if approved else last.id
    terminal = info.status if info.status in ("passed", "needs_review") else None
    terminal = terminal or ("passed" if approved else "needs_review")
    finished = RunFinishedEvent(
        run_id=run.id,
        status=terminal,  # type: ignore[arg-type]
        outcome=info.outcome,  # type: ignore[arg-type]
        approved_candidate_id=run.approved_candidate_id,
        best_candidate_id=run.best_candidate_id,
        cost_usd=info.cost_usd,
        latency_ms=info.latency_ms or 0,
        reason=info.reason,
    )
    await repo.append_run_event(session, run.id, finished.type, event_payload(finished))
    await session.flush()


async def import_runs(
    session: AsyncSession,
    tenant: str,
    golden: GoldenSet,
    items: list[OutputItem],
    *,
    products: dict[str, uuid.UUID],
    images: dict[str, uuid.UUID],
    report_checks: dict[str, tuple[str, list[CheckRecord]]],
) -> RunImportResult:
    """Create or link one golden run per manifest run (see the module docstring)."""
    result = RunImportResult()
    for rid, (nat, final) in group_runs(items).items():
        run_id = uuid.UUID(rid)
        if nat.image_sha not in images or final.image_sha not in images:
            continue
        if await _linked_run(session, tenant, run_id, images[final.image_sha]) is not None:
            result.linked += 1
            continue
        product_id = products.get(final.product_id)
        if product_id is None:
            continue
        await _create_run(
            session, tenant, golden, run_id, nat, final, product_id, images, report_checks
        )
        result.created += 1
    return result
