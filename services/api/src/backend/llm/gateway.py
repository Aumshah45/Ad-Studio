"""Text LLM gateway: pydantic-ai agents behind `guarded_call` (fallback, cache, ledger, budget)."""

import asyncio
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any, cast

from pydantic import TypeAdapter
from pydantic_ai import Agent, BinaryContent
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage

from backend.core.errors import AppError
from backend.core.settings import Settings
from backend.guardrails.budget import Budget
from backend.guardrails.context import UNTRUSTED_RULE, wrap_untrusted
from backend.http.sse import DoneEvent, ErrorEvent, StatusEvent, StreamEvent, TokenEvent
from backend.llm.cache import FileCache, cache_key
from backend.llm.calls import CallRuntime, Units, guarded_call, record_call
from backend.llm.config import (
    ModelSpec,
    build_chain,
    build_model,
    ensure_image_data_provider,
    is_configured,
    parse_spec,
)
from backend.llm.ledger import CallRecord
from backend.llm.pricing import estimate_cost
from backend.llm.prompts.base import Prompt
from backend.storage.blobs import sha256_hex

VISION_TEMPERATURE = 0.0


@dataclass
class GatewayResult[T]:
    output: T
    model: str
    cached: bool
    latency_ms: int
    input_tokens: int = 0
    output_tokens: int = 0


def image_media_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _served_spec(model_name: str | None, specs: list[ModelSpec]) -> str:
    if model_name:
        for spec in specs:
            if spec.raw == model_name or spec.name == model_name or model_name.endswith(spec.name):
                return spec.raw
        return model_name
    return specs[0].raw if specs else "unknown"


class LlmGateway:
    def __init__(
        self,
        settings: Settings,
        runtime: CallRuntime,
        model_override: Model | None = None,
        vision_override: Model | None = None,
    ) -> None:
        self.settings = settings
        self.runtime = runtime
        self.model_override = model_override
        self.vision_override = vision_override

    def _chain(self, judge: bool = False) -> tuple[Model, list[ModelSpec]]:
        if self.model_override is not None:
            name = getattr(self.model_override, "model_name", "override")
            return self.model_override, [parse_spec(f"test:{name}")]
        return build_chain(self.settings, judge=judge)

    def _user_prompt(self, user_input: str, wrap: bool) -> str:
        return wrap_untrusted("user_input", "user", user_input) if wrap else user_input

    async def run[T](
        self,
        prompt: Prompt,
        *,
        user_input: str,
        output_type: type[T] = str,
        cacheable: bool = False,
        budget: Budget | None = None,
        wrap: bool = True,
        judge: bool = False,
    ) -> GatewayResult[T]:
        model, specs = self._chain(judge)
        if budget:
            budget.charge(calls=1)
        agent = Agent(
            model,
            output_type=output_type,
            instructions=prompt.instructions(UNTRUSTED_RULE if wrap else ""),
            retries={"output": 1},
        )
        user_prompt = self._user_prompt(user_input, wrap)
        adapter: TypeAdapter[T] = TypeAdapter(output_type)
        key = (
            cache_key(
                prompt.name,
                prompt.version,
                [s.raw for s in specs],
                adapter.json_schema(),
                user_prompt,
            )
            if cacheable
            else None
        )
        started = time.perf_counter()
        usage_box: dict[str, Any] = {}

        async def call() -> T:
            result = await agent.run(user_prompt)
            usage_box["usage"] = result.usage
            usage_box["model"] = result.response.model_name
            return result.output

        def units(_: T) -> Units:
            usage = cast(RunUsage, usage_box["usage"])
            return Units(
                input=usage.input_tokens,
                output=usage.output_tokens,
                unit_type="tokens",
                served_model=_served_spec(usage_box.get("model"), specs),
            )

        output, cached = await guarded_call(
            "text",
            f"llm.{prompt.name}",
            specs[0].raw,
            call,
            version=prompt.version,
            prompt_name=prompt.name,
            cache_key=key,
            units=units,
            encode=lambda v: adapter.dump_python(v, mode="json"),
            decode=adapter.validate_python,
            runtime=self.runtime,
        )
        usage = cast(RunUsage | None, usage_box.get("usage"))
        if budget and usage:
            budget.charge(calls=0, units=usage.total_tokens)
        return GatewayResult(
            output=output,
            model=specs[0].raw if cached else _served_spec(usage_box.get("model"), specs),
            cached=cached,
            latency_ms=int((time.perf_counter() - started) * 1000),
            input_tokens=usage.input_tokens if usage else 0,
            output_tokens=usage.output_tokens if usage else 0,
        )

    # --- vision (image in, structured text out) ------------------------------------------------

    def vision_spec(self) -> ModelSpec | None:
        if self.vision_override is not None:
            name = getattr(self.vision_override, "model_name", "vision-override")
            return parse_spec(f"test:{name}")
        raw = self.settings.vision_judge.strip()
        return parse_spec(raw) if raw else None

    def replay_only(self) -> bool:
        return isinstance(self.runtime.cache, FileCache)

    def vision_configured(self) -> bool:
        """A vision model can answer: override, a keyed `VISION_JUDGE`, or a replay snapshot."""
        spec = self.vision_spec()
        if spec is None:
            return False
        return (
            self.vision_override is not None
            or is_configured(spec, self.settings)
            or self.replay_only()
        )

    async def run_vision[T](
        self,
        prompt: Prompt,
        *,
        images: Sequence[bytes],
        output_type: type[T],
        text: str = "",
        labels: Sequence[str] = (),
        key_parts: Sequence[Any] = (),
        budget: Budget | None = None,
    ) -> GatewayResult[T]:
        """One `VISION_JUDGE` call: images (+ code-built text) -> `output_type`, temperature 0.

        Always cached: the key holds the model, prompt name/version, output schema, the text, the
        sha256 of every image and `key_parts` (e.g. the evaluator version). A `FileCache` runtime
        replays without a key; a miss there raises `CacheMissError` (never a live call).
        """
        spec = self.vision_spec()
        if spec is None:
            raise AppError(
                503, "vision-unconfigured", "Vision judge not configured", "Set VISION_JUDGE."
            )
        if self.vision_override is None:
            ensure_image_data_provider(spec)  # D9: images reach only the billed Google project
        model: Model | None = self.vision_override or build_model(spec, self.settings)
        if model is None and not self.replay_only():
            raise AppError(
                503,
                "vision-unconfigured",
                "Vision judge not configured",
                f"No API key for {spec.raw}.",
            )
        if budget:
            budget.charge(calls=1)
        adapter: TypeAdapter[T] = TypeAdapter(output_type)
        key = cache_key(
            "vision",
            prompt.name,
            prompt.version,
            spec.raw,
            adapter.json_schema(),
            text,
            list(labels),
            [sha256_hex(img) for img in images],
            list(key_parts),
        )
        content: list[str | BinaryContent] = []
        for i, img in enumerate(images):
            content.append(labels[i] if i < len(labels) else f"Image {i + 1}:")
            content.append(BinaryContent(data=img, media_type=image_media_type(img)))
        if text:
            content.append(text)
        started = time.perf_counter()
        usage_box: dict[str, Any] = {}

        async def call() -> T:
            if model is None:  # replay-only runtime without a key: the cache must answer
                raise AppError(503, "vision-unconfigured", "Vision judge not configured")
            agent = Agent(
                model,
                output_type=output_type,
                instructions=prompt.instructions(),
                retries={"output": 1},
            )
            result = await agent.run(content, model_settings={"temperature": VISION_TEMPERATURE})
            usage_box["usage"] = result.usage
            return result.output

        def units(_: T) -> Units:
            usage = cast(RunUsage, usage_box["usage"])
            return Units(
                input=usage.input_tokens,
                output=usage.output_tokens,
                unit_type="tokens",
                served_model=spec.raw,
                meta={"images": len(images)},
            )

        output, cached = await guarded_call(
            "text",
            f"vision.{prompt.name}",
            spec.raw,
            call,
            version=prompt.version,
            prompt_name=prompt.name,
            cache_key=key,
            units=units,
            encode=lambda v: adapter.dump_python(v, mode="json"),
            decode=adapter.validate_python,
            runtime=self.runtime,
        )
        usage = cast(RunUsage | None, usage_box.get("usage"))
        if budget and usage:
            budget.charge(calls=0, units=usage.total_tokens)
        return GatewayResult(
            output=output,
            model=spec.raw,
            cached=cached,
            latency_ms=int((time.perf_counter() - started) * 1000),
            input_tokens=usage.input_tokens if usage else 0,
            output_tokens=usage.output_tokens if usage else 0,
        )

    async def stream(
        self, prompt: Prompt, *, user_input: str, wrap: bool = True
    ) -> AsyncIterator[StreamEvent]:
        """Stream text as SSE events: status, token*, done — or error. Never raises."""
        yield StatusEvent(message="Calling model")
        try:
            model, specs = self._chain()
        except AppError as exc:
            yield ErrorEvent(code=exc.type, message=exc.detail or exc.title)
            return
        requested = specs[0].raw
        breaker = self.runtime.breakers.get(requested)
        rec = CallRecord(
            kind="text",
            operation=f"llm.{prompt.name}.stream",
            requested_model=requested,
            prompt_name=prompt.name,
            prompt_version=prompt.version,
            unit_type="tokens",
        )
        if not breaker.allow():
            yield ErrorEvent(
                code="model-unavailable",
                message=f"Circuit breaker open for {requested}.",
            )
            return
        agent = Agent(model, instructions=prompt.instructions(UNTRUSTED_RULE if wrap else ""))
        started = time.perf_counter()
        try:
            async with (
                self.runtime.semaphore(requested),
                asyncio.timeout(self.runtime.timeout_s),
            ):
                async with agent.run_stream(self._user_prompt(user_input, wrap)) as result:
                    async for delta in result.stream_text(delta=True, debounce_by=None):
                        if delta:
                            yield TokenEvent(text=delta)
                    usage = result.usage
                    served = _served_spec(result.response.model_name, specs)
        except Exception as exc:  # noqa: BLE001 - reported as an event and in the ledger
            breaker.record_failure()
            rec.status, rec.error_type = "error", type(exc).__name__
            rec.latency_ms = int((time.perf_counter() - started) * 1000)
            await record_call(self.runtime, rec)
            yield ErrorEvent(
                code="model-error", message=f"Model call failed ({type(exc).__name__})."
            )
            return
        breaker.record_success()
        rec.served_model, rec.fallback_used = served, served != requested
        rec.input_units, rec.output_units = usage.input_tokens, usage.output_tokens
        rec.est_cost_usd = estimate_cost(served, rec.input_units, rec.output_units, "tokens")
        rec.latency_ms = int((time.perf_counter() - started) * 1000)
        await record_call(self.runtime, rec)
        yield DoneEvent(model=served, latency_ms=rec.latency_ms, cached=False)
