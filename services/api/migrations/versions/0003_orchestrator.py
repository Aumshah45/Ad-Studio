"""orchestrator: candidate kind clean_plate, runs.best_candidate_id, evaluations per candidate

Revision ID: 0003_orchestrator
Revises: 0002_adstudio
Create Date: 2026-09-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_orchestrator"
down_revision: str | None = "0002_adstudio"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_candidates_kind"), "candidates", type_="check")
    op.create_check_constraint(
        op.f("ck_candidates_kind"),
        "candidates",
        "kind IN ('initial', 'repair', 'clean_plate', 'overlay')",
    )
    op.add_column("runs", sa.Column("best_candidate_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_runs_best_candidate_id_candidates",
        "runs",
        "candidates",
        ["best_candidate_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # Two candidates of one run can share an image (a cache hit, an identical edit): each keeps
    # its own evaluation row, so the idempotency key gains the candidate.
    op.drop_constraint("uq_evaluations_image_run_version", "evaluations", type_="unique")
    op.create_unique_constraint(
        "uq_evaluations_image_run_candidate_version",
        "evaluations",
        ["image_id", "run_id", "candidate_id", "evaluator_version"],
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    op.drop_constraint("uq_evaluations_image_run_candidate_version", "evaluations", type_="unique")
    op.execute(
        "DELETE FROM evaluations a USING evaluations b WHERE a.image_id = b.image_id "
        "AND a.run_id IS NOT DISTINCT FROM b.run_id AND a.evaluator_version = b.evaluator_version "
        "AND a.created_at > b.created_at"
    )
    op.create_unique_constraint(
        "uq_evaluations_image_run_version",
        "evaluations",
        ["image_id", "run_id", "evaluator_version"],
        postgresql_nulls_not_distinct=True,
    )
    op.drop_constraint("fk_runs_best_candidate_id_candidates", "runs", type_="foreignkey")
    op.drop_column("runs", "best_candidate_id")
    op.execute("UPDATE candidates SET kind = 'initial' WHERE kind = 'clean_plate'")
    op.drop_constraint(op.f("ck_candidates_kind"), "candidates", type_="check")
    op.create_check_constraint(
        op.f("ck_candidates_kind"), "candidates", "kind IN ('initial', 'repair', 'overlay')"
    )
