"""Ad studio tables (architecture "Data model" + "Cross-doc reconciliation").

Tenancy: `tenant_id` lives on images, products, briefs and runs; child tables reach it through
`run_id` / `image_id`. Pass/fail per dimension is kept in typed columns because the meta-eval
queries it; jsonb is used only where the payload varies by version.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base, TimestampMixin

DEFAULT_TENANT = "demo"
MAX_GENERATED_EDGE = 1024

IMAGE_SOURCES = ("upload", "generated", "repair", "overlay", "planted", "golden")
GENERATED_SOURCES = ("generated", "repair", "overlay")
IMAGE_MIMES = ("image/png", "image/jpeg", "image/webp")
RUN_ACTIVE_STATUSES = ("queued", "planning", "generating", "evaluating", "repairing", "fallback")
RUN_TERMINAL_STATUSES = ("passed", "needs_review", "failed", "interrupted", "cancelled")
RUN_DECIDED_STATUSES = ("approved", "rejected")
RUN_STATUSES = RUN_ACTIVE_STATUSES + RUN_TERMINAL_STATUSES + RUN_DECIDED_STATUSES
RUN_OUTCOMES = ("native", "overlay")
RUN_ORIGINS = ("ui", "golden")
CANDIDATE_KINDS = ("initial", "repair", "clean_plate", "overlay")
CANDIDATE_STATUSES = ("pending", "passed", "failed", "blocked", "approved", "discarded")
DIMENSIONS = ("text", "product", "context", "technical", "composition")
VERDICTS = ("pass", "fail", "unverified")
DECISION_ACTIONS = ("approve", "reject")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _tenant() -> Mapped[str]:
    return mapped_column(String(64), nullable=False, server_default=DEFAULT_TENANT)


class Image(TimestampMixin, Base):
    __tablename__ = "images"
    __table_args__ = (
        UniqueConstraint("tenant_id", "sha256", name="uq_images_tenant_sha256"),
        CheckConstraint(_in("source", IMAGE_SOURCES), name="source"),
        CheckConstraint(_in("mime", IMAGE_MIMES), name="mime"),
        # C1 holds in the database too: generated pixels never exceed a 1024 px long edge.
        CheckConstraint(
            f"source NOT IN ({', '.join(repr(v) for v in GENERATED_SOURCES)}) "
            f"OR greatest(width, height) <= {MAX_GENERATED_EDGE}",
            name="generated_max_edge",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = _tenant()
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    mime: Mapped[str] = mapped_column(String(32), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    bytes: Mapped[int] = mapped_column(Integer, nullable=False)


class Product(TimestampMixin, Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("tenant_id", "image_id", name="uq_products_tenant_image"),
        Index("ix_products_tenant_created", "tenant_id", text("created_at DESC"), "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = _tenant()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    image_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("images.id"), nullable=False)
    # Filled by the vision product profile (a later slice); null until then or on a vision outage.
    reference_facts: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    facts_version: Mapped[str | None] = mapped_column(String(32))


class Brief(TimestampMixin, Base):
    __tablename__ = "briefs"
    __table_args__ = (
        UniqueConstraint("golden_key", name="uq_briefs_golden_key"),
        CheckConstraint("aspect_ratio IN ('1:1', '4:5')", name="aspect_ratio"),
        CheckConstraint("char_length(required_text) BETWEEN 1 AND 80", name="required_text_len"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = _tenant()
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False)
    geography_code: Mapped[str] = mapped_column(String(2), nullable=False)
    geography_detail: Mapped[str | None] = mapped_column(String(120))
    season: Mapped[str] = mapped_column(String(40), nullable=False)
    # NFC, byte-exact, never rewritten.
    required_text: Mapped[str] = mapped_column(Text, nullable=False)
    aspect_ratio: Mapped[str] = mapped_column(String(8), nullable=False, server_default="4:5")
    golden_key: Mapped[str | None] = mapped_column(String(32))


class Run(TimestampMixin, Base):
    __tablename__ = "runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_runs_tenant_idempotency_key"),
        CheckConstraint(_in("status", RUN_STATUSES), name="status"),
        CheckConstraint(f"outcome IS NULL OR {_in('outcome', RUN_OUTCOMES)}", name="outcome"),
        CheckConstraint(_in("origin", RUN_ORIGINS), name="origin"),
        Index("ix_runs_tenant_created", "tenant_id", text("created_at DESC"), "id"),
        Index(
            "ix_runs_active_status",
            "status",
            postgresql_where=text(_in("status", RUN_ACTIVE_STATUSES)),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = _tenant()
    brief_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("briefs.id"), nullable=False)
    origin: Mapped[str] = mapped_column(String(16), nullable=False, server_default="ui")
    batch_label: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="queued")
    outcome: Mapped[str | None] = mapped_column(String(16))
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    spec: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    spec_version: Mapped[str | None] = mapped_column(String(32))
    # Set by the gate (a passing candidate) or by a human override approval (run_decisions).
    approved_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("candidates.id", use_alter=True, ondelete="SET NULL")
    )
    # The candidate shown for review when the gate could not pass one (needs_review).
    best_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("candidates.id", use_alter=True, ondelete="SET NULL")
    )
    first_attempt_pass: Mapped[bool | None] = mapped_column(Boolean)
    repair_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, server_default="0")
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    request_hash: Mapped[str | None] = mapped_column(String(64))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RunEvent(Base):
    __tablename__ = "run_events"
    __table_args__ = (PrimaryKeyConstraint("run_id", "seq", name="pk_run_events"),)

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(48), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Candidate(TimestampMixin, Base):
    __tablename__ = "candidates"
    __table_args__ = (
        CheckConstraint(_in("kind", CANDIDATE_KINDS), name="kind"),
        CheckConstraint(_in("status", CANDIDATE_STATUSES), name="status"),
        Index("ix_candidates_run_attempt_slot", "run_id", "attempt", "slot"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    # Null when the model returned no image (safety block).
    image_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("images.id"))
    parent_candidate_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("candidates.id"))
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    slot: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    requested_model: Mapped[str | None] = mapped_column(String(128))
    served_model: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    repair_instruction: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    model_call_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("model_calls.id"))


class Evaluation(TimestampMixin, Base):
    __tablename__ = "evaluations"
    __table_args__ = (
        UniqueConstraint(
            "image_id",
            "run_id",
            "candidate_id",
            "evaluator_version",
            name="uq_evaluations_image_run_candidate_version",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(_in("verdict", VERDICTS), name="verdict"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    image_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("images.id"), nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), index=True
    )
    evaluator_version: Mapped[str] = mapped_column(String(32), nullable=False)
    # Null = unverified for that dimension (e.g. the vision judge was down).
    text_pass: Mapped[bool | None] = mapped_column(Boolean)
    product_pass: Mapped[bool | None] = mapped_column(Boolean)
    context_pass: Mapped[bool | None] = mapped_column(Boolean)
    technical_pass: Mapped[bool | None] = mapped_column(Boolean)
    # ADR-007 (ev-0.6); null on older evaluations and when unverified.
    composition_pass: Mapped[bool | None] = mapped_column(Boolean)
    overall_pass: Mapped[bool] = mapped_column(Boolean, nullable=False)
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, server_default="0")
    latency_ms: Mapped[int | None] = mapped_column(Integer)


class EvaluationCheck(Base):
    __tablename__ = "evaluation_checks"
    __table_args__ = (
        CheckConstraint(_in("dimension", DIMENSIONS), name="dimension"),
        CheckConstraint("method IN ('deterministic', 'vlm')", name="method"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    evaluation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dimension: Mapped[str] = mapped_column(String(16), nullable=False)
    check_name: Mapped[str] = mapped_column(String(64), nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    value: Mapped[float | None] = mapped_column(Float)
    threshold: Mapped[float | None] = mapped_column(Float)
    passed: Mapped[bool | None] = mapped_column(Boolean)  # null = unverified
    evidence: Mapped[str | None] = mapped_column(Text)  # the reason shown in the scorecard
    # Machine-readable evidence for the UI overlay (OCR boxes, product crop box, ...).
    evidence_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class Label(TimestampMixin, Base):
    __tablename__ = "labels"
    __table_args__ = (UniqueConstraint("image_id", "labeller", name="uq_labels_image_labeller"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    image_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("images.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"))
    labeller: Mapped[str] = mapped_column(String(64), nullable=False)  # human:<name> | planted
    text_ok: Mapped[bool | None] = mapped_column(Boolean)
    product_ok: Mapped[bool | None] = mapped_column(Boolean)
    context_ok: Mapped[bool | None] = mapped_column(Boolean)
    composition_ok: Mapped[bool | None] = mapped_column(Boolean)  # rubric v2 (ADR-007)
    overall_ok: Mapped[bool | None] = mapped_column(Boolean)
    rubric_version: Mapped[str] = mapped_column(String(32), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PlantedFailure(TimestampMixin, Base):
    __tablename__ = "planted_failures"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # Golden items may come from files rather than DB candidates, so the source image is kept too.
    source_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("candidates.id", ondelete="SET NULL")
    )
    source_image_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("images.id"))
    image_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("images.id"), nullable=False, unique=True
    )
    mutation: Mapped[str] = mapped_column(String(48), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    fails_dimensions: Mapped[list[str]] = mapped_column(
        ARRAY(String(16)), nullable=False, server_default=text("'{}'::varchar[]")
    )
    generator_version: Mapped[str] = mapped_column(String(32), nullable=False)


class EvalReport(TimestampMixin, Base):
    __tablename__ = "eval_reports"
    __table_args__ = (Index("ix_eval_reports_created_at", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    evaluator_version: Mapped[str] = mapped_column(String(32), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(64), nullable=False)
    git_sha: Mapped[str | None] = mapped_column(String(40))
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    report_path: Mapped[str | None] = mapped_column(String(512))


class RunDecision(TimestampMixin, Base):
    """Audit row for every human approve/reject (reconciliation: human release)."""

    __tablename__ = "run_decisions"
    __table_args__ = (CheckConstraint(_in("action", DECISION_ACTIONS), name="action"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_status: Mapped[str] = mapped_column(String(16), nullable=False)
    is_override: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
