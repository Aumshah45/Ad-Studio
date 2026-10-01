"""Golden CLI (slice 14) on a tmp dry run: fake image + vision clients, the test DB, a tmp dataset
dir. The same `execute_run` as the API; no network."""

import json
from pathlib import Path

import pytest

from backend.golden.context import build_context
from backend.golden.dataset import GOLDEN_DIR, LABEL_COLUMNS, GoldenPaths, read_labels, read_outputs
from backend.golden.export import golden_export
from backend.golden.harness import image_size
from backend.golden.runner import golden_run
from backend.golden.sheet import render_sheet
from backend.llm.cache import FileCache
from backend.storage.blobs import sha256_hex
from tests.conftest import make_settings
from tests.golden_fixtures import tesseract_available

BRIEFS = {"B01", "B20"}  # a native brief and the overlay-only injection brief


@pytest.mark.skipif(not tesseract_available(), reason="the evaluator's text checks need tesseract")
async def test_golden_manifest_matches_files(tmp_path: Path) -> None:
    settings = make_settings(
        image_client="fake", vision_client="fake", blob_dir=str(tmp_path / "blobs")
    )
    paths = GoldenPaths(source=GOLDEN_DIR, out=tmp_path / "golden")
    ctx = build_context(settings)
    try:
        outcomes = await golden_run(ctx, paths, only=BRIEFS)
        again = await golden_run(ctx, paths, only=BRIEFS)
        forced = await golden_run(ctx, paths, only={"B01"}, force=True)
        result = await golden_export(ctx, paths, only=BRIEFS)
    finally:
        await ctx.close()

    assert {o.brief_id for o in outcomes} == BRIEFS
    assert all(o.status in ("passed", "needs_review") for o in outcomes)
    # Idempotent per golden_key: the second batch reuses the finished runs.
    assert {o.run_id for o in again} == {o.run_id for o in outcomes}
    assert all("idempotent" in o.note for o in again)
    # --force: a new run for a finished brief; export takes it (the latest), superseding the old.
    (b01_forced,) = forced
    b01_first = next(o for o in outcomes if o.brief_id == "B01")
    assert b01_forced.run_id != b01_first.run_id and "idempotent" not in b01_forced.note

    items = read_outputs(paths)
    assert result.items and not result.missing
    assert sorted((i.brief_id, i.set) for i in items) == sorted(
        (b, s) for b in BRIEFS for s in ("nat", "final")
    )
    for item in items:
        data = (paths.outputs / item.file).read_bytes()
        assert sha256_hex(data) == item.image_sha, item.id
        assert image_size(data) == (item.width, item.height)
        assert max(item.width, item.height) <= 1024
        assert item.golden_key.startswith(f"{item.brief_id}@") and item.golden_key.endswith("@fake")
        assert item.spec["required_text"]["raw"]
        assert item.model and item.prompt_version and item.run.latency_ms is not None
        if item.set == "nat":  # the first candidate, raw, before any repair
            assert item.attempt == 0 and item.candidate_kind == "initial"
    assert next(i for i in items if i.id == "B01-final").run.run_id == str(b01_forced.run_id)
    b20 = next(i for i in items if i.id == "B20-final")
    assert b20.exclude_native_text and b20.candidate_kind == "overlay"
    assert {p.name for p in paths.outputs.glob("*.png")} == {i.file for i in items}
    assert paths.labels.exists() and read_labels(paths.labels) == {}
    snapshot = FileCache(paths.verdicts)
    assert snapshot.entries and snapshot.meta["vision"]["client"] == "fake"

    html = render_sheet(paths, embed=False)
    assert html.count('class="card"') == len(items)
    for leak in ("ocr_cer", "color_delta_e", "needs_review", "pipeline_verdict", "overall_pass"):
        assert leak not in html  # labelled blind to the evaluator
    assert f"const COLUMNS = {json.dumps(list(LABEL_COLUMNS))};" in html

    try:
        await _check_import(settings, paths)
    finally:
        # Leave no golden runs whose blobs lived in tmp_path: `make golden-dryrun` shares this DB
        # and would otherwise skip these briefs as done and fail to export their images.
        await _wipe_golden_runs(settings, sorted({i.golden_key for i in items}))


async def _wipe_golden_runs(settings, keys: list[str]) -> None:  # type: ignore[no-untyped-def]
    """A fresh DB for these briefs: their runs (cascading candidates, events, evaluations) and
    briefs are gone; images, products and blobs stay."""
    from sqlalchemy import delete, select

    from backend.db import models_adstudio as m
    from backend.db.session import create_database

    db = create_database(settings.database_url)
    try:
        async with db.sessionmaker() as session:
            briefs = select(m.Brief.id).where(m.Brief.golden_key.in_(keys))
            await session.execute(delete(m.Run).where(m.Run.brief_id.in_(briefs)))
            await session.execute(delete(m.Brief).where(m.Brief.golden_key.in_(keys)))
            await session.commit()
    finally:
        await db.dispose()


async def _check_import(settings, paths: GoldenPaths) -> None:  # type: ignore[no-untyped-def]
    """`golden import` creates or links the manifest's runs so `/v1/runs?origin=golden` and
    `/v1/runs/{id}` work (slice 22/23 follow-up)."""
    from backend.db.session import create_database
    from backend.golden.importer import golden_import
    from backend.http.app import create_app
    from backend.storage.blobs import BlobStore
    from tests.conftest import client_for

    items = read_outputs(paths)
    run_ids = {i.run.run_id for i in items}
    assert len(run_ids) == len(BRIEFS)

    async def do_import():  # type: ignore[no-untyped-def]
        db = create_database(settings.database_url)
        try:
            return await golden_import(
                db.sessionmaker,
                BlobStore(settings.blob_root),
                paths,
                tenant=settings.default_tenant,
                reports_dir=None,
                max_upload_bytes=settings.max_upload_bytes,
            )
        finally:
            await db.dispose()

    linked = await do_import()  # the live runs are in this DB: linked, not duplicated
    assert (linked.runs_created, linked.runs_linked) == (0, len(BRIEFS))

    await _wipe_golden_runs(settings, sorted({i.golden_key for i in items}))
    created = await do_import()  # a fresh DB (the dry-run case): runs rebuilt from the manifest
    assert (created.runs_created, created.runs_linked) == (len(BRIEFS), 0)
    again = await do_import()
    assert (again.runs_created, again.runs_linked) == (0, len(BRIEFS))

    app = create_app(settings)
    try:
        async with client_for(app) as client:
            listed = (
                await client.get("/v1/runs", params={"origin": "golden", "limit": 100})
            ).json()
            details = {rid: (await client.get(f"/v1/runs/{rid}")).json() for rid in run_ids}
            events = await client.get(f"/v1/runs/{next(iter(run_ids))}/events")
    finally:
        await app.state.runner.shutdown()
        await app.state.db.dispose()
    assert run_ids <= {r["id"] for r in listed["items"]}
    for item in items:
        detail = details[item.run.run_id]
        assert detail["run"]["origin"] == "golden" and detail["run"]["status"] == item.run.status
        assert detail["brief"]["golden_key"] == item.golden_key
        by_image = {c["image_id"]: c for c in detail["candidates"]}
        shown = [c for c in detail["candidates"] if c["kind"] == item.candidate_kind]
        assert shown and all(c["evaluation"] for c in shown), item.id
        assert len(by_image) == len(detail["candidates"])
        if item.set == "final":
            final_id = detail["approved_candidate_id"] or detail["best_candidate_id"]
            final = next(c for c in detail["candidates"] if c["id"] == final_id)
            assert final["kind"] == item.candidate_kind
            if len(detail["candidates"]) == 2:  # lineage: output <- first attempt
                first = next(c for c in detail["candidates"] if c["id"] != final_id)
                assert final["parent_candidate_id"] == first["id"]
            assert final["evaluation"]["verdict"] == item.pipeline_verdict
    assert events.status_code == 200 and "run.finished" in events.text
