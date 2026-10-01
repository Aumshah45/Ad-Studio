"""`golden import`: the golden files -> the DB, for the evaluator dashboard (ADR-004).

Idempotent upserts: output images (source `golden` unless a golden run already stored them),
human labels from `labels.csv` (labeller `human:golden`), planted images regenerated from their
params (source `planted`) with their `planted_failures` rows and construction labels (labeller
`planted`), the latest eval report's per-item evaluations (run and candidate null) and the report
itself (`eval_reports`, skipped when that report file is already registered), and the golden
runs of `outputs/manifest.jsonl` (`runs_import`: linked when this DB has them, else created with
their candidates, lineage, evaluations and events).
"""

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db import models_adstudio as m
from backend.db import repositories as repo
from backend.domain.adstudio.evalreport import EvalReportData
from backend.domain.adstudio.products import create_product_from_upload
from backend.golden.dataset import (
    GoldenPaths,
    load_golden,
    read_labels,
    read_outputs,
    read_planted,
)
from backend.golden.harness import image_size, materialize, reference_images
from backend.golden.plant import GENERATOR_VERSION
from backend.golden.runs_import import import_runs
from backend.storage.blobs import BlobStore, sha256_hex

HUMAN_LABELLER = "human:golden"


@dataclass
class ImportResult:
    images: int = 0
    labels: int = 0
    planted: int = 0
    evaluations: int = 0
    runs_created: int = 0
    runs_linked: int = 0
    report: str = ""


async def _image(
    session: AsyncSession, blobs: BlobStore, tenant: str, data: bytes, source: str
) -> m.Image:
    sha = sha256_hex(data)
    blobs.put(data)  # idempotent; also restores bytes a fresh blob store lacks
    existing = await repo.get_image_by_sha(session, tenant, sha)
    if existing is not None:
        return existing
    size = image_size(data) or (1, 1)
    mime = "image/jpeg" if data[:3] == b"\xff\xd8\xff" else "image/png"
    image, _ = await repo.get_or_create_image(
        session,
        tenant,
        sha256=sha,
        source=source,
        mime=mime,
        width=size[0],
        height=size[1],
        size=len(data),
    )
    return image


async def _label(
    session: AsyncSession,
    image_id: uuid.UUID,
    labeller: str,
    dims: dict[str, bool | None],
    *,
    rubric: str,
    notes: str | None,
) -> None:
    values = list(dims.values())
    overall = False if any(v is False for v in values) else (True if all(values) else None)
    await repo.upsert_label(
        session,
        image_id=image_id,
        labeller=labeller,
        text_ok=dims.get("text"),
        product_ok=dims.get("product"),
        context_ok=dims.get("context"),
        composition_ok=dims.get("composition"),
        overall_ok=overall,
        rubric_version=rubric,
        notes=notes,
    )


def latest_report(reports_dir: Path) -> tuple[Path, EvalReportData] | None:
    path = reports_dir / "latest.json"
    if not path.exists():
        return None
    return path, EvalReportData.model_validate_json(path.read_text(encoding="utf-8"))


async def register_report(
    session: AsyncSession, data: EvalReportData, report_path: str
) -> m.EvalReport:
    """One `eval_reports` row per report file (the dashboard serves the newest)."""
    existing = await session.scalar(
        select(m.EvalReport).where(m.EvalReport.report_path == report_path)
    )
    if existing is not None:
        return existing
    row = m.EvalReport(
        evaluator_version=data.evaluator_version,
        dataset_version=data.dataset_version,
        git_sha=data.git_sha,
        metrics=json.loads(data.model_dump_json()),
        report_path=report_path,
    )
    session.add(row)
    await session.flush()
    return row


async def golden_import(
    sessionmaker: async_sessionmaker[AsyncSession],
    blobs: BlobStore,
    paths: GoldenPaths,
    *,
    tenant: str,
    reports_dir: Path | None,
    max_upload_bytes: int,
) -> ImportResult:
    golden = load_golden(paths)
    refs = await reference_images(paths, golden)
    result = ImportResult()
    by_sha: dict[str, uuid.UUID] = {}
    products: dict[str, uuid.UUID] = {}
    found = latest_report(reports_dir) if reports_dir is not None else None
    async with sessionmaker() as session:
        for pid, product in golden.products.items():
            product_result = await create_product_from_upload(
                session,
                blobs,
                tenant,
                name=f"{pid} {product.name}"[:120],
                data=paths.product_file(golden, pid).read_bytes(),
                max_bytes=max_upload_bytes,
            )
            if not blobs.exists(product_result.image.sha256):
                blobs.put(product_result.data)
            products[pid] = product_result.product.id
        outputs = read_outputs(paths)
        for item in outputs:
            data = (paths.outputs / item.file).read_bytes()
            image = await _image(session, blobs, tenant, data, "golden")
            by_sha[item.image_sha] = image.id
            result.images += 1
        report_checks = (
            {i.image_sha: (found[1].evaluator_version, i.checks) for i in found[1].items}
            if found is not None
            else {}
        )
        runs = await import_runs(
            session,
            tenant,
            golden,
            outputs,
            products=products,
            images=by_sha,
            report_checks=report_checks,
        )
        result.runs_created, result.runs_linked = runs.created, runs.linked
        for row in read_labels(paths.labels).values():
            image_id = by_sha.get(row.image_sha)
            if image_id is None:
                continue
            await _label(
                session,
                image_id,
                HUMAN_LABELLER,
                {d: v for d, v in row.dims().items() if d != "technical"},
                rubric=row.rubric_version,
                notes=row.notes or None,
            )
            result.labels += 1
        for planted in read_planted(paths):
            data = materialize(planted, paths, refs)
            image = await _image(session, blobs, tenant, data, "planted")
            by_sha[image.sha256] = image.id
            source_id = by_sha.get(planted.source_sha)
            existing = await session.scalar(
                select(m.PlantedFailure).where(m.PlantedFailure.image_id == image.id)
            )
            fields: dict[str, Any] = {
                "source_image_id": source_id,
                "mutation": planted.mutation,
                "params": {
                    **planted.params,
                    "item_id": planted.id,
                    "seed": planted.seed,
                    "source_item": planted.source_id,
                    "brief_id": planted.brief_id,
                },
                "fails_dimensions": planted.fails_dimensions,
                "generator_version": planted.generator_version or GENERATOR_VERSION,
            }
            if existing is None:
                session.add(m.PlantedFailure(image_id=image.id, **fields))
            else:
                for name, value in fields.items():
                    setattr(existing, name, value)
            await _label(
                session,
                image.id,
                "planted",
                {d: planted.labels.get(d) for d in ("text", "product", "context", "composition")},
                rubric="planted-1",
                notes=f"{planted.mutation} (planted, labels by construction)",
            )
            result.planted += 1
        if found is not None:
            path, report = found
            for item in report.items:
                image_id = by_sha.get(item.image_sha)
                if image_id is None:
                    continue
                await repo.upsert_evaluation(
                    session,
                    image_id=image_id,
                    run_id=None,
                    candidate_id=None,
                    evaluator_version=report.evaluator_version,
                    checks=[
                        {
                            "dimension": c.dimension,
                            "check_name": c.name,
                            "method": c.method,
                            "value": c.value,
                            "threshold": c.threshold,
                            "passed": c.passed,
                            "evidence": c.evidence or None,
                            "evidence_data": None,
                        }
                        for c in item.checks
                    ],
                    text_pass=item.dimensions.get("text"),
                    product_pass=item.dimensions.get("product"),
                    context_pass=item.dimensions.get("context"),
                    composition_pass=item.dimensions.get("composition"),
                    technical_pass=item.dimensions.get("technical"),
                    overall_pass=item.verdict == "pass",
                    verdict=item.verdict,
                )
                result.evaluations += 1
            row = await register_report(session, report, str(path.with_name(report.report_file)))
            result.report = str(row.id)
        await session.commit()
    return result
