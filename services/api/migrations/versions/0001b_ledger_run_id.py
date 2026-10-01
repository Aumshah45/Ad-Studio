"""ledger: model_calls.run_id (nullable, indexed; FK to runs is added by 0002_adstudio)

Revision ID: 0001b_ledger_run_id
Revises: 0001_base
Create Date: 2026-09-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001b_ledger_run_id"
down_revision: str | None = "0001_base"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("model_calls", sa.Column("run_id", sa.Uuid(), nullable=True))
    op.create_index("ix_model_calls_run_id", "model_calls", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_model_calls_run_id", table_name="model_calls")
    op.drop_column("model_calls", "run_id")
