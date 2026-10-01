"""Application factory."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute

from backend.core.errors import install_error_handlers
from backend.core.logging import configure_logging
from backend.core.settings import Settings, get_settings
from backend.core.telemetry import setup_telemetry
from backend.db.session import create_database
from backend.domain.adstudio.evaluator.core import JUDGE_CACHE_VERSION, Evaluator
from backend.domain.adstudio.evaluator.ocr import TesseractOcr, second_engine
from backend.domain.adstudio.image_clients import build_image_client
from backend.domain.adstudio.pipeline import PipelineDeps, execute_run, mark_interrupted
from backend.domain.adstudio.planner import Planner
from backend.domain.adstudio.vision import build_vision_client
from backend.http.limits import IpRateLimiter
from backend.http.middleware import (
    BodySizeLimitMiddleware,
    RateLimitMiddleware,
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
)
from backend.http.routes import evals, golden, health, images, llm, meta, ops, products, runs
from backend.jobs.runner import RunRunner, recover_interrupted
from backend.llm.cache import DbCache
from backend.llm.calls import CallRuntime, set_runtime
from backend.llm.config import validate_model_roles
from backend.llm.gateway import LlmGateway
from backend.llm.ledger import DbRecorder
from backend.storage.blobs import BlobStore


def _operation_id(route: APIRoute) -> str:
    return f"{route.tags[0]}_{route.name}"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    validate_model_roles(settings)  # startup fails if a vision role maps to a text-only model
    configure_logging("DEBUG" if settings.app_env == "dev" else "INFO")
    db = create_database(settings.database_url)
    runtime = CallRuntime(
        recorder=DbRecorder(db.sessionmaker),
        cache=DbCache(db.sessionmaker),
        max_concurrency=settings.llm_max_concurrency,
        timeout_s=settings.llm_timeout_s,
    )
    set_runtime(runtime)

    def _warm_ocr(deps: PipelineDeps) -> "asyncio.Task[None] | None":
        """Apple Vision loads its models on the first request (about 25 s cold): do it now, in
        the background, so the first evaluation does not pay for it."""
        engine = deps.evaluator.ocr2
        warm = getattr(engine, "warm", None)
        if warm is None:
            return None

        async def run() -> None:
            try:
                await asyncio.to_thread(warm)
            except Exception as exc:  # noqa: BLE001 - a failed warm-up only costs latency later
                structlog.get_logger(__name__).warning("ocr_warm_failed", error=type(exc).__name__)

        return asyncio.create_task(run())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        deps: PipelineDeps = app.state.pipeline
        try:
            await recover_interrupted(db.sessionmaker, lambda rid: mark_interrupted(deps, rid))
        except Exception as exc:  # noqa: BLE001 - a DB outage must not stop the API starting
            structlog.get_logger(__name__).warning("run_recovery_failed", error=type(exc).__name__)
        warm = _warm_ocr(deps)
        yield
        if warm is not None and not warm.done():
            warm.cancel()
        await app.state.runner.shutdown()
        await app.state.db.dispose()

    app = FastAPI(
        title=settings.project_name,
        version="0.1.0",
        lifespan=lifespan,
        generate_unique_id_function=_operation_id,
    )
    app.state.settings = settings
    app.state.db = db
    app.state.runtime = runtime
    app.state.gateway = LlmGateway(settings, runtime)
    app.state.blobs = BlobStore(settings.blob_root)
    app.state.image_client = build_image_client(settings)
    app.state.vision = build_vision_client(
        settings, app.state.gateway, cache_version=JUDGE_CACHE_VERSION
    )
    pipeline = PipelineDeps(
        settings=settings,
        sessionmaker=db.sessionmaker,
        blobs=app.state.blobs,
        runtime=runtime,
        image_client=app.state.image_client,
        gateway=app.state.gateway,
        evaluator=Evaluator(
            TesseractOcr(runtime),
            vision=app.state.vision,
            ocr2=second_engine(runtime, settings.ocr_apple_vision),
        ),
        planner=Planner(app.state.gateway),
    )
    app.state.pipeline = pipeline
    app.state.runner = RunRunner(
        lambda run_id: execute_run(run_id, app.state.pipeline),
        sessionmaker=db.sessionmaker,
        max_concurrent=settings.runs_max_concurrent,
        queue_cap=settings.runs_queue_cap,
    )

    app.state.run_limiter = IpRateLimiter(
        name="runs",
        per_hour=settings.runs_per_hour_per_ip,
        concurrent=settings.runs_concurrent_per_ip,
        is_active=app.state.runner.has_task,
    )
    app.state.product_limiter = IpRateLimiter(
        name="uploads", per_hour=settings.products_per_hour_per_ip
    )
    if settings.llm_dev_routes_enabled and settings.app_env not in ("dev", "test"):
        structlog.get_logger(__name__).warning("llm_dev_routes_enabled", app_env=settings.app_env)

    install_error_handlers(app)
    # Added last = outermost: request id wraps everything so errors carry it.
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RateLimitMiddleware, per_minute=settings.rate_limit_per_min)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_body_bytes)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "Retry-After"],
    )
    app.add_middleware(RequestIdMiddleware)

    for router in (
        health.router,
        llm.router,
        ops.router,
        meta.router,
        products.router,
        images.router,
        runs.router,
        evals.router,
        golden.router,
    ):
        app.include_router(router)
    setup_telemetry(app, settings)
    return app
