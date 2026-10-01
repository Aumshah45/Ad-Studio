"""Server-sent events: the `StreamEvent` union and a keep-alive SSE response."""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from starlette.responses import StreamingResponse

KEEPALIVE_S = 15.0


class _Event(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)


class StatusEvent(_Event):
    type: Literal["status"] = "status"
    message: str


class TokenEvent(_Event):
    type: Literal["token"] = "token"
    text: str


class StepEvent(_Event):
    type: Literal["step"] = "step"
    name: str
    state: Literal["started", "done", "failed", "skipped"]
    detail: str | None = None


class ResultEvent(_Event):
    type: Literal["result"] = "result"
    data: Any


class ErrorEvent(_Event):
    type: Literal["error"] = "error"
    code: str
    message: str


class DoneEvent(_Event):
    type: Literal["done"] = "done"
    model: str | None = None
    latency_ms: int = 0
    cached: bool = False


StreamEvent = Annotated[
    StatusEvent | TokenEvent | StepEvent | ResultEvent | ErrorEvent | DoneEvent,
    Field(discriminator="type"),
]
stream_event_adapter: TypeAdapter[StreamEvent] = TypeAdapter(StreamEvent)


def format_event(event: BaseModel) -> str:
    """One SSE frame; an event with an integer `seq` gets an `id:` line (Last-Event-ID resume)."""
    etype = str(getattr(event, "type", "message"))
    seq = getattr(event, "seq", None)
    id_line = f"id: {seq}\n" if isinstance(seq, int) and not isinstance(seq, bool) else ""
    return f"{id_line}event: {etype}\ndata: {event.model_dump_json()}\n\n"


_END = object()


def sse_response(events: AsyncIterator[Any]) -> StreamingResponse:
    """One producer task drains `events` into a queue; the response loop adds keep-alives."""

    async def body() -> AsyncIterator[str]:
        queue: asyncio.Queue[object] = asyncio.Queue()

        async def produce() -> None:
            try:
                async for event in events:
                    await queue.put(event)
            except Exception as exc:  # noqa: BLE001 - surface as an SSE error event
                await queue.put(ErrorEvent(code="internal-error", message=type(exc).__name__))
            finally:
                await queue.put(_END)

        producer = asyncio.create_task(produce())
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_S)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if item is _END:
                    break
                assert isinstance(item, BaseModel)  # noqa: S101
                yield format_event(item)
        finally:
            if not producer.done():
                producer.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await producer

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
