"""Expose the SSE event unions in OpenAPI so the TS client gets their types."""

from fastapi import APIRouter

from backend.domain.adstudio.events import RunEvent
from backend.http.sse import StreamEvent

router = APIRouter(prefix="/v1/meta", tags=["meta"])


@router.get("/stream-events", response_model=list[StreamEvent])
async def stream_events() -> list[StreamEvent]:
    return []


@router.get("/run-events", response_model=list[RunEvent])
async def run_events() -> list[RunEvent]:
    """Schema carrier for the `GET /v1/runs/{id}/events` payloads (always empty)."""
    return []
