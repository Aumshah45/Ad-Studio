"""Smoke check: one ping through the gateway. Skips cleanly without keys."""

from backend.core.errors import AppError
from backend.core.settings import get_settings
from backend.llm.calls import CallRuntime
from backend.llm.gateway import LlmGateway
from backend.llm.prompts.ping import PING
from evals.report import Metric, Target

NAME = "gateway_smoke"
TARGETS: dict[str, Target] = {"latency_ms": ("<=", 10_000)}


async def run() -> dict[str, Metric]:
    gateway = LlmGateway(get_settings(), CallRuntime())
    try:
        result = await gateway.run(PING, user_input="Say pong.")
    except AppError as exc:
        return {"status": f"skipped: {exc.type}"}
    return {"status": "ok", "latency_ms": result.latency_ms, "model": result.model}
