"""API schemas for products, images and runs (the contract the web client is generated from)."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.db import models_adstudio as m


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None = Field(
        default=None, description="Opaque; pass as `cursor` to get the next page."
    )


def image_url(image_id: uuid.UUID) -> str:
    return f"/v1/images/{image_id}"


class Product(BaseModel):
    id: uuid.UUID
    name: str
    image_id: uuid.UUID
    image_url: str
    width: int
    height: int
    reference_facts: dict[str, Any] | None = Field(
        default=None, description="Vision product profile; null until it has been extracted."
    )
    facts_version: str | None = None
    created_at: datetime

    @classmethod
    def from_rows(cls, product: m.Product, image: m.Image) -> "Product":
        return cls(
            id=product.id,
            name=product.name,
            image_id=image.id,
            image_url=image_url(image.id),
            width=image.width,
            height=image.height,
            reference_facts=product.reference_facts,
            facts_version=product.facts_version,
            created_at=product.created_at,
        )


# --- runs ----------------------------------------------------------------------------------------


class RunCreate(BaseModel):
    """A brief to run. `required_text` is rendered exactly as given (after NFC)."""

    product_id: uuid.UUID
    geography_code: str = Field(
        pattern=r"^[A-Za-z]{2}$", description="ISO 3166-1 alpha-2 country code"
    )
    geography_detail: str | None = Field(
        default=None, max_length=120, description="Optional region or city (alias table only)"
    )
    season: str = Field(
        min_length=1, max_length=40, description="Month, season name or holiday, e.g. December"
    )
    required_text: str = Field(
        min_length=1, max_length=80, description="1-80 characters, at most 3 lines"
    )
    aspect_ratio: Literal["1:1", "4:5"] = "4:5"
    fresh: bool = Field(
        default=False, description="Salt the image cache so a repeat makes new images"
    )


class RunAccepted(BaseModel):
    run_id: uuid.UUID
    brief_id: uuid.UUID
    status: str
    events_url: str
    run_url: str


class BriefView(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    geography_code: str
    geography_detail: str | None
    season: str
    required_text: str
    aspect_ratio: str
    golden_key: str | None


class CheckView(BaseModel):
    dimension: str
    check_name: str
    method: str
    value: float | None
    threshold: float | None
    passed: bool | None
    evidence: str | None
    evidence_data: dict[str, Any] | None = None


class EvaluationView(BaseModel):
    id: uuid.UUID
    evaluator_version: str
    verdict: str
    overall_pass: bool
    dimensions: dict[str, bool | None]
    checks: list[CheckView]
    latency_ms: int | None


class CandidateView(BaseModel):
    id: uuid.UUID
    kind: str
    attempt: int
    slot: int
    status: str
    parent_candidate_id: uuid.UUID | None
    image_id: uuid.UUID | None
    image_url: str | None
    width: int | None
    height: int | None
    requested_model: str | None
    served_model: str | None
    prompt_version: str | None
    repair_instruction: str | None
    created_at: datetime
    evaluation: EvaluationView | None


class RunView(BaseModel):
    id: uuid.UUID
    origin: str
    status: str
    outcome: str | None
    first_attempt_pass: bool | None
    repair_count: int
    cost_usd: float
    latency_ms: int | None
    error: dict[str, Any] | None
    config: dict[str, Any]
    created_at: datetime
    finished_at: datetime | None


class RunDetail(BaseModel):
    run: RunView
    brief: BriefView
    product_id: uuid.UUID
    spec: dict[str, Any] | None
    spec_version: str | None
    candidates: list[CandidateView]
    approved_candidate_id: uuid.UUID | None
    best_candidate_id: uuid.UUID | None = Field(
        default=None,
        description="The candidate to review: the approved one, or the best one on needs_review",
    )
    cost_usd: float
    latency_ms: int | None
    events_url: str


# --- human decision, cancel, history -------------------------------------------------------------

ACTOR_PATTERN = r"^[A-Za-z0-9._@-]{1,48}$"


class DecisionCreate(BaseModel):
    """A human release decision; approving a `needs_review` run is an override (needs a reason)."""

    action: Literal["approve", "reject"]
    reason: str | None = Field(
        default=None,
        max_length=500,
        description="Required to approve a needs_review run (an override of the gate)",
    )
    actor: str = Field(
        default="reviewer",
        pattern=ACTOR_PATTERN,
        description="Who decides; recorded in the audit row and as labeller human:<actor>",
    )


class DecisionView(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    action: Literal["approve", "reject"]
    reason: str | None
    actor: str
    previous_status: str
    status: Literal["approved", "rejected"]
    is_override: bool
    approved_candidate_id: uuid.UUID | None
    label_id: uuid.UUID | None = Field(
        default=None, description="The labels row an override wrote (labeller human:<actor>)"
    )
    created_at: datetime


class RunCancelled(BaseModel):
    run_id: uuid.UUID
    status: str
    cancelled: bool = Field(description="False when the run ended on its own first")


class RunSummary(BaseModel):
    id: uuid.UUID
    origin: str
    status: str
    outcome: str | None
    product_id: uuid.UUID
    geography_code: str
    season: str
    required_text: str
    aspect_ratio: str
    golden_key: str | None
    first_attempt_pass: bool | None
    repair_count: int
    cost_usd: float
    latency_ms: int | None
    approved_candidate_id: uuid.UUID | None
    best_candidate_id: uuid.UUID | None
    thumbnail_url: str | None = Field(
        description="The approved candidate's image, else the best candidate's"
    )
    created_at: datetime
    finished_at: datetime | None
