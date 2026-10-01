"""Shared fixtures. No network: keys are blank and models are TestModel/FunctionModel."""

import os
import tempfile
from collections.abc import AsyncIterator, Callable

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from backend.core.settings import Settings
from backend.http.app import create_app
from tests import netguard

TEST_BLOB_DIR = tempfile.mkdtemp(prefix="ad-studio-blobs-")
TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://localhost:5432/ad_studio_test"
)


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:  # pyright: ignore[reportUnusedFunction]
    """No test may reach the network: non-loopback connects and DNS lookups raise."""
    netguard.install(monkeypatch)


def make_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "app_env": "test",
        "database_url": TEST_DB_URL,
        "llm_primary": "google:gemini-test",
        "llm_fallbacks": [],
        "llm_judge": "",
        "google_api_key": SecretStr(""),
        "groq_api_key": SecretStr(""),
        "openrouter_api_key": SecretStr(""),
        "otel_enabled": False,
        "rate_limit_per_min": 1000,
        # Per-IP run/upload limits off by default in tests; the limit tests switch them on.
        "runs_per_hour_per_ip": 0,
        "runs_concurrent_per_ip": 0,
        "products_per_hour_per_ip": 0,
        "llm_dev_routes": True,  # test_llm_endpoints_disabled_in_prod turns them off
        "blob_dir": TEST_BLOB_DIR,
        "fake_image_script": "",  # a developer's demo script in .env must not leak into tests
        # Tesseract (+ scripted read-backs) only: identical behaviour on macOS and Linux CI. The
        # Apple Vision engine has its own tests (test_ocr_ensemble.py).
        "ocr_apple_vision": False,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


@pytest.fixture
def settings_factory() -> Callable[..., Settings]:
    return make_settings


@pytest.fixture
async def app_factory() -> AsyncIterator[Callable[..., FastAPI]]:
    apps: list[FastAPI] = []

    def factory(**overrides: object) -> FastAPI:
        app = create_app(make_settings(**overrides))
        apps.append(app)
        return app

    yield factory
    for app in apps:
        await app.state.runner.shutdown()
        await app.state.db.dispose()


def client_for(app: FastAPI, raise_app_exceptions: bool = True) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions)
    return httpx.AsyncClient(transport=transport, base_url="http://test")
