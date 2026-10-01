from pydantic_ai.models.test import TestModel
from sqlalchemy import func, select

from backend.db.models_calls import ModelCall
from backend.http.deps import get_gateway
from backend.llm.gateway import LlmGateway
from tests.conftest import client_for


async def test_health(app_factory) -> None:  # type: ignore[no-untyped-def]
    async with client_for(app_factory()) as client:
        res = await client.get("/health")
    assert res.json() == {"status": "ok"}
    assert res.headers["x-frame-options"] == "DENY"


async def test_ready_degraded_without_keys(app_factory) -> None:  # type: ignore[no-untyped-def]
    async with client_for(app_factory()) as client:
        res = await client.get("/ready")
    body = res.json()
    assert res.status_code == 200 and body["status"] == "degraded"
    assert body["db"] is True and all(body["extensions"].values())


async def test_ready_ok_with_test_model(app_factory) -> None:  # type: ignore[no-untyped-def]
    async with client_for(app_factory(llm_primary="test:pong")) as client:
        res = await client.get("/ready")
    assert res.status_code == 200 and res.json()["status"] == "ok"


async def test_ready_503_when_db_down(app_factory) -> None:  # type: ignore[no-untyped-def]
    app = app_factory(database_url="postgresql+psycopg://localhost:1/nope")
    async with client_for(app) as client:
        res = await client.get("/ready")
    assert res.status_code == 503 and res.json()["status"] == "unavailable"


async def test_complete_writes_ledger_row_and_summary(app_factory) -> None:  # type: ignore[no-untyped-def]
    app = app_factory()
    gateway = LlmGateway(
        app.state.settings, app.state.runtime, model_override=TestModel(custom_output_text="pong")
    )
    app.dependency_overrides[get_gateway] = lambda: gateway
    async with client_for(app) as client:
        res = await client.post("/v1/llm/complete", json={"prompt": "integration ping"})
        again = await client.post("/v1/llm/complete", json={"prompt": "integration ping"})
        summary = await client.get("/v1/ops/summary", params={"window": 50})
    body = res.json()
    assert res.status_code == 200 and body["text"] == "pong" and body["cached"] is False
    assert body["request_id"] == res.headers["x-request-id"]
    assert again.json()["cached"] is True
    async with app.state.db.sessionmaker() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(ModelCall)
            .where(ModelCall.request_id == body["request_id"])
        )
    assert count == 1
    s = summary.json()
    assert s["count"] >= 2 and s["by_kind"]["text"] >= 2 and s["cache_hit_rate"] > 0


async def test_stream_sse_framing(app_factory) -> None:  # type: ignore[no-untyped-def]
    app = app_factory()
    gateway = LlmGateway(
        app.state.settings, app.state.runtime, model_override=TestModel(custom_output_text="a b")
    )
    app.dependency_overrides[get_gateway] = lambda: gateway
    async with client_for(app) as client:
        res = await client.post("/v1/llm/stream", json={"prompt": "hi"})
    assert res.headers["content-type"].startswith("text/event-stream")
    frames = [f for f in res.text.split("\n\n") if f.strip()]
    assert frames[0].startswith("event: status\ndata: ")
    assert any(f.startswith("event: token\n") for f in frames)
    assert frames[-1].startswith("event: done\n")


async def test_stream_unconfigured_emits_error(app_factory) -> None:  # type: ignore[no-untyped-def]
    async with client_for(app_factory()) as client:
        res = await client.post("/v1/llm/stream", json={"prompt": "hi"})
    frames = [f for f in res.text.split("\n\n") if f.strip()]
    assert [f.split("\n")[0] for f in frames] == ["event: status", "event: error"]
