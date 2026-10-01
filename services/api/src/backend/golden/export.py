"""`golden export`: the golden runs in the DB -> files under `data/golden/` (ADR-004).

Per brief (its latest finished golden run for this pipeline version and image client):
- **E-nat** = the first candidate-model candidate (attempt 0, lowest slot with an image), raw,
  before any repair. **E-final** = the run's terminal output (the gate's approved candidate, else
  the best candidate shown for review).
- `outputs/<brief>-<set>.png` (the stored blob bytes) and `outputs/manifest.jsonl` with the image
  sha, brief, set, spec, product facts, model, prompt version, candidate kind/attempt, cost and
  latency (the generating call's and the run's).
- `labels.csv`: human labels from the DB (the labelling UI) are merged in; rows already in the
  file are never overwritten.
- `cache/verdicts.json`: the OCR and judge answers the offline evaluator needs for these images,
  taken from the DB cache where the run already paid for them (a live call only for answers it
  does not hold, unless `allow_live=False`).
"""

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from backend.db import models_adstudio as m
from backend.db import repositories as repo
from backend.db.models_calls import ModelCall
from backend.domain.adstudio.pipeline import PIPELINE_VERSION
from backend.golden.context import GoldenContext
from backend.golden.dataset import (
    GoldenBrief,
    GoldenPaths,
    ImageSet,
    LabelRow,
    OutputItem,
    RunInfo,
    golden_key,
    load_golden,
    read_labels,
    write_jsonl,
    write_labels,
)
from backend.golden.harness import (
    StaleSnapshotError,
    build_cases,
    evaluate_cases,
    record_engine,
    write_verdicts,
)
from backend.golden.runner import DONE_STATUSES

CheckRows = list[m.EvaluationCheck]


@dataclass
class ExportResult:
    items: list[OutputItem] = field(default_factory=list[OutputItem])
    missing: list[str] = field(default_factory=list[str])
    labels_added: int = 0
    verdict_entries: int = 0
    verdict_note: str = ""


async def _terminal_run(ctx: GoldenContext, key: str) -> tuple[m.Brief, m.Run] | None:
    async with ctx.deps.sessionmaker() as session:
        brief = await session.scalar(select(m.Brief).where(m.Brief.golden_key == key))
        if brief is None:
            return None
        run = await session.scalar(
            select(m.Run)
            .where(m.Run.brief_id == brief.id, m.Run.status.in_(DONE_STATUSES))
            .order_by(m.Run.created_at.desc(), m.Run.id.desc())
            .limit(1)
        )
    return (brief, run) if run is not None else None


async def _image_call(ctx: GoldenContext, sha: str) -> tuple[float | None, int | None]:
    """Cost and latency of the (first, uncached) image call that produced this image."""
    async with ctx.deps.sessionmaker() as session:
        row = await session.scalar(
            select(ModelCall)
            .where(
                ModelCall.kind == "image",
                ModelCall.cached.is_(False),
                ModelCall.meta["sha256"].astext == sha,
            )
            .order_by(ModelCall.created_at)
            .limit(1)
        )
    return (float(row.est_cost_usd), int(row.latency_ms)) if row is not None else (None, None)


def _dims(ev: m.Evaluation, checks: CheckRows) -> dict[str, bool | None]:
    judged = ev.composition_pass is not None or any(c.dimension == "composition" for c in checks)
    return {
        "technical": ev.technical_pass,
        "text": ev.text_pass,
        "product": ev.product_pass,
        "context": ev.context_pass,
        **({"composition": ev.composition_pass} if judged else {}),
    }


def _product_box(checks: CheckRows) -> list[int] | None:
    for c in checks:
        data: Any = c.evidence_data
        if c.check_name == "color_delta_e" and isinstance(data, dict):
            box: Any = data.get("ad_box")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            if isinstance(box, list) and len(box) == 4:  # pyright: ignore[reportUnknownArgumentType]
                return [int(v) for v in box]  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    return None


async def _item(
    ctx: GoldenContext,
    paths: GoldenPaths,
    brief: GoldenBrief,
    key: str,
    run: m.Run,
    product: m.Product,
    reference: m.Image,
    candidate: m.Candidate,
    evaluation: tuple[m.Evaluation, CheckRows] | None,
    image_set: ImageSet,
) -> OutputItem:
    async with ctx.deps.sessionmaker() as session:
        image = (
            await repo.get_image(session, run.tenant_id, candidate.image_id)
            if candidate.image_id
            else None
        )
    if image is None:
        raise LookupError(f"{brief.id}: candidate {candidate.id} has no image")
    data = ctx.deps.blobs.read(image.sha256)
    name = f"{brief.id}-{image_set}.png"
    paths.outputs.mkdir(parents=True, exist_ok=True)
    (paths.outputs / name).write_bytes(data)
    cost, latency = (None, None)
    if candidate.kind != "overlay":
        cost, latency = await _image_call(ctx, image.sha256)
    ev_row, checks = evaluation if evaluation else (None, [])
    verification = None
    if candidate.kind == "overlay":
        verification = (
            "construction" if any(c.check_name == "overlay_construction" for c in checks) else "ocr"
        )
    return OutputItem(
        id=f"{brief.id}-{image_set}",
        set=image_set,
        brief_id=brief.id,
        product_id=brief.product,
        golden_key=key,
        file=name,
        image_sha=image.sha256,
        width=image.width,
        height=image.height,
        mime=image.mime,
        reference_sha=reference.sha256,
        spec=run.spec or {},
        facts=product.reference_facts,
        candidate_kind=candidate.kind,
        attempt=candidate.attempt,
        slot=candidate.slot,
        candidate_status=candidate.status,
        requested_model=candidate.requested_model,
        model=candidate.served_model,
        prompt_version=candidate.prompt_version,
        image_client=str(run.config.get("image_client", "")),
        pipeline_version=str(run.config.get("pipeline_version", "")),
        evaluator_version=ev_row.evaluator_version if ev_row else None,
        pipeline_verdict=ev_row.verdict if ev_row else None,
        pipeline_dimensions=_dims(ev_row, checks) if ev_row else {},
        text_verification=verification,
        product_box=_product_box(checks),
        call_cost_usd=cost,
        call_latency_ms=latency,
        run=RunInfo(
            run_id=str(run.id),
            status=run.status,
            outcome=run.outcome,
            reason=_finish_reason(run),
            first_attempt_pass=run.first_attempt_pass,
            repair_count=run.repair_count,
            cost_usd=float(run.cost_usd),
            latency_ms=run.latency_ms,
        ),
        exclude_native_text=brief.exclude_native_text,
    )


def _finish_reason(run: m.Run) -> str | None:
    error: Any = run.error
    if isinstance(error, dict):
        return str(error.get("type"))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    return None


async def _db_labels(ctx: GoldenContext, items: list[OutputItem]) -> list[LabelRow]:
    """Human labels from the labelling UI, for the exported images."""
    by_sha = {i.image_sha: i for i in items}
    rows: list[LabelRow] = []
    async with ctx.deps.sessionmaker() as session:
        result = await session.execute(
            select(m.Label, m.Image.sha256)
            .join(m.Image, m.Image.id == m.Label.image_id)
            .where(m.Label.labeller.like("human:%"), m.Image.sha256.in_(list(by_sha)))
        )
        for label, sha in result.all():
            if label.text_ok is None or label.product_ok is None or label.context_ok is None:
                continue
            item = by_sha[sha]
            rows.append(
                LabelRow(
                    image_sha=sha,
                    set=item.set,
                    brief_id=item.brief_id,
                    text=label.text_ok,
                    product=label.product_ok,
                    context=label.context_ok,
                    composition=label.composition_ok,
                    notes=label.notes or "",
                    rubric_version=label.rubric_version,
                )
            )
    return rows


async def golden_export(
    ctx: GoldenContext,
    paths: GoldenPaths,
    *,
    record_verdicts: bool = True,
    allow_live: bool = True,
    only: set[str] | None = None,
) -> ExportResult:
    golden = load_golden(paths)
    client = ctx.deps.image_client.name
    result = ExportResult()
    for brief in golden.briefs:
        if only is not None and brief.id not in only:
            continue
        key = golden_key(brief.id, PIPELINE_VERSION, client)
        found = await _terminal_run(ctx, key)
        if found is None:
            result.missing.append(brief.id)
            continue
        brief_row, run = found
        async with ctx.deps.sessionmaker() as session:
            product = await repo.get_product(session, run.tenant_id, brief_row.product_id)
            reference = (
                await repo.get_image(session, run.tenant_id, product.image_id) if product else None
            )
            candidates = list(await repo.list_candidates(session, run.id))
            evals = await repo.evaluations_for_candidates(session, [c.id for c in candidates])
        if product is None or reference is None:
            result.missing.append(brief.id)
            continue
        firsts = sorted(
            (c for c in candidates if c.attempt == 0 and c.image_id is not None),
            key=lambda c: c.slot,
        )
        by_id: dict[uuid.UUID, m.Candidate] = {c.id: c for c in candidates}
        final_id = run.approved_candidate_id or run.best_candidate_id
        final = by_id.get(final_id) if final_id else None
        if not firsts or final is None or final.image_id is None:
            result.missing.append(brief.id)
            continue
        for image_set, cand in (("nat", firsts[0]), ("final", final)):
            result.items.append(
                await _item(
                    ctx,
                    paths,
                    brief,
                    key,
                    run,
                    product,
                    reference,
                    cand,
                    evals.get(cand.id),
                    image_set,  # type: ignore[arg-type]
                )
            )
    write_jsonl(paths.output_manifest, result.items)
    keep = {i.file for i in result.items}
    for stale in paths.outputs.glob("*.png"):
        if stale.name not in keep:
            stale.unlink()

    existing = read_labels(paths.labels)
    added = [r for r in await _db_labels(ctx, result.items) if (r.image_sha, r.set) not in existing]
    if added or not paths.labels.exists():
        write_labels(paths.labels, [*existing.values(), *added])
    result.labels_added = len(added)

    if record_verdicts and result.items:
        batch = await build_cases(paths, include_planted=False)
        engine = record_engine(
            ctx.settings,
            base=paths.verdicts,
            sessionmaker=ctx.deps.sessionmaker,
            allow_live=allow_live,
        )
        try:
            await evaluate_cases(engine, batch.cases, concurrency=2)
        except StaleSnapshotError as exc:
            result.verdict_note = f"{len(exc.misses)} answers not cached; run `make eval-record`"
        result.verdict_entries = write_verdicts(paths.verdicts, engine, keep_existing=True)
    return result
