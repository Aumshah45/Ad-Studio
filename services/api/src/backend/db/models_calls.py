"""Model-call ledger and response cache tables."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base, TimestampMixin


class ModelCall(TimestampMixin, Base):
    __tablename__ = "model_calls"
    __table_args__ = (Index("ix_model_calls_created_at", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    request_id: Mapped[str | None] = mapped_column(String(64))
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL"), index=True
    )
    trace_id: Mapped[str | None] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(16), index=True)  # text|image|audio|embedding|other
    operation: Mapped[str] = mapped_column(String(128))
    prompt_name: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    requested_model: Mapped[str] = mapped_column(String(128))
    served_model: Mapped[str | None] = mapped_column(String(128))
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)
    cached: Mapped[bool] = mapped_column(Boolean, default=False)
    input_units: Mapped[float] = mapped_column(Float, default=0)
    output_units: Mapped[float] = mapped_column(Float, default=0)
    unit_type: Mapped[str] = mapped_column(String(16), default="items")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    est_cost_usd: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(16), default="ok")
    error_type: Mapped[str | None] = mapped_column(String(128))
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class ModelCacheEntry(Base):
    __tablename__ = "model_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256 hex
    kind: Mapped[str] = mapped_column(String(16), index=True)
    model: Mapped[str] = mapped_column(String(128))
    version: Mapped[str | None] = mapped_column(String(32))
    output: Mapped[Any | None] = mapped_column(JSONB)
    blob_path: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
