"""Security baseline over HTTP (slice 25): dev-only LLM routes, per-IP run and upload limits,
and the startup refusal of non-Google image roles. Fake image/vision clients, no network."""

import asyncio
import uuid

import httpx
import pytest

from backend.http.app import create_app
from backend.llm.config import ModelRoleError
from tests.conftest import client_for, make_settings
from tests.images import png
from tests.integration.test_orchestrator import image_calls
from tests.integration.test_products import SVG
from tests.integration.test_runs import fake_app, start_run, upload_product


def _is_problem(res: httpx.Response, status: int, type_: str) -> None:
    assert res.status_code == status, res.text
    assert res.headers["content-type"].startswith("application/problem+json")
    assert res.json()["type"] == type_


async def _post_llm(app_factory, **overrides: object) -> tuple[httpx.Response, httpx.Response]:  # type: ignore[no-untyped-def]
    async with client_for(app_factory(**overrides)) as client:
        complete = await client.post("/v1/llm/complete", json={"prompt": "hi"})
        stream = await client.post("/v1/llm/stream", json={"prompt": "hi"})
    return complete, stream


async def test_llm_endpoints_disabled_in_prod(app_factory) -> None:  # type: ignore[no-untyped-def]
    # APP_ENV other than dev and no explicit flag: both open text proxies are a 404.
    for env in ("prod", "staging", "test"):
        complete, stream = await _post_llm(app_factory, app_env=env, llm_dev_routes=None)
        _is_problem(complete, 404, "not-found")
        _is_problem(stream, 404, "not-found")
    # Dev (the default APP_ENV) keeps them: the model is unconfigured here, so a 503, not a 404.
    complete, _ = await _post_llm(app_factory, app_env="dev", llm_dev_routes=None)
    _is_problem(complete, 503, "llm-unconfigured")
    # An explicit LLM_DEV_ROUTES wins either way.
    complete, _ = await _post_llm(app_factory, app_env="dev", llm_dev_routes=False)
    _is_problem(complete, 404, "not-found")
    complete, _ = await _post_llm(app_factory, app_env="prod", llm_dev_routes=True)
    _is_problem(complete, 503, "llm-unconfigured")


async def test_dev_llm_routes_404_outside_dev(app_factory) -> None:  # type: ignore[no-untyped-def]
    # production-readiness T3's id: same guard, checked on the settings default.
    assert make_settings(app_env="prod", llm_dev_routes=None).llm_dev_routes_enabled is False
    assert make_settings(app_env="dev", llm_dev_routes=None).llm_dev_routes_enabled is True
    complete, _ = await _post_llm(app_factory, app_env="production", llm_dev_routes=None)
    _is_problem(complete, 404, "not-found")


def test_startup_refuses_images_to_non_google_providers() -> None:
    for role, spec in (
        ("vision_judge", "openrouter:google/gemini-2.5-flash"),
        ("vision_judge", "groq:meta-llama/llama-4-scout-17b-16e-instruct"),
        ("image_model_candidate", "openrouter:google/gemini-3.1-flash-image"),
    ):
        with pytest.raises(ModelRoleError, match="only the billed Google project"):
            create_app(make_settings(**{role: spec}))


async def test_runs_rate_limit_per_ip_hourly(app_factory) -> None:  # type: ignore[no-untyped-def]
    app = fake_app(app_factory, runs_per_hour_per_ip=2)
    key = f"k-{uuid.uuid4()}"
    async with client_for(app) as client:
        product_id = await upload_product(client, png(512, 512, (10, 200, 30)))
        bad = await start_run(client, product_id, f"k-{uuid.uuid4()}", season="blorp")
        first = await start_run(client, product_id, key)
        second = await start_run(client, product_id, f"k-{uuid.uuid4()}")
        third = await start_run(client, product_id, f"k-{uuid.uuid4()}")
        replay = await start_run(client, product_id, key)
        await app.state.runner.drain()
    _is_problem(bad, 422, "unknown-season")  # a rejected brief does not use up the hour
    assert (first.status_code, second.status_code) == (202, 202)
    _is_problem(third, 429, "rate-limited")
    assert 3500 < int(third.headers["retry-after"]) <= 3600
    assert "2 runs per hour" in third.json()["detail"]
    # Replaying an accepted Idempotency-Key is not a new run, so it is never limited.
    assert replay.status_code == 200 and replay.json()["run_id"] == first.json()["run_id"]


async def test_runs_rate_limit_two_concurrent_per_ip(app_factory) -> None:  # type: ignore[no-untyped-def]
    app = fake_app(app_factory, runs_concurrent_per_ip=2, runs_per_hour_per_ip=10)
    gate = asyncio.Event()
    started: list[uuid.UUID] = []

    async def blocked(run_id: uuid.UUID) -> None:
        started.append(run_id)
        await gate.wait()

    app.state.runner.execute = blocked
    async with client_for(app) as client:
        product_id = await upload_product(client, png(512, 512, (10, 200, 30)))
        a = await start_run(client, product_id, f"k-{uuid.uuid4()}")
        b = await start_run(client, product_id, f"k-{uuid.uuid4()}")
        c = await start_run(client, product_id, f"k-{uuid.uuid4()}")
        gate.set()
        await app.state.runner.drain()
        d = await start_run(client, product_id, f"k-{uuid.uuid4()}")
        await app.state.runner.drain()
    assert (a.status_code, b.status_code) == (202, 202)
    _is_problem(c, 429, "rate-limited")
    assert c.headers["retry-after"] == "30" and "at once" in c.json()["detail"]
    assert d.status_code == 202  # both earlier runs finished: a slot is free again
    assert len(started) == 3


async def test_products_rate_limit_per_ip(app_factory) -> None:  # type: ignore[no-untyped-def]
    app = fake_app(app_factory, products_per_hour_per_ip=2)

    async def upload(client: httpx.AsyncClient, data: bytes) -> httpx.Response:
        return await client.post(
            "/v1/products", data={"name": "Mug"}, files={"image": ("p.png", data, "image/png")}
        )

    async with client_for(app) as client:
        rejected = await upload(client, SVG)
        ok = [await upload(client, png(300, 300, (i, 9, 9))) for i in (1, 2)]
        over = await upload(client, png(300, 300, (3, 9, 9)))
        listed = await client.get("/v1/products")  # reads are not limited
    _is_problem(rejected, 415, "unsupported-media-type")  # a rejected upload is not counted
    assert [r.status_code for r in ok] == [201, 201]
    _is_problem(over, 429, "rate-limited")
    assert int(over.headers["retry-after"]) > 0
    assert listed.status_code == 200


async def test_duplicate_brief_makes_zero_new_image_calls(app_factory) -> None:  # type: ignore[no-untyped-def]
    # T3 dedupe: a new Idempotency-Key for an identical brief is a new run, but every image step
    # is served from the content-keyed step cache, so it bills nothing new.
    app = fake_app(app_factory)
    fake = app.state.pipeline.image_client
    runs: list[str] = []
    calls_after: list[int] = []
    async with client_for(app) as client:
        product_id = await upload_product(client, png(512, 512, (60, 90, 200)))
        for _ in range(2):
            res = await start_run(client, product_id, f"k-{uuid.uuid4()}")
            assert res.status_code == 202, res.text
            runs.append(res.json()["run_id"])
            await app.state.runner.join(uuid.UUID(runs[-1]), timeout_s=120)
            calls_after.append(len(fake.calls))
    assert runs[0] != runs[1]
    assert calls_after[0] > 0 and calls_after[1] == calls_after[0]  # no new image-model call
    second = await image_calls(app, runs[1])
    assert second and all(row.cached for row in second)
