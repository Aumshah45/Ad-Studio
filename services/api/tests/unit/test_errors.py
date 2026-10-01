import pytest
from pydantic import SecretStr

from tests.conftest import client_for

PROBLEM = "application/problem+json"


async def test_413_body_too_large(app_factory) -> None:  # type: ignore[no-untyped-def]
    app = app_factory(max_body_bytes=10)
    async with client_for(app) as client:
        res = await client.post("/v1/llm/complete", json={"prompt": "x" * 100})
    assert res.status_code == 413
    assert res.headers["content-type"] == PROBLEM
    assert res.json()["type"] == "payload-too-large"
    assert res.json()["request_id"] == res.headers["x-request-id"]


async def test_422_validation(app_factory) -> None:  # type: ignore[no-untyped-def]
    async with client_for(app_factory()) as client:
        res = await client.post("/v1/llm/complete", json={"wrong": 1})
    body = res.json()
    assert res.status_code == 422 and res.headers["content-type"] == PROBLEM
    assert body["type"] == "validation-error" and body["errors"][0]["loc"][-1] == "prompt"


async def test_429_rate_limit(app_factory) -> None:  # type: ignore[no-untyped-def]
    async with client_for(app_factory(rate_limit_per_min=1)) as client:
        first = await client.get("/v1/meta/stream-events")
        second = await client.get("/v1/meta/stream-events")
        health = await client.get("/health")
    assert first.status_code == 200
    assert second.status_code == 429 and second.headers["content-type"] == PROBLEM
    assert int(second.headers["retry-after"]) >= 1
    assert health.status_code == 200


async def test_500_unhandled(app_factory) -> None:  # type: ignore[no-untyped-def]
    app = app_factory()

    async def boom() -> None:
        raise RuntimeError("secret internals")

    app.add_api_route("/boom", boom, tags=["test"])
    async with client_for(app, raise_app_exceptions=False) as client:
        res = await client.get("/boom")
    assert res.status_code == 500 and res.headers["content-type"] == PROBLEM
    assert "secret internals" not in res.text
    assert res.headers["x-content-type-options"] == "nosniff"


async def test_503_llm_unconfigured(app_factory) -> None:  # type: ignore[no-untyped-def]
    app = app_factory(google_api_key=SecretStr(""))
    async with client_for(app) as client:
        res = await client.post("/v1/llm/complete", json={"prompt": "hi"})
    assert res.status_code == 503 and res.json()["type"] == "llm-unconfigured"


@pytest.mark.parametrize("value", ["a,b", " a , b "])
def test_fallbacks_parse_comma_list(settings_factory, value: str) -> None:  # type: ignore[no-untyped-def]
    assert settings_factory(llm_fallbacks=value).llm_fallbacks == ["a", "b"]
