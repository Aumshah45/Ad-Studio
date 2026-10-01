"""OpenTelemetry -> OTLP (Arize Phoenix). Never crashes the app if Phoenix is down."""

import structlog
from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import (  # pyright: ignore[reportMissingTypeStubs]
    FastAPIInstrumentor,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from pydantic_ai import Agent

from backend.core.settings import Settings

log = structlog.get_logger(__name__)
_provider: TracerProvider | None = None


def setup_telemetry(app: FastAPI, settings: Settings) -> None:
    global _provider
    if not settings.otel_enabled:
        return
    try:
        if _provider is None:
            resource = Resource.create(
                {
                    "service.name": settings.project_name,
                    "openinference.project.name": settings.project_name,
                }
            )
            _provider = TracerProvider(resource=resource)
            exporter = OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint)
            _provider.add_span_processor(BatchSpanProcessor(exporter))
            trace.set_tracer_provider(_provider)
            Agent.instrument_all()
        FastAPIInstrumentor.instrument_app(app, tracer_provider=_provider)
    except Exception as exc:  # noqa: BLE001 - telemetry must never break the app
        log.warning("telemetry_setup_failed", error=str(exc))


def current_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None
