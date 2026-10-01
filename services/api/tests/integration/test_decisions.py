"""Human decision (approve / reject, audited, overrides labelled), cancel, history and run metrics.

Each test that counts runs uses its own tenant, so the shared test database doesn't leak in.
"""

import asyncio
import uuid
from typing import Any

import httpx
from fastapi import FastAPI
from sqlalchemy import select

from backend.db.models_adstudio import Label, RunDecision
from backend.domain.adstudio.evaluator.ocr import TesseractOcr
from backend.domain.adstudio.image_clients import FakeImageClient, ImageRequest, ImageResult
from tests.conftest import client_for
from tests.integration.test_orchestrator import needs_tesseract, scripted_app
from tests.integration.test_runs import parse_sse, start_run, upload_product

RECOLOR = "candidate=recolor;repair_product=recolor"  # ends needs_review
TYPO = "candidate=typo;repair_text=typo"  # ends passed via overlay


def tenant() -> str:
    return f"t-{uuid.uuid4().hex[:12]}"


async def run_to_end(app: FastAPI, client: httpx.AsyncClient, product_id: str) -> dict[str, Any]:
    res = await start_run(client, product_id, f"k-{uuid.uuid4()}", fresh=True)
    assert res.status_code == 202, res.text
    run_id = res.json()["run_id"]
    await app.state.runner.join(uuid.UUID(run_id), timeout_s=120)
    return (await client.get(f"/v1/runs/{run_id}")).json()


async def decide(client: httpx.AsyncClient, run_id: str, **body: Any) -> httpx.Response:
    return await client.post(f"/v1/runs/{run_id}/decision", json=body)


async def rows(app: FastAPI, model: Any, **where: Any) -> list[Any]:
    async with app.state.db.sessionmaker() as session:
        stmt = select(model).filter_by(**where)
        return list((await session.scalars(stmt)).all())


async def test_decision_requires_reason_for_override(app_factory) -> None:  # type: ignore[no-untyped-def]
    app, _ = scripted_app(app_factory, RECOLOR, default_tenant=tenant())
    async with client_for(app) as client:
        product_id = await upload_product(client)
        detail = await run_to_end(app, client, product_id)
        run_id = detail["run"]["id"]
        assert detail["run"]["status"] == "needs_review"
        missing = await decide(client, run_id, action="approve")
        blank = await decide(client, run_id, action="approve", reason="   ")
        ok = await decide(
            client, run_id, action="approve", reason="Colour is on-brand for AU", actor="maya"
        )
        again = await decide(client, run_id, action="reject")
        after = (await client.get(f"/v1/runs/{run_id}")).json()
        events = parse_sse((await client.get(f"/v1/runs/{run_id}/events")).text)
        unknown = await decide(client, str(uuid.uuid4()), action="reject")
        bad_actor = await decide(client, run_id, action="reject", actor="no spaces allowed")

    for res in (missing, blank):
        assert res.status_code == 422 and res.json()["type"] == "override-reason-required"
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["status"] == "approved" and body["previous_status"] == "needs_review"
    assert body["is_override"] is True and body["actor"] == "maya"
    assert body["approved_candidate_id"] == detail["best_candidate_id"]
    assert again.status_code == 409 and again.json()["type"] == "run-not-decidable"
    assert unknown.status_code == 404 and bad_actor.status_code == 422
    assert after["run"]["status"] == "approved"
    assert after["approved_candidate_id"] == detail["best_candidate_id"]
    approved = next(c for c in after["candidates"] if c["id"] == body["approved_candidate_id"])
    assert approved["status"] == "approved"
    assert events[-1]["type"] == "run.status" and events[-1]["status"] == "approved"
    (audit,) = await rows(app, RunDecision, run_id=uuid.UUID(run_id))  # refusals wrote nothing
    assert (audit.action, audit.actor, audit.previous_status) == ("approve", "maya", "needs_review")
    assert audit.is_override and audit.reason == "Colour is on-brand for AU"


@needs_tesseract
async def test_override_writes_label(app_factory) -> None:  # type: ignore[no-untyped-def]
    app, _ = scripted_app(app_factory, RECOLOR, default_tenant=tenant())
    async with client_for(app) as client:
        product_id = await upload_product(client)
        held = await run_to_end(app, client, product_id)
        override = await decide(client, held["run"]["id"], action="approve", reason="Looks right")
        app.state.pipeline.image_client = FakeImageClient()
        clean = await run_to_end(app, client, product_id)
        plain = await decide(client, clean["run"]["id"], action="approve")
        app.state.pipeline.image_client = FakeImageClient(script=TYPO)
        passed = await run_to_end(app, client, product_id)
        veto = await decide(client, passed["run"]["id"], action="reject", actor="sam")

    # Approving the held run: a human label on the approved image, all dimensions ok.
    best = next(c for c in held["candidates"] if c["id"] == held["best_candidate_id"])
    (label,) = await rows(app, Label, run_id=uuid.UUID(held["run"]["id"]))
    assert override.json()["label_id"] == str(label.id)
    assert label.labeller == "human:reviewer" and str(label.image_id) == best["image_id"]
    assert (label.text_ok, label.product_ok, label.context_ok, label.overall_ok) == (
        True,
        True,
        True,
        True,
    )
    assert label.notes == "Looks right" and label.rubric_version == "decision-1"

    # Approving a passed run needs no reason, is not an override and writes no label.
    assert clean["run"]["status"] == "passed"
    assert plain.status_code == 200 and plain.json()["is_override"] is False
    assert plain.json()["label_id"] is None
    assert not await rows(app, Label, run_id=uuid.UUID(clean["run"]["id"]))

    # Rejecting a run the gate passed is the other override: labelled overall not ok.
    assert passed["run"]["status"] == "passed" and passed["run"]["outcome"] == "overlay"
    assert veto.status_code == 200 and veto.json()["status"] == "rejected"
    assert veto.json()["is_override"] is True
    (rejected,) = await rows(app, Label, run_id=uuid.UUID(passed["run"]["id"]))
    assert rejected.labeller == "human:sam" and rejected.overall_ok is False
    assert rejected.text_ok is None
    audits = await rows(app, RunDecision, run_id=uuid.UUID(passed["run"]["id"]))
    assert [(a.action, a.is_override) for a in audits] == [("reject", True)]


class GatedImageClient(FakeImageClient):
    """Holds every image call until released, so a run can be cancelled mid-flight."""

    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def generate(self, request: ImageRequest, *, model: str) -> ImageResult:
        self.started.set()
        await self.release.wait()
        return await super().generate(request, model=model)


async def test_cancel_run(app_factory) -> None:  # type: ignore[no-untyped-def]
    app, _ = scripted_app(app_factory)
    gated = GatedImageClient()
    app.state.pipeline.image_client = gated
    async with client_for(app) as client:
        product_id = await upload_product(client)
        res = await start_run(client, product_id, f"k-{uuid.uuid4()}", fresh=True)
        run_id = res.json()["run_id"]
        await asyncio.wait_for(gated.started.wait(), timeout=30)
        cancelled = await client.post(f"/v1/runs/{run_id}/cancel")
        detail = (await client.get(f"/v1/runs/{run_id}")).json()
        events = parse_sse((await client.get(f"/v1/runs/{run_id}/events")).text)
        again = await client.post(f"/v1/runs/{run_id}/cancel")
        decision = await decide(client, run_id, action="approve", reason="n/a")
        missing = await client.post(f"/v1/runs/{uuid.uuid4()}/cancel")

    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json() == {"run_id": run_id, "status": "cancelled", "cancelled": True}
    assert detail["run"]["status"] == "cancelled" and detail["run"]["finished_at"]
    assert not detail["candidates"] and not app.state.runner.has_task(uuid.UUID(run_id))
    assert events[-1]["type"] == "run.finished" and events[-1]["status"] == "cancelled"
    assert again.status_code == 409 and again.json()["type"] == "run-not-active"
    assert decision.status_code == 409
    assert missing.status_code == 404


async def test_list_runs_cursor_paging_and_filters(app_factory) -> None:  # type: ignore[no-untyped-def]
    app, _ = scripted_app(app_factory, RECOLOR, default_tenant=tenant())
    async with client_for(app) as client:
        product_id = await upload_product(client)
        ids = [(await run_to_end(app, client, product_id))["run"]["id"] for _ in range(3)]
        await decide(client, ids[0], action="reject")
        first = (await client.get("/v1/runs", params={"limit": 2})).json()
        second = (
            await client.get("/v1/runs", params={"limit": 2, "cursor": first["next_cursor"]})
        ).json()
        held = (await client.get("/v1/runs", params={"status": "needs_review"})).json()
        golden = (await client.get("/v1/runs", params={"origin": "golden"})).json()
        bad_status = await client.get("/v1/runs", params={"status": "nope"})
        bad_cursor = await client.get("/v1/runs", params={"cursor": "%%%"})

    assert [r["id"] for r in first["items"]] == ids[::-1][:2] and first["next_cursor"]
    assert [r["id"] for r in second["items"]] == [ids[0]] and second["next_cursor"] is None
    item = second["items"][0]
    assert item["status"] == "rejected" and item["geography_code"] == "AU"
    assert item["required_text"] == "Summer Sale — 30% OFF" and item["repair_count"] == 2
    assert item["thumbnail_url"].startswith("/v1/images/")
    assert {r["id"] for r in held["items"]} == set(ids[1:])
    assert golden["items"] == []
    assert bad_status.status_code == 422 and bad_cursor.status_code == 422


@needs_tesseract
async def test_ops_summary_run_metrics(app_factory) -> None:  # type: ignore[no-untyped-def]
    app, _ = scripted_app(app_factory, default_tenant=tenant())
    async with client_for(app) as client:
        product_id = await upload_product(client)
        clean = await run_to_end(app, client, product_id)  # passed native, first attempt
        await decide(client, clean["run"]["id"], action="approve")
        app.state.pipeline.image_client = FakeImageClient(script=RECOLOR)
        held = await run_to_end(app, client, product_id)  # needs_review, 2 repairs
        await decide(client, held["run"]["id"], action="approve", reason="Human override")
        app.state.pipeline.image_client = FakeImageClient(script=TYPO)
        overlay = await run_to_end(app, client, product_id)  # passed overlay, 1 repair
        await decide(client, overlay["run"]["id"], action="reject")
        summary = (await client.get("/v1/ops/summary", params={"run_window": 50})).json()

    runs = summary["runs"]
    assert runs["count"] == runs["finished"] == 3
    assert runs["by_status"] == {"approved": 2, "rejected": 1}
    assert runs["approved"] == 2 and runs["approved_rate"] == round(2 / 3, 4)
    assert runs["gate_pass_rate"] == round(2 / 3, 4)
    assert runs["first_attempt_pass_rate"] == round(1 / 3, 4)
    assert runs["mean_repairs"] == 1.0
    assert runs["overlay_rate"] == 0.5
    assert runs["override_count"] == 1
    assert runs["total_cost_usd"] == 0.0 and runs["cost_per_approved_ad"] == 0.0
    assert runs["p50_ms_to_approved"] is not None
    assert runs["p95_ms_to_approved"] >= runs["p50_ms_to_approved"]
    assert runs["active"] == 0 and runs["queued"] == 0
    rates = runs["dimension_pass_rates"]
    assert set(rates) == {"technical", "text", "product", "context", "composition"}
    assert rates["technical"] == 1.0 and rates["composition"] == 1.0  # the fake judge: realistic
    assert rates["product"] is not None and rates["product"] < 1.0  # the recoloured candidates


async def test_ops_summary_empty_tenant(app_factory) -> None:  # type: ignore[no-untyped-def]
    app, _ = scripted_app(app_factory, default_tenant=tenant())
    async with client_for(app) as client:
        runs = (await client.get("/v1/ops/summary")).json()["runs"]
    assert runs["count"] == 0 and runs["by_status"] == {}
    for key in (
        "approved_rate",
        "first_attempt_pass_rate",
        "mean_repairs",
        "overlay_rate",
        "cost_per_approved_ad",
        "p50_ms_to_approved",
    ):
        assert runs[key] is None


async def test_ready_reports_image_models_judge_and_ocr(app_factory) -> None:  # type: ignore[no-untyped-def]
    live = app_factory(image_client="gemini", vision_client="gemini")  # no key in tests
    fake, _ = scripted_app(app_factory)
    async with client_for(live) as client:
        unconfigured = (await client.get("/ready")).json()["adstudio"]
    async with client_for(fake) as client:
        offline = (await client.get("/ready")).json()["adstudio"]

    roles = [r["role"] for r in unconfigured["models"]]
    assert roles == ["image_candidate", "image_repair", "vision_judge"]
    assert not any(r["configured"] for r in unconfigured["models"])
    assert unconfigured["status"] == "degraded" and "not configured" in unconfigured["detail"]
    assert unconfigured["models"][0]["spec"] == "google:gemini-3.1-flash-lite-image"
    assert all(r["configured"] for r in offline["models"])
    assert offline["models"][0]["recorded_as"] == "test:fake-image"
    ocr = offline["ocr"]
    assert ocr["engine"] == "tesseract" and ocr["available"] == TesseractOcr().available()
    if ocr["available"]:
        assert ocr["scripts"]["Latn"] is True and ocr["languages"] > 0
        assert offline["status"] == "ok"
