"""`/v1/evals/summary` and `/v1/evals/items` (architecture API contract), served from the newest
`eval_reports` row."""

import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete

from backend.db import models_adstudio as m
from backend.domain.adstudio.evalreport import (
    DimensionMetrics,
    EvalReportData,
    ItemRecord,
)
from tests.conftest import client_for

DIMS = ("technical", "text", "product", "context")


def _item(i: int, image_set: str, outcome: str) -> ItemRecord:
    fail = outcome in ("tp", "fn")
    flagged = outcome in ("tp", "fp")
    return ItemRecord(
        id=f"I{i:02d}",
        set=image_set,  # type: ignore[arg-type]
        brief_id="B01",
        product_id="P1",
        split="calibration",
        image_sha=f"{i:064x}",
        mutation="text_typo" if image_set == "plant" else None,
        label_source="planted" if image_set == "plant" else "human",
        labels={d: not (d == "text" and fail) for d in DIMS},
        verdict="fail" if flagged else "pass",
        dimensions={d: not (d == "text" and flagged) for d in DIMS},
        outcomes={"text": outcome},  # type: ignore[dict-item]
        evidence=["Rendered «Sumer»"] if flagged else [],
    )


def _report() -> EvalReportData:
    items = [_item(i, "plant", "tp") for i in range(3)] + [
        _item(3, "nat", "fp"),
        _item(4, "nat", "tn"),
    ]
    return EvalReportData(
        report_file="20260101T000000Z.md",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        evaluator_version="ev-test",
        dataset_version="abc123",
        dry_run=True,
        per_dimension={"text": DimensionMetrics(n=5, precision=0.75, recall=1.0, f1=0.857)},
        items=items,
    )


async def _clear(app) -> None:  # type: ignore[no-untyped-def]
    async with app.state.db.sessionmaker() as session:
        await session.execute(delete(m.EvalReport))
        await session.commit()


async def test_evals_summary_404_before_first_report(app_factory) -> None:  # type: ignore[no-untyped-def]
    app = app_factory()
    await _clear(app)
    async with client_for(app) as client:
        res = await client.get("/v1/evals/summary")
        items = await client.get("/v1/evals/items")
    assert res.status_code == 404 and res.json()["type"] == "no-eval-report"
    assert res.headers["content-type"].startswith("application/problem+json")
    assert items.status_code == 404


async def test_evals_summary_and_items(app_factory) -> None:  # type: ignore[no-untyped-def]
    app = app_factory()
    await _clear(app)
    data = _report()
    async with app.state.db.sessionmaker() as session:
        row = m.EvalReport(
            evaluator_version=data.evaluator_version,
            dataset_version=data.dataset_version,
            metrics=json.loads(data.model_dump_json()),
            report_path="x.md",
        )
        session.add(row)
        await session.commit()
        report_id = str(row.id)
    async with client_for(app) as client:
        summary = (await client.get("/v1/evals/summary")).json()
        planted = (await client.get("/v1/evals/items", params={"origin": "planted"})).json()
        fps = (
            await client.get("/v1/evals/items", params={"dimension": "text", "outcome": "fp"})
        ).json()
        page1 = (await client.get("/v1/evals/items", params={"limit": 2})).json()
        page2 = (
            await client.get("/v1/evals/items", params={"limit": 2, "cursor": page1["next_cursor"]})
        ).json()
        bad = await client.get("/v1/evals/items", params={"cursor": "!!"})
        other = await client.get("/v1/evals/summary", params={"evaluator_version": "ev-none"})

    assert summary["report_id"] == report_id and summary["dry_run"] is True
    assert summary["per_dimension"]["text"]["precision"] == 0.75
    assert summary["dataset_version"] == "abc123"
    assert [i["id"] for i in planted["items"]] == ["I00", "I01", "I02"]
    assert all(i["origin"] == "planted" and i["image_url"] is None for i in planted["items"])
    assert [i["id"] for i in fps["items"]] == ["I03"]
    assert [i["id"] for i in page1["items"]] == ["I00", "I01"] and page1["next_cursor"]
    assert [i["id"] for i in page2["items"]] == ["I02", "I03"]
    assert bad.status_code == 422 and other.status_code == 404


async def _golden_run_for(app, sha: str) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    """An image with this sha and a golden run whose candidate shows it -> (image id, run id)."""
    from backend.db import repositories as repo

    async with app.state.db.sessionmaker() as session:
        ref, _ = await repo.get_or_create_image(
            session,
            "demo",
            sha256=f"{uuid.uuid4().hex}{uuid.uuid4().hex}",
            source="upload",
            mime="image/png",
            width=512,
            height=512,
            size=10,
        )
        product = await repo.create_product(session, "demo", name="P1 mug", image_id=ref.id)
        image, _ = await repo.get_or_create_image(
            session,
            "demo",
            sha256=sha,
            source="golden",
            mime="image/png",
            width=820,
            height=1024,
            size=10,
        )
        brief = await repo.create_brief(
            session,
            "demo",
            product_id=product.id,
            geography_code="AU",
            season="December",
            required_text="Summer Sale — 30% OFF",
        )
        run = await repo.create_run(
            session,
            "demo",
            brief_id=brief.id,
            origin="golden",
            status="passed",
            idempotency_key=f"t:{uuid.uuid4().hex}",
        )
        await repo.create_candidate(
            session,
            run.id,
            image_id=image.id,
            kind="initial",
            attempt=0,
            slot=0,
            status="passed",
        )
        await session.commit()
        return str(image.id), str(run.id)


async def test_eval_items_carry_brief_fields_and_golden_run(app_factory) -> None:  # type: ignore[no-untyped-def]
    app = app_factory()
    await _clear(app)
    data = _report()
    data.items[3].image_sha = uuid.uuid4().hex * 2  # a fresh sha per test run (I03, natural)
    data.items[0].image_sha = uuid.uuid4().hex * 2  # I00, planted: never gets a run id
    async with app.state.db.sessionmaker() as session:
        session.add(
            m.EvalReport(
                evaluator_version=data.evaluator_version,
                dataset_version=data.dataset_version,
                metrics=json.loads(data.model_dump_json()),
                report_path="x.md",
            )
        )
        await session.commit()
    image_id, run_id = await _golden_run_for(app, data.items[3].image_sha)
    await _golden_run_for(app, data.items[0].image_sha)
    async with client_for(app) as client:
        items = (await client.get("/v1/evals/items")).json()["items"]
    by_id = {i["id"]: i for i in items}
    nat = by_id["I03"]
    assert nat["run_id"] == run_id and nat["image_id"] == image_id
    assert nat["geography_code"] == "AU" and nat["season"] == "December"
    assert nat["required_text"] == "Summer Sale — 30% OFF"
    assert "south" in nat["tags"] and nat["product_id"] == "P1" and nat["product_name"]
    assert nat["aspect_ratio"] == "4:5"
    assert by_id["I00"]["run_id"] is None and by_id["I00"]["image_id"]
    assert by_id["I04"]["run_id"] is None  # no image in this DB


async def test_evals_summary_exposes_provenance_warnings_and_pipeline(app_factory) -> None:  # type: ignore[no-untyped-def]
    from backend.domain.adstudio.evalreport import (
        FAKE_DRY_RUN_WARNING,
        AgreementMetrics,
        PipelineMetrics,
    )

    app = app_factory()
    await _clear(app)
    data = _report()
    data.counts = {"nat": 2, "final": 0, "plant": 3, "human_labelled": 0}
    data.pipeline = PipelineMetrics(briefs=2, mean_repairs=0.5, status_counts={"passed": 2})
    data.agreement = {"nat": {"text": AgreementMetrics(n=2, agree=1, agreement=0.5, kappa=0.0)}}
    data.provenance = {
        "vision_client": "fake",
        "vision_model": "test:fake-vision",
        "ocr_engine": "tesseract",
        "ocr_version": "5.5.3",
        "ocr_languages": ["eng"],
        "image_models": ["test:fake-image"],
        "prompt_versions": {"context_judge": "1"},
        "snapshot": "/nowhere/cache/verdicts.json",
    }
    async with app.state.db.sessionmaker() as session:
        session.add(
            m.EvalReport(
                evaluator_version=data.evaluator_version,
                dataset_version=data.dataset_version,
                metrics=json.loads(data.model_dump_json()),
                report_path="x.md",
            )
        )
        await session.commit()
    async with client_for(app) as client:
        summary = (await client.get("/v1/evals/summary")).json()
    assert summary["pipeline"]["mean_repairs"] == 0.5
    assert summary["pipeline"]["status_counts"] == {"passed": 2}
    assert summary["agreement_by_dimension"]["nat"]["text"]["kappa"] == 0.0
    prov = summary["provenance"]
    assert prov["judge_model"] == "test:fake-vision" and prov["ocr_version"] == "5.5.3"
    assert prov["ocr_languages"] == ["eng"] and prov["image_models"] == ["test:fake-image"]
    assert prov["evaluator_version"] == "ev-test" and prov["snapshot_file"] == "verdicts.json"
    warnings = summary["warnings"]
    assert warnings[0] == FAKE_DRY_RUN_WARNING
    assert any(w.startswith("Missing labels") for w in warnings)
    assert any("evaluator is now" in w for w in warnings)  # ev-test is not the live version


async def test_golden_sources_endpoint(app_factory) -> None:  # type: ignore[no-untyped-def]
    app = app_factory()
    async with client_for(app) as client:
        res = await client.get("/v1/golden/sources")
    assert res.status_code == 200
    body = res.json()
    assert body["markdown"].startswith("# Golden dataset")
    assert [p["id"] for p in body["products"]] == ["P1", "P2", "P3", "P4", "P5"]
    p2 = body["products"][1]
    assert p2["share_alike"] is True and p2["licence"] == "CC BY-SA 4.0"
    assert p2["name"] == "Labelled glass bottle" and p2["source_url"].startswith("https://")


async def test_evals_summary_by_golden_version(app_factory) -> None:  # type: ignore[no-untyped-def]
    """ADR-007: one report per golden version; `golden_version` picks one, the default is the
    newest (v2, which carries the v1 -> v2 comparison); items filter the same way."""
    from backend.domain.adstudio.evalreport import GoldenComparison, VersionSummary

    app = app_factory()
    await _clear(app)
    v1 = _report().model_copy(update={"golden_version": "v1", "report_file": "a-v1.md"})
    comparison = GoldenComparison(
        versions=[
            VersionSummary(version="v1", evaluator_version="ev-0.6"),
            VersionSummary(version="v2", evaluator_version="ev-0.6"),
        ]
    )
    v2 = _report().model_copy(
        update={
            "golden_version": "v2",
            "report_file": "a-v2.md",
            "comparison": comparison,
            "items": _report().items[:1],
        }
    )
    async with app.state.db.sessionmaker() as session:
        for data in (v1, v2):
            session.add(
                m.EvalReport(
                    evaluator_version=data.evaluator_version,
                    dataset_version=data.dataset_version,
                    metrics=json.loads(data.model_dump_json()),
                    report_path=data.report_file,
                )
            )
            await session.commit()  # separate transactions: v2 is newer
    async with client_for(app) as client:
        newest = (await client.get("/v1/evals/summary")).json()
        first = (await client.get("/v1/evals/summary", params={"golden_version": "v1"})).json()
        items_v1 = (await client.get("/v1/evals/items", params={"golden_version": "v1"})).json()
        missing = await client.get("/v1/evals/summary", params={"golden_version": "v9"})
        bad = await client.get("/v1/evals/summary", params={"golden_version": "x"})
    assert newest["golden_version"] == "v2" and newest["report_file"] == "a-v2.md"
    assert [v["version"] for v in newest["comparison"]["versions"]] == ["v1", "v2"]
    assert first["golden_version"] == "v1" and first["comparison"] is None
    assert len(items_v1["items"]) == 5
    assert missing.status_code == 404 and bad.status_code == 422
