"""`guarded_call`: the one wrapper every model call (text, image, audio, embeddings) uses.

Per-provider semaphore, timeout, retries on retryable errors with jittered backoff, circuit
breaker, optional cache, a ledger row and span attributes.
"""

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, cast

import httpx
import structlog
from opentelemetry import trace

from backend.core.errors import AppError
from backend.core.telemetry import current_trace_id
from backend.llm.breaker import BreakerRegistry
from backend.llm.cache import CacheStore
from backend.llm.ledger import CallRecord, MemoryRecorder, Recorder, current_run_id
from backend.llm.pricing import UnitType, estimate_cost

Kind = Literal["text", "image", "audio", "embedding", "other"]
RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
tracer = trace.get_tracer("backend.llm")
log = structlog.get_logger(__name__)


@dataclass
class Units:
    input: float = 0.0
    output: float = 0.0
    unit_type: UnitType = "items"
    served_model: str | None = None
    meta: dict[str, Any] = field(default_factory=dict[str, Any])


@dataclass
class CallRuntime:
    recorder: Recorder = field(default_factory=MemoryRecorder)
    cache: CacheStore | None = None
    breakers: BreakerRegistry = field(default_factory=BreakerRegistry)
    max_concurrency: int = 3
    timeout_s: float = 30.0
    max_retries: int = 2
    base_delay_s: float = 0.5
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    _semaphores: dict[str, asyncio.Semaphore] = field(default_factory=dict[str, asyncio.Semaphore])

    def semaphore(self, model: str) -> asyncio.Semaphore:
        provider = model.split(":", 1)[0]
        if provider not in self._semaphores:
            self._semaphores[provider] = asyncio.Semaphore(self.max_concurrency)
        return self._semaphores[provider]


_runtime = CallRuntime()


def set_runtime(runtime: CallRuntime) -> None:
    global _runtime
    _runtime = runtime


def get_runtime() -> CallRuntime:
    return _runtime


class SafetyBlockedError(Exception):
    """The provider refused on safety grounds: never retried, never trips the breaker."""

    def __init__(self, reason: str = "") -> None:
        super().__init__(reason or "blocked by provider safety filter")
        self.reason = reason


def is_timeout(exc: BaseException) -> bool:
    return isinstance(exc, TimeoutError | httpx.TimeoutException)


def is_retryable(exc: BaseException, *, retry_on_timeout: bool = True) -> bool:
    if isinstance(exc, SafetyBlockedError):
        return False
    if is_timeout(exc):
        return retry_on_timeout
    if isinstance(exc, httpx.TransportError):
        return True
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    return isinstance(status, int) and status in RETRYABLE_STATUS


def _request_id() -> str | None:
    value = structlog.contextvars.get_contextvars().get("request_id")
    return value if isinstance(value, str) else None


async def record_call(runtime: CallRuntime, rec: CallRecord) -> None:
    rec.request_id = rec.request_id or _request_id()
    rec.trace_id = rec.trace_id or current_trace_id()
    rec.run_id = rec.run_id or current_run_id.get()
    await runtime.recorder.record(rec)


async def guarded_call[T](
    kind: Kind,
    operation: str,
    model: str,
    fn: Callable[[], Awaitable[T]],
    *,
    version: str | None = None,
    prompt_name: str | None = None,
    cache_key: str | None = None,
    units: Callable[[T], Units] | None = None,
    encode: Callable[[T], Any] | None = None,
    decode: Callable[[Any], T] | None = None,
    runtime: CallRuntime | None = None,
    timeout_s: float | None = None,
    retry_on_timeout: bool = True,
) -> tuple[T, bool]:
    """Run `fn` under the guard. Returns (result, cached).

    `timeout_s` overrides the runtime's per-attempt timeout (image calls use `IMAGE_TIMEOUT_S`).
    Image calls pass `retry_on_timeout=False`: the provider may already have billed the generation
    we abandoned, so only 429/5xx/connection errors are retried. A `SafetyBlockedError` is never
    retried and does not count against the circuit breaker.
    """
    rt = runtime or get_runtime()
    timeout = timeout_s if timeout_s is not None else rt.timeout_s
    rec = CallRecord(
        kind=kind,
        operation=operation,
        requested_model=model,
        prompt_name=prompt_name,
        prompt_version=version,
    )
    started = time.perf_counter()
    with tracer.start_as_current_span(f"model_call {operation}") as span:
        span.set_attributes({"llm.kind": kind, "llm.requested_model": model})
        if cache_key and rt.cache is not None:
            hit = await rt.cache.get(cache_key)
            if hit is not None:
                value = decode(hit) if decode else cast(T, hit)
                rec.cached, rec.served_model = True, model
                rec.latency_ms = int((time.perf_counter() - started) * 1000)
                span.set_attribute("llm.cached", True)
                await record_call(rt, rec)
                return value, True

        breaker = rt.breakers.get(model)
        if not breaker.allow():
            rec.status, rec.error_type = "error", "breaker_open"
            await record_call(rt, rec)
            raise AppError(
                503,
                "model-unavailable",
                "Model temporarily unavailable",
                f"Circuit breaker open for {model}.",
            )

        attempt = 0
        while True:
            try:
                async with rt.semaphore(model), asyncio.timeout(timeout):
                    result = await fn()
                break
            except Exception as exc:
                retryable = is_retryable(exc, retry_on_timeout=retry_on_timeout)
                if retryable and attempt < rt.max_retries:
                    attempt += 1
                    delay = rt.base_delay_s * (2 ** (attempt - 1)) * (1 + random.random())  # noqa: S311
                    log.info("model_call_retry", operation=operation, attempt=attempt)
                    await rt.sleep(delay)
                    continue
                if isinstance(exc, SafetyBlockedError):
                    rec.status = "blocked"
                else:
                    breaker.record_failure()
                    rec.status = "error"
                rec.error_type = type(exc).__name__
                rec.latency_ms = int((time.perf_counter() - started) * 1000)
                rec.meta["attempts"] = attempt + 1
                span.record_exception(exc)
                await record_call(rt, rec)
                raise

        breaker.record_success()
        u = units(result) if units else Units()
        rec.served_model = u.served_model or model
        rec.fallback_used = rec.served_model != model
        rec.input_units, rec.output_units, rec.unit_type = (
            u.input,
            u.output,
            u.unit_type,
        )
        rec.est_cost_usd = estimate_cost(rec.served_model, u.input, u.output, u.unit_type)
        rec.latency_ms = int((time.perf_counter() - started) * 1000)
        rec.meta = {**u.meta, "attempts": attempt + 1}
        span.set_attributes(
            {"llm.served_model": rec.served_model, "llm.latency_ms": rec.latency_ms}
        )
        await record_call(rt, rec)
        if cache_key and rt.cache is not None:
            stored = encode(result) if encode else result
            await rt.cache.put(cache_key, kind=kind, model=model, version=version, value=stored)
        return result, False
