from pydantic import BaseModel
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from backend.http.sse import DoneEvent, ErrorEvent, StatusEvent, TokenEvent
from backend.llm.calls import CallRuntime
from backend.llm.gateway import LlmGateway
from backend.llm.ledger import MemoryRecorder
from backend.llm.prompts.ping import PING
from tests.conftest import make_settings


def _boom(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
    raise ModelHTTPError(503, "primary", "unavailable")


async def test_fallback_serves_when_primary_fails() -> None:
    model = FallbackModel(
        FunctionModel(_boom, model_name="primary"), TestModel(custom_output_text="pong")
    )
    recorder = MemoryRecorder()
    gateway = LlmGateway(make_settings(), CallRuntime(recorder=recorder), model_override=model)
    result = await gateway.run(PING, user_input="ping")
    assert result.output == "pong"
    assert recorder.records[0].status == "ok"


class Verdict(BaseModel):
    ok: bool
    reason: str


async def test_typed_output() -> None:
    gateway = LlmGateway(make_settings(), CallRuntime(), model_override=TestModel())
    result = await gateway.run(PING, user_input="judge", output_type=Verdict)
    assert isinstance(result.output, Verdict)


async def test_unconfigured_stream_emits_status_then_error() -> None:
    gateway = LlmGateway(make_settings(), CallRuntime())
    events = [e async for e in gateway.stream(PING, user_input="hi")]
    assert isinstance(events[0], StatusEvent)
    assert isinstance(events[1], ErrorEvent) and events[1].code == "llm-unconfigured"


async def test_stream_tokens_then_done() -> None:
    gateway = LlmGateway(
        make_settings(), CallRuntime(), model_override=TestModel(custom_output_text="a b c")
    )
    events = [e async for e in gateway.stream(PING, user_input="hi")]
    assert any(isinstance(e, TokenEvent) for e in events)
    assert isinstance(events[-1], DoneEvent)
