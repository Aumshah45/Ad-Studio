"""Evaluator dashboard API (architecture API contract): the latest golden eval report.

Both endpoints read the newest `eval_reports` row (written by `make eval` when the DB is
reachable, or by `make golden-import` from `evals/reports/latest.json`). Before the first report
they return 404 `no-eval-report`. Items are the report's labelled items joined to the images table
by sha256 for their URLs (null when the images were not imported), to `data/golden/briefs.yaml`
for the brief fields, and to golden runs' candidates for `run_id`.
"""

import base64
import binascii
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.errors import AppError
from backend.db import models_adstudio as m
from backend.db.session import get_session
from backend.domain.adstudio.evalreport import (
    EvalItem,
    EvalReportData,
    EvalSummary,
    ItemRecord,
    Outcome,
    summary_of,
    summary_warnings,
)
from backend.domain.adstudio.evaluator.core import EVALUATOR_VERSION
from backend.domain.adstudio.schemas import Page, image_url
from backend.golden.dataset import (
    GOLDEN_DIR,
    GoldenBrief,
    GoldenPaths,
    GoldenSet,
    dataset_version,
    load_golden,
)
from backend.http.deps import get_tenant

router = APIRouter(prefix="/v1/evals", tags=["evals"])
PROBLEM: dict[str, Any] = {"content": {"application/problem+json": {}}}
Dimension = Literal["technical", "text", "product", "context", "composition"]


def _no_report() -> AppError:
    return AppError(
        404,
        "no-eval-report",
        "No eval report yet",
        "Run `make eval` (or `make golden-import`) to create the first evaluator report.",
    )


GoldenVersion = Annotated[
    str | None,
    Query(
        pattern=r"^v[0-9]{1,3}$",
        description="Golden dataset version (v1 | v2); default: the newest report of any version",
    ),
]


async def _latest(
    session: AsyncSession, evaluator_version: str | None, golden_version: str | None = None
) -> tuple[m.EvalReport, EvalReportData]:
    stmt = select(m.EvalReport).order_by(m.EvalReport.created_at.desc(), m.EvalReport.id.desc())
    if evaluator_version:
        stmt = stmt.where(m.EvalReport.evaluator_version == evaluator_version)
    if golden_version:
        stmt = stmt.where(m.EvalReport.metrics["golden_version"].astext == golden_version)
    row = await session.scalar(stmt.limit(1))
    if row is None:
        raise _no_report()
    return row, EvalReportData.model_validate(row.metrics)


@router.get("/summary", responses={404: PROBLEM})
async def eval_summary(
    session: Annotated[AsyncSession, Depends(get_session)],
    evaluator_version: Annotated[str | None, Query(max_length=32)] = None,
    golden_version: GoldenVersion = None,
) -> EvalSummary:
    """The latest evaluator report: per-dimension precision/recall/F1, human agreement, pipeline
    pass rates, text exactness, $/ad, latency and every PRD criterion with its target."""
    row, data = await _latest(session, evaluator_version, golden_version)
    warnings = summary_warnings(
        data,
        current_evaluator_version=EVALUATOR_VERSION,
        current_dataset_version=_current_dataset_version(data.provenance.get("snapshot")),
    )
    return summary_of(row.id, data, warnings)


def _current_dataset_version(snapshot: object) -> str | None:
    """The dataset hash of the golden dir this report was built from, when it is on this machine
    (the snapshot lives at `<golden out>/cache/verdicts.json`); None when it is not."""
    if not isinstance(snapshot, str) or not snapshot:
        return None
    out = Path(snapshot).parent.parent
    paths = GoldenPaths(source=GOLDEN_DIR, out=out)
    if not paths.output_manifest.exists() or not paths.briefs.exists():
        return None
    return dataset_version(paths)


@lru_cache(maxsize=4)
def _golden_at(path: Path, mtime_ns: int) -> GoldenSet | None:
    del mtime_ns  # cache key only: an edited briefs.yaml is re-read
    try:
        return load_golden(GoldenPaths(source=path.parent, out=path.parent))
    except (OSError, ValueError, KeyError):
        return None


def golden_briefs() -> GoldenSet | None:
    path = GoldenPaths().briefs
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return None
    return _golden_at(path, mtime)


def _encode(offset: int) -> str:
    return base64.urlsafe_b64encode(f"o:{offset}".encode()).decode()


def _decode(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        kind, _, value = raw.partition(":")
        offset = int(value)
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise AppError(422, "validation-error", "Invalid cursor") from exc
    if kind != "o" or offset < 0:
        raise AppError(422, "validation-error", "Invalid cursor")
    return offset


def _keep(
    item: ItemRecord,
    origin: str | None,
    dimension: Dimension | None,
    outcome: Outcome | None,
) -> bool:
    if origin == "natural" and item.set == "plant":
        return False
    if origin == "planted" and item.set != "plant":
        return False
    if dimension is not None and outcome is not None:
        return item.outcomes.get(dimension) == outcome
    if dimension is not None:
        return item.outcomes.get(dimension) is not None
    if outcome is not None:
        return outcome in item.outcomes.values()
    return True


def _brief(golden: GoldenSet | None, brief_id: str) -> GoldenBrief | None:
    if golden is None:
        return None
    try:
        return golden.brief(brief_id)
    except KeyError:
        return None


async def _golden_runs(
    session: AsyncSession, tenant_id: str, image_ids: list[uuid.UUID]
) -> dict[uuid.UUID, uuid.UUID]:
    """image id -> the newest golden run with a candidate showing that image."""
    if not image_ids:
        return {}
    result = await session.execute(
        select(m.Candidate.image_id, m.Candidate.run_id)
        .join(m.Run, m.Run.id == m.Candidate.run_id)
        .where(
            m.Run.tenant_id == tenant_id,
            m.Run.origin == "golden",
            m.Candidate.image_id.in_(image_ids),
        )
        .order_by(m.Run.created_at.desc(), m.Run.id.desc())
    )
    out: dict[uuid.UUID, uuid.UUID] = {}
    for image_id, run_id in result.all():
        if image_id is not None:
            out.setdefault(image_id, run_id)
    return out


@router.get("/items", responses={404: PROBLEM, 422: PROBLEM})
async def eval_items(
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[str, Depends(get_tenant)],
    origin: Annotated[Literal["natural", "planted"] | None, Query()] = None,
    dimension: Annotated[Dimension | None, Query()] = None,
    outcome: Annotated[Outcome | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(description="`next_cursor` of the previous page")] = None,
    golden_version: GoldenVersion = None,
) -> Page[EvalItem]:
    """Labelled items of the latest report with their verdicts (planted-failure gallery and the
    TP/FP/FN/TN drill-down; `outcome` needs the positive class FAIL)."""
    _, data = await _latest(session, None, golden_version)
    chosen = [i for i in data.items if _keep(i, origin, dimension, outcome)]
    start = _decode(cursor)
    page = chosen[start : start + limit]
    shas = {i.image_sha for i in page} | {i.source_sha for i in page if i.source_sha}
    rows = (
        await session.scalars(
            select(m.Image).where(m.Image.tenant_id == tenant_id, m.Image.sha256.in_(shas))
        )
    ).all()
    ids = {row.sha256: row.id for row in rows}
    runs = await _golden_runs(
        session,
        tenant_id,
        [ids[i.image_sha] for i in page if i.image_sha in ids and i.set != "plant"],
    )
    golden = golden_briefs()
    items: list[EvalItem] = []
    for i in page:
        image_id = ids.get(i.image_sha)
        source_id = ids.get(i.source_sha) if i.source_sha else None
        brief = _brief(golden, i.brief_id)
        product = golden.products.get(i.product_id) if golden else None
        items.append(
            EvalItem(
                id=i.id,
                origin="planted" if i.set == "plant" else "natural",
                set=i.set,
                brief_id=i.brief_id,
                product_id=i.product_id,
                product_name=product.name if product else None,
                geography_code=brief.geography.upper() if brief else None,
                season=brief.season if brief else None,
                required_text=brief.required_text if brief else None,
                aspect_ratio=brief.aspect_ratio if brief else None,
                tags=list(brief.tags) if brief else [],
                run_id=runs.get(image_id) if image_id and i.set != "plant" else None,
                split=i.split,
                image_id=image_id,
                image_url=image_url(image_id) if image_id else None,
                source_image_url=image_url(source_id) if source_id else None,
                mutation=i.mutation,
                label_source=i.label_source,
                label=i.labels,
                verdict=i.verdict,
                dimensions=i.dimensions,
                outcomes=i.outcomes,
                evidence=i.evidence,
            )
        )
    more = start + limit < len(chosen)
    return Page[EvalItem](items=items, next_cursor=_encode(start + limit) if more else None)
