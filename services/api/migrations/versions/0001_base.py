"""base: extensions, model_calls ledger, model_cache

Revision ID: 0001_base
Revises:
Create Date: 2026-09-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_base"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for ext in ("vector", "pg_trgm", "fuzzystrmatch"):
        op.execute(f"CREATE EXTENSION IF NOT EXISTS {ext}")

    op.create_table(
        "model_calls",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("request_id", sa.String(64)),
        sa.Column("trace_id", sa.String(32)),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("operation", sa.String(128), nullable=False),
        sa.Column("prompt_name", sa.String(128)),
        sa.Column("prompt_version", sa.String(32)),
        sa.Column("requested_model", sa.String(128), nullable=False),
        sa.Column("served_model", sa.String(128)),
        sa.Column("fallback_used", sa.Boolean(), nullable=False),
        sa.Column("cached", sa.Boolean(), nullable=False),
        sa.Column("input_units", sa.Float(), nullable=False),
        sa.Column("output_units", sa.Float(), nullable=False),
        sa.Column("unit_type", sa.String(16), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("est_cost_usd", sa.Float(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_type", sa.String(128)),
        sa.Column("meta", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_model_calls"),
    )
    op.create_index("ix_model_calls_created_at", "model_calls", ["created_at"])
    op.create_index("ix_model_calls_kind", "model_calls", ["kind"])

    op.create_table(
        "model_cache",
        sa.Column("key", sa.String(64)),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("version", sa.String(32)),
        sa.Column("output", postgresql.JSONB()),
        sa.Column("blob_path", sa.String(512)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("hit_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("key", name="pk_model_cache"),
    )
    op.create_index("ix_model_cache_created_at", "model_cache", ["created_at"])
    op.create_index("ix_model_cache_kind", "model_cache", ["kind"])


def downgrade() -> None:
    op.drop_table("model_cache")
    op.drop_table("model_calls")
