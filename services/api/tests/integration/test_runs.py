"""Runs API end to end with the fake image client (no key, no network)."""

import asyncio
import io
import json
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI
from PIL import Image

from tests.conftest import client_for
from tests.images import png

GOLDEN = Path(__file__).resolve().parents[4] / "data" / "golden"
B01 = {"geography_code": "AU", "season": "December", "required_text": "Summer Sale — 30% OFF"}


def fake_app(app_factory, **overrides: object) -> FastAPI:  # type: ignore[no-untyped-def]
    overrides.setdefault("vision_client", "fake")
    app = app_factory(image_client="fake", **overrides)
    app.state.sse_poll_s = 0.02
    return app


async def upload_product(client: httpx.AsyncClient, data: bytes | None = None) -> str:
    data = data or (GOLDEN / "products" / "P1.jpg").read_bytes()
    res = await client.post(
        "/v1/products", data={"name": "Mug"}, files={"image": ("p.jpg", data, "image/jpeg")}
    )
    assert res.status_code in (200, 201), res.text
    return res.json()["id"]


async def start_run(
    client: httpx.AsyncClient, product_id: str, key: str, **brief: Any
) -> httpx.Response:
    body = {"product_id": product_id, **B01, **brief}
    return await client.post("/v1/runs", json=body, headers={"Idempotency-Key": key})


def parse_sse(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in text.split("\n\n"):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            if line.startswith(":") or ":" not in line:
                continue
            name, _, value = line.partition(":")
            fields[name] = value.strip()
        if "data" in fields:
            events.append(
                {"id": fields.get("id"), "event": fields.get("event"), **json.loads(fields["data"])}
            )
    return events


async def test_run_idempotency(app_factory) -> None:  # type: ignore[no-untyped-def]
    app = fake_app(app_factory)
    key = f"k-{uuid.uuid4()}"
    async with client_for(app) as client:
        product_id = await upload_product(client, png(512, 512, (10, 200, 30)))
        first = await start_run(client, product_id, key)
        again = await start_run(client, product_id, key)
        conflict = await start_run(client, product_id, key, required_text="Different")
        missing = await client.post("/v1/runs", json={"product_id": product_id, **B01})
        other = await start_run(client, product_id, f"k-{uuid.uuid4()}")
        await app.state.runner.drain()

    assert first.status_code == 202, first.text
    assert first.json()["events_url"] == f"/v1/runs/{first.json()['run_id']}/events"
    assert again.status_code == 200 and again.json()["run_id"] == first.json()["run_id"]
    assert conflict.status_code == 409
    assert conflict.headers["content-type"].startswith("application/problem+json")
    assert conflict.json()["type"] == "idempotency-conflict"
    assert missing.status_code == 422
    assert other.status_code == 202 and other.json()["run_id"] != first.json()["run_id"]


async def test_queue_full_429_leaves_no_orphaned_run(app_factory) -> None:  # type: ignore[no-untyped-def]
    app = fake_app(app_factory, runs_queue_cap=1)
    keys = [f"k-{uuid.uuid4()}" for _ in range(6)]
    async with client_for(app) as client:
        product_id = await upload_product(client, png(512, 512, (10, 200, 30)))
        responses = await asyncio.gather(*(start_run(client, product_id, k) for k in keys))
        await app.state.runner.drain()
        rejected = [k for k, r in zip(keys, responses, strict=True) if r.status_code == 429]
        retries: list[httpx.Response] = []
        for key in rejected:
            retries.append(await start_run(client, product_id, key))
            await app.state.runner.drain()

    assert {r.status_code for r in responses} <= {202, 429}
    assert rejected, "the burst should overflow a queue of 1"
    # A rejected key was never used: a retry creates a fresh run instead of replaying an orphan.
    assert [r.status_code for r in retries] == [202] * len(rejected)
    assert app.state.runner.queued == 0


async def test_skeleton_run_end_to_end(app_factory) -> None:  # type: ignore[no-untyped-def]
    app = fake_app(app_factory)
    async with client_for(app) as client:
        product_id = await upload_product(client)
        res = await start_run(client, product_id, f"k-{uuid.uuid4()}")
        run_id = res.json()["run_id"]
        await app.state.runner.join(uuid.UUID(run_id))
        detail = (await client.get(f"/v1/runs/{run_id}")).json()
        image = await client.get(detail["candidates"][0]["image_url"])

    from backend.domain.adstudio.evaluator.ocr import TesseractOcr

    candidates = detail["candidates"]
    assert [(c["attempt"], c["slot"], c["kind"]) for c in candidates] == [
        (0, 0, "initial"),
        (0, 1, "initial"),
    ]  # N=2 Flash-Lite candidates, both clean, so no repair
    cand = candidates[0]
    assert detail["brief"]["required_text"] == B01["required_text"]
    assert cand["served_model"] == "test:fake-image"
    checks = {c["check_name"] for c in cand["evaluation"]["checks"]}
    assert {"resolution", "aspect", "not_blank", "not_placeholder"} <= checks
    if TesseractOcr().available():  # live OCR reads the fake's headline
        assert detail["run"]["status"] == "passed"
        assert detail["run"]["outcome"] == "native"
        assert detail["run"]["first_attempt_pass"] is True
        assert detail["approved_candidate_id"] in {c["id"] for c in candidates}
        assert cand["evaluation"]["verdict"] == "pass"
        assert cand["evaluation"]["dimensions"]["text"] is True
        assert {"ocr_cer", "critical_tokens", "stray_text"} <= checks
    else:  # no OCR: text is unverified; the overlay can't be OCR-verified -> review
        assert detail["run"]["status"] == "needs_review"
        assert cand["evaluation"]["verdict"] == "unverified"
    with Image.open(io.BytesIO(image.content)) as img:
        assert max(img.size) <= 1024
        assert img.size == (825, 1024)  # the fake's 928x1152 "1K" 4:5, downscaled


async def test_sse_replay_from_last_event_id(app_factory) -> None:  # type: ignore[no-untyped-def]
    app = fake_app(app_factory)
    async with client_for(app) as client:
        product_id = await upload_product(client)
        run_id = (await start_run(client, product_id, f"k-{uuid.uuid4()}")).json()["run_id"]
        # Live tail: connect while the run is in flight; the stream closes after run.finished.
        live = await client.get(f"/v1/runs/{run_id}/events")
        full = await client.get(f"/v1/runs/{run_id}/events")  # after the end: full replay
        resumed = await client.get(f"/v1/runs/{run_id}/events", headers={"Last-Event-ID": "3"})
        by_query = await client.get(f"/v1/runs/{run_id}/events", params={"after": 3})
        missing = await client.get(f"/v1/runs/{uuid.uuid4()}/events")

    assert live.headers["content-type"].startswith("text/event-stream")
    live_events, all_events = parse_sse(live.text), parse_sse(full.text)
    assert [e["type"] for e in live_events] == [e["type"] for e in all_events]
    types = [e["type"] for e in all_events]
    assert types[0] == "run.status" and types[-1] == "run.finished"
    assert types.index("plan.done") < types.index("candidate.created")
    assert types.index("candidate.created") < types.index("evaluation.done")
    seqs = [int(e["id"]) for e in all_events]
    assert seqs == list(range(1, len(seqs) + 1))
    assert all(e["seq"] == int(e["id"]) and e["run_id"] == run_id for e in all_events)
    assert all(e["event"] == e["type"] for e in all_events)

    replayed = parse_sse(resumed.text)
    assert [int(e["id"]) for e in replayed] == seqs[3:]
    assert replayed == all_events[3:]
    assert parse_sse(by_query.text) == all_events[3:]
    assert missing.status_code == 404


async def test_run_requires_configured_image_model(app_factory) -> None:  # type: ignore[no-untyped-def]
    app = app_factory(image_client="gemini")  # no GOOGLE_API_KEY in tests
    async with client_for(app) as client:
        product_id = await upload_product(client, png(400, 400, (1, 1, 200)))
        res = await start_run(client, product_id, f"k-{uuid.uuid4()}")
    assert res.status_code == 503 and res.json()["type"] == "image-unconfigured"


async def test_run_rejects_invisible_text_and_unknown_product(app_factory) -> None:  # type: ignore[no-untyped-def]
    app = fake_app(app_factory)
    async with client_for(app) as client:
        product_id = await upload_product(client, png(400, 400, (1, 90, 90)))
        bidi = await start_run(
            client, product_id, f"k-{uuid.uuid4()}", required_text="Sale" + chr(0x202E)
        )
        lines = await start_run(client, product_id, f"k-{uuid.uuid4()}", required_text="a\nb\nc\nd")
        nope = await start_run(client, str(uuid.uuid4()), f"k-{uuid.uuid4()}")
    assert bidi.status_code == 422 and bidi.json()["type"] == "invalid-text"
    assert lines.status_code == 422 and lines.json()["type"] == "invalid-text"
    assert nope.status_code == 404


async def test_safety_block_ends_needs_review(app_factory) -> None:  # type: ignore[no-untyped-def]
    from backend.domain.adstudio.image_clients import FakeImageClient

    app = fake_app(app_factory)
    app.state.pipeline.image_client = FakeImageClient(block=True)
    async with client_for(app) as client:
        product_id = await upload_product(client, png(400, 400, (200, 90, 90)))
        run_id = (await start_run(client, product_id, f"k-{uuid.uuid4()}")).json()["run_id"]
        await app.state.runner.join(uuid.UUID(run_id))
        detail = (await client.get(f"/v1/runs/{run_id}")).json()
    assert detail["run"]["status"] == "needs_review"
    assert detail["candidates"][0]["status"] == "blocked"
    assert detail["approved_candidate_id"] is None


async def test_startup_recovery_marks_stale_runs_interrupted(app_factory) -> None:  # type: ignore[no-untyped-def]
    from datetime import UTC, datetime, timedelta

    from backend.db import repositories as repo
    from backend.domain.adstudio.pipeline import mark_interrupted
    from backend.jobs.runner import recover_interrupted

    app = fake_app(app_factory)
    sm = app.state.db.sessionmaker
    async with client_for(app) as client:
        product_id = await upload_product(client, png(420, 420, (5, 5, 90)))
    async with sm() as session:
        brief = await repo.create_brief(
            session,
            "demo",
            product_id=uuid.UUID(product_id),
            geography_code="AU",
            season="December",
            required_text="Hi",
            aspect_ratio="4:5",
        )
        stale = await repo.create_run(
            session,
            "demo",
            brief_id=brief.id,
            status="generating",
            heartbeat_at=datetime.now(UTC) - timedelta(minutes=5),
        )
        await session.commit()
    ids = await recover_interrupted(sm, lambda rid: mark_interrupted(app.state.pipeline, rid))
    assert stale.id in ids
    async with sm() as session:
        run = await repo.get_run(session, "demo", stale.id)
        events = await repo.list_run_events(session, stale.id)
    assert run is not None and run.status == "interrupted" and run.finished_at is not None
    assert [e.type for e in events] == ["run.error", "run.finished"]
    assert events[-1].payload["status"] == "interrupted"


async def test_run_plan_done_carries_season_and_422s(app_factory) -> None:  # type: ignore[no-untyped-def]
    app = fake_app(app_factory)
    async with client_for(app) as client:
        product_id = await upload_product(client)
        run_id = (await start_run(client, product_id, f"k-{uuid.uuid4()}")).json()["run_id"]
        events = parse_sse((await client.get(f"/v1/runs/{run_id}/events")).text)
        detail = (await client.get(f"/v1/runs/{run_id}")).json()
        bad_season = await start_run(client, product_id, f"k-{uuid.uuid4()}", season="blorp")
        bad_geo = await start_run(client, product_id, f"k-{uuid.uuid4()}", geography_code="XX")

    plan = next(e for e in events if e["type"] == "plan.done")["spec_summary"]
    assert (plan["effective_season"], plan["hemisphere"]) == ("summer", "south")
    assert "southern hemisphere" in plan["rationale"]
    assert plan["source"] == "default_table"  # no text-model key in tests
    assert detail["spec"]["required_text"]["raw"] == B01["required_text"]
    assert detail["run"]["config"]["resolution"]["season"]["effective_season"] == "summer"
    assert bad_season.status_code == 422 and bad_season.json()["type"] == "unknown-season"
    assert bad_geo.status_code == 422 and bad_geo.json()["type"] == "unknown-geography"


async def _run_to_end(app: FastAPI, client: httpx.AsyncClient, product_id: str) -> dict[str, Any]:
    run_id = (await start_run(client, product_id, f"k-{uuid.uuid4()}")).json()["run_id"]
    await app.state.runner.join(uuid.UUID(run_id))
    return (await client.get(f"/v1/runs/{run_id}")).json()


async def test_judge_down_fails_closed_to_needs_review(app_factory) -> None:  # type: ignore[no-untyped-def]
    from backend.domain.adstudio.vision import FakeVisionClient

    # 1. VISION_JUDGE is set but has no key (tests have none): unconfigured.
    app = fake_app(app_factory, vision_client="gemini")
    async with client_for(app) as client:
        product_id = await upload_product(client)
        unconfigured = await _run_to_end(app, client, product_id)
    # 2. A configured judge that times out mid-run.
    down = fake_app(app_factory)
    down.state.pipeline.evaluator.vision = FakeVisionClient(fail=TimeoutError())
    async with client_for(down) as client:
        product_id = await upload_product(client)
        outage = await _run_to_end(down, client, product_id)

    for detail in (unconfigured, outage):
        assert detail["run"]["status"] == "needs_review"  # never auto-approved
        assert detail["approved_candidate_id"] is None
        assert len(detail["candidates"]) == 2  # no repair is attempted on an unverified judge
        for cand in detail["candidates"]:
            dims = cand["evaluation"]["dimensions"]
            assert dims["product"] is None and dims["context"] is None  # unverified
            assert cand["evaluation"]["verdict"] in ("unverified", "fail")


async def test_product_profile_at_upload_and_lazily_at_run(app_factory) -> None:  # type: ignore[no-untyped-def]
    from backend.domain.adstudio.vision import FakeVisionClient

    photo = png(360, 480, (30, 90, 200))
    no_judge = fake_app(app_factory, vision_client="gemini")
    async with client_for(no_judge) as client:
        product_id = await upload_product(client, photo)
        before = (await client.get(f"/v1/products/{product_id}")).json()
    assert before["reference_facts"]["status"] == "unverified"

    judged = fake_app(app_factory)
    judge = FakeVisionClient()
    judged.state.vision = judge
    judged.state.pipeline.evaluator.vision = judge
    async with client_for(judged) as client:
        await _run_to_end(judged, client, product_id)  # computes the missing profile lazily
        after = (await client.get(f"/v1/products/{product_id}")).json()
        fresh_id = await upload_product(client, png(362, 480, (30, 90, 200)))  # at upload
        fresh = (await client.get(f"/v1/products/{fresh_id}")).json()
    for product in (after, fresh):
        assert product["reference_facts"]["status"] == "verified"
        assert product["facts_version"] == "pp-2+product_profile@2"
    assert judge.calls.count("profile") == 2


async def test_run_rejects_text_the_overlay_fonts_cannot_draw(app_factory) -> None:  # type: ignore[no-untyped-def]
    app = fake_app(app_factory)
    async with client_for(app) as client:
        product_id = await upload_product(client, png(300, 300, (90, 90, 20)))
        hebrew = await start_run(client, product_id, f"k-{uuid.uuid4()}", required_text="מבצע חורף")
        korean = await start_run(client, product_id, f"k-{uuid.uuid4()}", required_text="겨울 세일")
        hindi = await start_run(
            client, product_id, f"k-{uuid.uuid4()}", required_text="दिवाली सेल 20% छूट"
        )
        await app.state.runner.drain()
    assert hebrew.status_code == 422 and hebrew.json()["type"] == "unsupported-script"
    assert "U+05DE" in hebrew.json()["detail"]
    assert korean.status_code == 202  # ev-0.8: Korean, Thai and Arabic fonts are bundled
    assert hindi.status_code == 202
