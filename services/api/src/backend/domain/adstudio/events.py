"""The `RunEvent` union streamed over SSE and stored in `run_events` (architecture "API contract").

Every event carries `seq`, `run_id` and `at`. The stored payload is the event without `seq` and
`run_id` (those are the row's key), so a stored row round-trips back into the same event.
"""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from backend.domain.adstudio.evaluator.schemas import Dimension, Verdict

RunStatus = Literal[
    "queued",
    "planning",
    "generating",
    "evaluating",
    "repairing",
    "fallback",
    "passed",
    "needs_review",
    "failed",
    "interrupted",
    "cancelled",
    "approved",
    "rejected",
]
TerminalStatus = Literal["passed", "needs_review", "failed", "interrupted", "cancelled"]
Outcome = Literal["native", "overlay"]
# initial = a first-round candidate or a fresh regenerate; repair = a targeted edit of a parent;
# clean_plate = a fresh scene with an empty text zone; overlay = the deterministic text overlay.
CandidateKind = Literal["initial", "repair", "clean_plate", "overlay"]


def utcnow() -> datetime:
    return datetime.now(UTC)


def _added_later(*names: str) -> Callable[[dict[str, Any]], None]:
    """Keep fields added after the first contract optional in the JSON schema.

    Events set `json_schema_serialization_defaults_required`, which makes every field required in
    the generated TS types; fields added later (always present on the wire) stay optional so
    existing typed fixtures and clients keep compiling.
    """

    def extra(schema: dict[str, Any]) -> None:
        required = schema.get("required")
        if isinstance(required, list):
            schema["required"] = [n for n in cast(list[str], required) if n not in names]

    return extra


def _config(*added_later: str) -> ConfigDict:
    return ConfigDict(
        json_schema_serialization_defaults_required=True,
        json_schema_extra=_added_later(*added_later) if added_later else None,
    )


class _RunEventBase(BaseModel):
    model_config = _config()

    seq: int = Field(default=0, description="Per-run sequence; the SSE `id:`")
    run_id: uuid.UUID
    at: datetime = Field(default_factory=utcnow)


class NormBoxView(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float


class ProductScaleView(BaseModel):
    """ADR-007: how big the product should be (its longest side / the image height)."""

    framing: str = Field(description="close_up | medium | wide")
    scale_min: float
    scale_max: float
    size_cm: float = Field(description="The product's real-world largest dimension")
    size_class: str
    resting_surface: str | None = None
    scale_references: list[str] = Field(default_factory=list[str])


class SpecSummary(BaseModel):
    source: Literal["template", "planner", "default_table"]
    effective_season: str | None = None
    hemisphere: str | None = None
    months: list[int] = Field(default_factory=list[int])
    holidays: list[str] = Field(default_factory=list[str])
    rationale: str | None = None
    country_name: str | None = None
    locale_cues: list[str] = Field(default_factory=list[str])
    avoid: list[str] = Field(default_factory=list[str])
    text_zone: NormBoxView | None = None
    text_lines: list[str] = Field(default_factory=list[str])
    text_mode: Literal["native", "overlay_only"] = "native"
    planner_model: str | None = None
    product_scale: ProductScaleView | None = Field(
        default=None, description="Framing and product scale (null when the size is unknown)"
    )


class DimensionView(BaseModel):
    passed: bool | None = Field(description="null = unverified")
    reasons: list[str] = Field(default_factory=list[str])


class RunStatusEvent(_RunEventBase):
    type: Literal["run.status"] = "run.status"
    status: RunStatus


class PlanDoneEvent(_RunEventBase):
    type: Literal["plan.done"] = "plan.done"
    spec_summary: SpecSummary


class CandidateCreatedEvent(_RunEventBase):
    model_config = _config("parent_candidate_id")

    type: Literal["candidate.created"] = "candidate.created"
    candidate_id: uuid.UUID
    attempt: int
    slot: int
    kind: CandidateKind
    parent_candidate_id: uuid.UUID | None = Field(
        default=None, description="The candidate this one repairs or overlays (lineage)"
    )
    status: Literal["pending", "blocked"] = "pending"
    image_id: uuid.UUID | None = None
    image_url: str | None = None
    width: int | None = None
    height: int | None = None
    native_width: int | None = None
    native_height: int | None = None
    model: str
    cached: bool = False
    reason: str | None = None


class EvaluationDoneEvent(_RunEventBase):
    type: Literal["evaluation.done"] = "evaluation.done"
    candidate_id: uuid.UUID
    evaluation_id: uuid.UUID | None = None
    verdict: Verdict
    dimensions: dict[Dimension, DimensionView]


class RepairStartedEvent(_RunEventBase):
    model_config = _config("action", "row", "model", "attempt")

    type: Literal["repair.started"] = "repair.started"
    from_candidate_id: uuid.UUID
    target_dimension: Dimension
    instruction: str = Field(description="Summary of the edit (code-built, never VLM prose)")
    action: str | None = Field(
        default=None,
        description="regenerate | repair_product | repair_context | repair_composition | "
        "repair_text | clean_plate",
    )
    row: str | None = Field(default=None, description="Routing-table row (ai-design §4.3)")
    model: str | None = None
    attempt: int | None = None


class FallbackAppliedEvent(_RunEventBase):
    model_config = _config("from_candidate_id", "verification")

    type: Literal["fallback.applied"] = "fallback.applied"
    candidate_id: uuid.UUID
    reason: str
    from_candidate_id: uuid.UUID | None = None
    verification: Literal["ocr", "construction", "failed"] | None = Field(
        default=None, description="How the overlaid text was verified (ai-design §4.4 step 5)"
    )


class BudgetWarningEvent(_RunEventBase):
    model_config = _config("reason", "image_calls", "max_image_calls")

    type: Literal["budget.warning"] = "budget.warning"
    spent_usd: float
    cap_usd: float
    reason: Literal["threshold", "exhausted", "deadline"] = "threshold"
    image_calls: int | None = None
    max_image_calls: int | None = None


class RunFinishedEvent(_RunEventBase):
    model_config = _config("best_candidate_id")

    type: Literal["run.finished"] = "run.finished"
    status: TerminalStatus
    outcome: Outcome | None = None
    approved_candidate_id: uuid.UUID | None = None
    best_candidate_id: uuid.UUID | None = Field(
        default=None, description="needs_review: the candidate shown for the human decision"
    )
    cost_usd: float = 0.0
    latency_ms: int = 0
    reason: str | None = None


class RunErrorEvent(_RunEventBase):
    type: Literal["run.error"] = "run.error"
    problem: dict[str, Any]


RunEvent = Annotated[
    RunStatusEvent
    | PlanDoneEvent
    | CandidateCreatedEvent
    | EvaluationDoneEvent
    | RepairStartedEvent
    | FallbackAppliedEvent
    | BudgetWarningEvent
    | RunFinishedEvent
    | RunErrorEvent,
    Field(discriminator="type"),
]
run_event_adapter: TypeAdapter[RunEvent] = TypeAdapter(RunEvent)
FINISHED = "run.finished"


def event_payload(event: BaseModel) -> dict[str, Any]:
    return event.model_dump(mode="json", exclude={"seq", "run_id"})


def event_from_row(run_id: uuid.UUID, seq: int, payload: dict[str, Any]) -> RunEvent:
    return run_event_adapter.validate_python({**payload, "seq": seq, "run_id": run_id})
