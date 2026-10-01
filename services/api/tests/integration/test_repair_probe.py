"""`golden repair-probe` on a tmp dry run (fake image + vision clients, the test DB): every failing
first-round draft goes through the production repair loop in its own probe run, which export never
sees and whose ledger rows carry the probe run id. No network."""

import shutil
from pathlib import Path

import pytest
from sqlalchemy import delete, func, select

from backend.db import models_adstudio as m
from backend.db.models_calls import ModelCall
from backend.golden.context import build_context
from backend.golden.dataset import GOLDEN_DIR, GoldenPaths, read_outputs
from backend.golden.export import golden_export
from backend.golden.repair_probe import probe_label, run_probe
from backend.golden.runner import golden_run
from tests.conftest import make_settings
from tests.golden_fixtures import tesseract_available

BRIEF = "V09"  # Korean copy "추석 특가 20%"
# Script the first-round drafts to fail text deterministically (the typo drops the critical "%")
# instead of relying on the evaluator misreading the fake client's clean Korean text, which the
# ev-0.8 text checks now read correctly. Repairs are unscripted, so the loop can fix the draft.
SCRIPT = "candidate=typo"


@pytest.mark.skipif(not tesseract_available(), reason="the evaluator's text checks need tesseract")
async def test_repair_probe_repairs_each_failing_draft_outside_the_export(tmp_path: Path) -> None:
    settings = make_settings(
        image_client="fake",
        vision_client="fake",
        blob_dir=str(tmp_path / "blobs"),
        fake_image_script=SCRIPT,
    )
    out = tmp_path / "v3"
    out.mkdir()
    shutil.copy(GOLDEN_DIR / "v3" / "briefs.yaml", out / "briefs.yaml")
    paths = GoldenPaths(source=GOLDEN_DIR, out=out)
    ctx = build_context(settings)
    created: list[object] = []  # run ids to remove afterwards (this DB is shared with dry runs)
    try:
        (golden,) = await golden_run(ctx, paths, only={BRIEF})
        created.append(golden.run_id)
        probes = await run_probe(ctx, paths, only={BRIEF})
        created += [p.probe_run_id for p in probes]
        result = await golden_export(ctx, paths, only={BRIEF}, record_verdicts=False)
        async with ctx.deps.sessionmaker() as session:
            probe_runs = list(
                await session.scalars(select(m.Run).where(m.Run.batch_label == probe_label()))
            )
            mine = [r for r in probe_runs if r.id in {p.probe_run_id for p in probes}]
            ledger = await session.scalar(
                select(func.count())
                .select_from(ModelCall)
                .where(ModelCall.run_id.in_([r.id for r in mine]))
            )
            briefs = {
                b.id: b
                for b in await session.scalars(
                    select(m.Brief).where(m.Brief.id.in_([r.brief_id for r in mine]))
                )
            }
    finally:
        try:
            async with ctx.deps.sessionmaker() as session:
                await session.execute(delete(m.Run).where(m.Run.id.in_(created)))
                await session.commit()
        finally:
            await ctx.close()

    assert golden.status in ("passed", "needs_review") and golden.run_id is not None
    assert probes, "the scripted typo fails both drafts' text: there is something to probe"
    assert {p.source_run_id for p in probes} == {golden.run_id}
    assert sorted(p.slot for p in probes) == sorted({p.slot for p in probes})  # one probe a draft
    for probe in probes:
        assert probe.status in ("passed", "needs_review"), probe
        assert probe.steps[0].kind == "initial" and probe.steps[0].verdict == "fail"
        assert len(probe.steps) >= 2, "the loop routed the draft to a repair or the overlay"
        assert not probe.note, probe.note  # the re-evaluation matches the source verdict
    # Probe runs: their own brief rows (no golden_key), invisible to export.
    assert len(mine) == len(probes)
    assert all(r.origin == "golden" and r.config["probe"]["brief_id"] == BRIEF for r in mine)
    assert all(briefs[r.brief_id].golden_key is None for r in mine)
    assert {i.run.run_id for i in read_outputs(paths)} == {str(golden.run_id)}
    assert result.items and not result.missing
    assert ledger, "judge / OCR calls are recorded against the probe run"
