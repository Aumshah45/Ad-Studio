"""Ledger recorders for `model_calls` rows."""

import uuid
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models_calls import ModelCall

log = structlog.get_logger(__name__)

# The run whose work is in progress; `record_call` stamps it on every ledger row so cost and
# latency roll up exactly per run (architecture "Scaffold changes" 1).
current_run_id: ContextVar[uuid.UUID | None] = ContextVar("current_run_id", default=None)


@contextmanager
def bind_run(run_id: uuid.UUID | None) -> Generator[None]:
    token = current_run_id.set(run_id)
    try:
        yield
    finally:
        current_run_id.reset(token)


@dataclass
class CallRecord:
    kind: str
    operation: str
    requested_model: str
    served_model: str | None = None
    run_id: uuid.UUID | None = None
    request_id: str | None = None
    trace_id: str | None = None
    prompt_name: str | None = None
    prompt_version: str | None = None
    fallback_used: bool = False
    cached: bool = False
    input_units: float = 0.0
    output_units: float = 0.0
    unit_type: str = "items"
    latency_ms: int = 0
    est_cost_usd: float = 0.0
    status: str = "ok"
    error_type: str | None = None
    meta: dict[str, Any] = field(default_factory=dict[str, Any])


class Recorder(Protocol):
    async def record(self, rec: CallRecord) -> None: ...


class MemoryRecorder:
    def __init__(self) -> None:
        self.records: list[CallRecord] = []

    async def record(self, rec: CallRecord) -> None:
        self.records.append(rec)


class DbRecorder:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self.sessionmaker = sessionmaker

    async def record(self, rec: CallRecord) -> None:
        try:
            async with self.sessionmaker() as session:
                session.add(ModelCall(**asdict(rec)))
                await session.commit()
        except Exception as exc:  # noqa: BLE001 - the ledger must never fail a model call
            log.warning("ledger_write_failed", error=str(exc))
