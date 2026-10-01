import asyncio
import uuid

import pytest

from backend.core.errors import AppError
from backend.llm.breaker import BreakerRegistry
from backend.llm.cache import MemoryCache
from backend.llm.calls import CallRuntime, SafetyBlockedError, Units, guarded_call
from backend.llm.ledger import MemoryRecorder, bind_run


class StatusError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"status {status_code}")
        self.status_code = status_code


async def _no_sleep(_: float) -> None:
    return None


def runtime(**kw: object) -> CallRuntime:
    rt = CallRuntime(recorder=MemoryRecorder(), sleep=_no_sleep, **kw)  # type: ignore[arg-type]
    return rt


async def test_retries_on_429_then_succeeds() -> None:
    rt = runtime()
    calls = 0

    async def fn() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise StatusError(429)
        return "ok"

    result, cached = await guarded_call("image", "gen", "google:img", fn, runtime=rt)
    assert (result, cached, calls) == ("ok", False, 2)


async def test_no_retry_on_400() -> None:
    rt = runtime()
    calls = 0

    async def fn() -> str:
        nonlocal calls
        calls += 1
        raise StatusError(400)

    with pytest.raises(StatusError):
        await guarded_call("image", "gen", "google:img", fn, runtime=rt)
    assert calls == 1
    recorder = rt.recorder
    assert isinstance(recorder, MemoryRecorder)
    assert recorder.records[0].status == "error"
    assert recorder.records[0].error_type == "StatusError"


async def test_breaker_opens() -> None:
    rt = runtime(breakers=BreakerRegistry(threshold=2, reset_s=60))

    async def fn() -> str:
        raise StatusError(400)

    for _ in range(2):
        with pytest.raises(StatusError):
            await guarded_call("text", "op", "groq:m", fn, runtime=rt)
    with pytest.raises(AppError) as err:
        await guarded_call("text", "op", "groq:m", fn, runtime=rt)
    assert err.value.type == "model-unavailable"
    assert rt.breakers.get("groq:m").state == "open"


def test_breaker_half_open_after_reset() -> None:
    now = [0.0]
    reg = BreakerRegistry(threshold=1, reset_s=30, clock=lambda: now[0])
    breaker = reg.get("m")
    breaker.record_failure()
    assert breaker.state == "open"
    now[0] = 31
    assert breaker.state == "half_open" and breaker.allow()
    breaker.record_success()
    assert breaker.state == "closed"


async def test_cache_hit_on_repeat() -> None:
    rt = runtime(cache=MemoryCache())
    calls = 0

    async def fn() -> dict[str, int]:
        nonlocal calls
        calls += 1
        return {"n": 1}

    first = await guarded_call("text", "op", "test:m", fn, cache_key="k", runtime=rt)
    second = await guarded_call("text", "op", "test:m", fn, cache_key="k", runtime=rt)
    assert first == ({"n": 1}, False)
    assert second == ({"n": 1}, True)
    assert calls == 1


async def test_ledger_row_fields() -> None:
    rt = runtime()

    async def fn() -> bytes:
        return b"png"

    await guarded_call(
        "image",
        "ad.generate",
        "google:gemini-3.1-flash-image",
        fn,
        version="3",
        prompt_name="ad",
        units=lambda _: Units(
            input=0, output=1, unit_type="images", served_model="google:gemini-3.1-flash-lite-image"
        ),
        runtime=rt,
    )
    recorder = rt.recorder
    assert isinstance(recorder, MemoryRecorder)
    rec = recorder.records[0]
    assert rec.kind == "image" and rec.operation == "ad.generate"
    assert rec.prompt_name == "ad" and rec.prompt_version == "3"
    assert rec.served_model == "google:gemini-3.1-flash-lite-image" and rec.fallback_used
    assert rec.unit_type == "images" and rec.output_units == 1
    assert rec.est_cost_usd == pytest.approx(0.034)
    assert rec.status == "ok" and rec.latency_ms >= 0


async def test_image_call_no_retry_on_timeout() -> None:
    rt = runtime()
    calls = 0

    async def slow() -> bytes:
        nonlocal calls
        calls += 1
        await asyncio.sleep(1)
        return b"png"

    with pytest.raises(TimeoutError):
        await guarded_call(
            "image",
            "ad.generate",
            "google:img",
            slow,
            timeout_s=0.01,
            retry_on_timeout=False,
            runtime=rt,
        )
    assert calls == 1
    recorder = rt.recorder
    assert isinstance(recorder, MemoryRecorder)
    assert recorder.records[0].error_type == "TimeoutError"
    assert recorder.records[0].meta["attempts"] == 1


async def test_image_call_still_retries_on_503() -> None:
    rt = runtime()
    calls = 0

    async def fn() -> bytes:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise StatusError(503)
        return b"png"

    result = await guarded_call(
        "image", "ad.generate", "google:img", fn, timeout_s=5, retry_on_timeout=False, runtime=rt
    )
    assert result == (b"png", False) and calls == 3


async def test_text_call_timeout_override_retries_by_default() -> None:
    rt = runtime()
    calls = 0

    async def slow() -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(1)
        return "x"

    with pytest.raises(TimeoutError):
        await guarded_call("text", "op", "groq:m", slow, timeout_s=0.01, runtime=rt)
    assert calls == rt.max_retries + 1


async def test_safety_block_not_retried() -> None:
    rt = runtime(breakers=BreakerRegistry(threshold=1, reset_s=60))
    calls = 0

    async def blocked() -> bytes:
        nonlocal calls
        calls += 1
        raise SafetyBlockedError("SAFETY")

    with pytest.raises(SafetyBlockedError):
        await guarded_call("image", "ad.generate", "google:img", blocked, runtime=rt)
    assert calls == 1
    assert rt.breakers.get("google:img").state == "closed"
    recorder = rt.recorder
    assert isinstance(recorder, MemoryRecorder)
    assert recorder.records[0].status == "blocked"


async def test_ledger_records_run_id() -> None:
    rt = runtime()
    run_id = uuid.uuid4()

    async def fn() -> str:
        return "ok"

    with bind_run(run_id):
        await guarded_call("text", "op", "test:m", fn, runtime=rt)
    await guarded_call("text", "op", "test:m", fn, runtime=rt)
    recorder = rt.recorder
    assert isinstance(recorder, MemoryRecorder)
    assert [r.run_id for r in recorder.records] == [run_id, None]
