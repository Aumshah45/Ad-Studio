"""composition dimension (ADR-007): evaluations.composition_pass, labels.composition_ok, and
'composition' in evaluation_checks.dimension

Revision ID: 0004_composition
Revises: 0003_orchestrator
Create Date: 2026-09-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_composition"
down_revision: str | None = "0003_orchestrator"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("evaluations", sa.Column("composition_pass", sa.Boolean(), nullable=True))
    op.add_column("labels", sa.Column("composition_ok", sa.Boolean(), nullable=True))
    op.drop_constraint(op.f("ck_evaluation_checks_dimension"), "evaluation_checks", type_="check")
    op.create_check_constraint(
        op.f("ck_evaluation_checks_dimension"),
        "evaluation_checks",
        "dimension IN ('text', 'product', 'context', 'technical', 'composition')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM evaluation_checks WHERE dimension = 'composition'")
    op.drop_constraint(op.f("ck_evaluation_checks_dimension"), "evaluation_checks", type_="check")
    op.create_check_constraint(
        op.f("ck_evaluation_checks_dimension"),
        "evaluation_checks",
        "dimension IN ('text', 'product', 'context', 'technical')",
    )
    op.drop_column("labels", "composition_ok")
    op.drop_column("evaluations", "composition_pass")
