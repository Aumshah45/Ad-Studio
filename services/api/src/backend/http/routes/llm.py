"""Dev endpoints proving the gateway end to end: open text proxies to a paid model.

They answer only when `Settings.llm_dev_routes_enabled` (APP_ENV=dev, or LLM_DEV_ROUTES=true);
otherwise every call is a 404 problem+json (production-readiness T3). The routes stay in the
OpenAPI schema so the generated client does not depend on the environment.
Stream event payloads are typed by `/v1/meta/stream-events` (the `StreamEvent` union).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from backend.core.errors import AppError
from backend.core.settings import Settings
from backend.guardrails.input import check_input
from backend.http.deps import get_app_settings, get_gateway
from backend.http.sse import sse_response
from backend.llm.gateway import LlmGateway
from backend.llm.prompts.ping import PING


def require_dev_routes(settings: Annotated[Settings, Depends(get_app_settings)]) -> None:
    if not settings.llm_dev_routes_enabled:
        raise AppError(404, "not-found", "Not found", "This endpoint is available in dev only.")


router = APIRouter(
    prefix="/v1/llm",
    tags=["llm"],
    dependencies=[Depends(require_dev_routes)],
    responses={404: {"content": {"application/problem+json": {}}}},
)


class CompleteRequest(BaseModel):
    prompt: str = Field(min_length=1)


class CompleteResponse(BaseModel):
    text: str
    model: str
    cached: bool
    latency_ms: int
    request_id: str | None


@router.post("/complete")
async def complete(
    body: CompleteRequest,
    request: Request,
    gateway: Annotated[LlmGateway, Depends(get_gateway)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> CompleteResponse:
    text = check_input(body.prompt, settings.max_input_chars)
    result = await gateway.run(PING, user_input=text, cacheable=True)
    return CompleteResponse(
        text=result.output,
        model=result.model,
        cached=result.cached,
        latency_ms=result.latency_ms,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post(
    "/stream",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
)
async def stream(
    body: CompleteRequest,
    gateway: Annotated[LlmGateway, Depends(get_gateway)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> StreamingResponse:
    text = check_input(body.prompt, settings.max_input_chars)
    return sse_response(gateway.stream(PING, user_input=text))
