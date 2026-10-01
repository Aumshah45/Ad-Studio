"""adstudio: images, products, briefs, runs, run_events, candidates, evaluations, evaluation_checks,
labels, planted_failures, eval_reports, run_decisions; FK model_calls.run_id -> runs

Revision ID: 0002_adstudio
Revises: 0001b_ledger_run_id
Create Date: 2026-09-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_adstudio"
down_revision: str | None = "0001b_ledger_run_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "eval_reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("evaluator_version", sa.String(length=32), nullable=False),
        sa.Column("dataset_version", sa.String(length=64), nullable=False),
        sa.Column("git_sha", sa.String(length=40), nullable=True),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("report_path", sa.String(length=512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_eval_reports")),
    )
    op.create_index("ix_eval_reports_created_at", "eval_reports", ["created_at"], unique=False)
    op.create_table(
        "images",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), server_default="demo", nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("mime", sa.String(length=32), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("bytes", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "mime IN ('image/png', 'image/jpeg', 'image/webp')", name=op.f("ck_images_mime")
        ),
        sa.CheckConstraint(
            "source NOT IN ('generated', 'repair', 'overlay') OR greatest(width, height) <= 1024",
            name=op.f("ck_images_generated_max_edge"),
        ),
        sa.CheckConstraint(
            "source IN ('upload', 'generated', 'repair', 'overlay', 'planted', 'golden')",
            name=op.f("ck_images_source"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_images")),
        sa.UniqueConstraint("tenant_id", "sha256", name="uq_images_tenant_sha256"),
    )
    op.create_table(
        "products",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), server_default="demo", nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("image_id", sa.Uuid(), nullable=False),
        sa.Column("reference_facts", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("facts_version", sa.String(length=32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["image_id"], ["images.id"], name=op.f("fk_products_image_id_images")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_products")),
        sa.UniqueConstraint("tenant_id", "image_id", name="uq_products_tenant_image"),
    )
    op.create_index(
        "ix_products_tenant_created",
        "products",
        ["tenant_id", sa.literal_column("created_at DESC"), "id"],
        unique=False,
    )
    op.create_table(
        "briefs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), server_default="demo", nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("geography_code", sa.String(length=2), nullable=False),
        sa.Column("geography_detail", sa.String(length=120), nullable=True),
        sa.Column("season", sa.String(length=40), nullable=False),
        sa.Column("required_text", sa.Text(), nullable=False),
        sa.Column("aspect_ratio", sa.String(length=8), server_default="4:5", nullable=False),
        sa.Column("golden_key", sa.String(length=32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("aspect_ratio IN ('1:1', '4:5')", name=op.f("ck_briefs_aspect_ratio")),
        sa.CheckConstraint(
            "char_length(required_text) BETWEEN 1 AND 80", name=op.f("ck_briefs_required_text_len")
        ),
        sa.ForeignKeyConstraint(
            ["product_id"], ["products.id"], name=op.f("fk_briefs_product_id_products")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_briefs")),
        sa.UniqueConstraint("golden_key", name="uq_briefs_golden_key"),
    )
    op.create_table(
        "runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), server_default="demo", nullable=False),
        sa.Column("brief_id", sa.Uuid(), nullable=False),
        sa.Column("origin", sa.String(length=16), server_default="ui", nullable=False),
        sa.Column("batch_label", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="queued", nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=True),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("spec", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("spec_version", sa.String(length=32), nullable=True),
        sa.Column("approved_candidate_id", sa.Uuid(), nullable=True),
        sa.Column("first_attempt_pass", sa.Boolean(), nullable=True),
        sa.Column("repair_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "cost_usd", sa.Numeric(precision=12, scale=6), server_default="0", nullable=False
        ),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("request_hash", sa.String(length=64), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("origin IN ('ui', 'golden')", name=op.f("ck_runs_origin")),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN ('native', 'overlay')", name=op.f("ck_runs_outcome")
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'planning', 'generating', 'evaluating', 'repairing', "
            "'fallback', 'passed', 'needs_review', 'failed', 'interrupted', 'cancelled', "
            "'approved', 'rejected')",
            name=op.f("ck_runs_status"),
        ),
        sa.ForeignKeyConstraint(["brief_id"], ["briefs.id"], name=op.f("fk_runs_brief_id_briefs")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runs")),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_runs_tenant_idempotency_key"),
    )
    op.create_index(
        "ix_runs_active_status",
        "runs",
        ["status"],
        unique=False,
        postgresql_where=sa.text(
            "status IN ('queued', 'planning', 'generating', 'evaluating', 'repairing', 'fallback')"
        ),
    )
    op.create_index(
        "ix_runs_tenant_created",
        "runs",
        ["tenant_id", sa.literal_column("created_at DESC"), "id"],
        unique=False,
    )
    op.create_table(
        "candidates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("image_id", sa.Uuid(), nullable=True),
        sa.Column("parent_candidate_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("slot", sa.Integer(), server_default="0", nullable=False),
        sa.Column("requested_model", sa.String(length=128), nullable=True),
        sa.Column("served_model", sa.String(length=128), nullable=True),
        sa.Column("prompt_version", sa.String(length=32), nullable=True),
        sa.Column("repair_instruction", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("model_call_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('initial', 'repair', 'overlay')", name=op.f("ck_candidates_kind")
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'passed', 'failed', 'blocked', 'approved', 'discarded')",
            name=op.f("ck_candidates_status"),
        ),
        sa.ForeignKeyConstraint(
            ["image_id"], ["images.id"], name=op.f("fk_candidates_image_id_images")
        ),
        sa.ForeignKeyConstraint(
            ["model_call_id"],
            ["model_calls.id"],
            name=op.f("fk_candidates_model_call_id_model_calls"),
        ),
        sa.ForeignKeyConstraint(
            ["parent_candidate_id"],
            ["candidates.id"],
            name=op.f("fk_candidates_parent_candidate_id_candidates"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name=op.f("fk_candidates_run_id_runs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_candidates")),
    )
    op.create_index(
        "ix_candidates_run_attempt_slot", "candidates", ["run_id", "attempt", "slot"], unique=False
    )
    # runs <-> candidates is circular: add the approved-candidate FK once both tables exist.
    op.create_foreign_key(
        "fk_runs_approved_candidate_id_candidates",
        "runs",
        "candidates",
        ["approved_candidate_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # `runs` was created empty just above, so every non-null ledger run_id is an orphan (left by
    # an interrupted downgrade/upgrade or rows written at 0001b): clear them so the FK can be added.
    op.execute("UPDATE model_calls SET run_id = NULL WHERE run_id IS NOT NULL")
    op.create_foreign_key(
        "fk_model_calls_run_id_runs", "model_calls", "runs", ["run_id"], ["id"], ondelete="SET NULL"
    )
    op.create_table(
        "labels",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("image_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("labeller", sa.String(length=64), nullable=False),
        sa.Column("text_ok", sa.Boolean(), nullable=True),
        sa.Column("product_ok", sa.Boolean(), nullable=True),
        sa.Column("context_ok", sa.Boolean(), nullable=True),
        sa.Column("overall_ok", sa.Boolean(), nullable=True),
        sa.Column("rubric_version", sa.String(length=32), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["image_id"], ["images.id"], name=op.f("fk_labels_image_id_images"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name=op.f("fk_labels_run_id_runs"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_labels")),
        sa.UniqueConstraint("image_id", "labeller", name="uq_labels_image_labeller"),
    )
    op.create_table(
        "run_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("previous_status", sa.String(length=16), nullable=False),
        sa.Column("is_override", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("action IN ('approve', 'reject')", name=op.f("ck_run_decisions_action")),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name=op.f("fk_run_decisions_run_id_runs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_decisions")),
    )
    op.create_index(op.f("ix_run_decisions_run_id"), "run_decisions", ["run_id"], unique=False)
    op.create_table(
        "run_events",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=48), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name=op.f("fk_run_events_run_id_runs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("run_id", "seq", name="pk_run_events"),
    )
    op.create_table(
        "evaluations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("image_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("candidate_id", sa.Uuid(), nullable=True),
        sa.Column("evaluator_version", sa.String(length=32), nullable=False),
        sa.Column("text_pass", sa.Boolean(), nullable=True),
        sa.Column("product_pass", sa.Boolean(), nullable=True),
        sa.Column("context_pass", sa.Boolean(), nullable=True),
        sa.Column("technical_pass", sa.Boolean(), nullable=True),
        sa.Column("overall_pass", sa.Boolean(), nullable=False),
        sa.Column("verdict", sa.String(length=16), nullable=False),
        sa.Column(
            "cost_usd", sa.Numeric(precision=12, scale=6), server_default="0", nullable=False
        ),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "verdict IN ('pass', 'fail', 'unverified')", name=op.f("ck_evaluations_verdict")
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["candidates.id"],
            name=op.f("fk_evaluations_candidate_id_candidates"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["image_id"], ["images.id"], name=op.f("fk_evaluations_image_id_images")
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name=op.f("fk_evaluations_run_id_runs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluations")),
        sa.UniqueConstraint(
            "image_id",
            "run_id",
            "evaluator_version",
            name="uq_evaluations_image_run_version",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        op.f("ix_evaluations_candidate_id"), "evaluations", ["candidate_id"], unique=False
    )
    op.create_table(
        "planted_failures",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_candidate_id", sa.Uuid(), nullable=True),
        sa.Column("source_image_id", sa.Uuid(), nullable=True),
        sa.Column("image_id", sa.Uuid(), nullable=False),
        sa.Column("mutation", sa.String(length=48), nullable=False),
        sa.Column(
            "params",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "fails_dimensions",
            sa.ARRAY(sa.String(length=16)),
            server_default=sa.text("'{}'::varchar[]"),
            nullable=False,
        ),
        sa.Column("generator_version", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["image_id"], ["images.id"], name=op.f("fk_planted_failures_image_id_images")
        ),
        sa.ForeignKeyConstraint(
            ["source_candidate_id"],
            ["candidates.id"],
            name=op.f("fk_planted_failures_source_candidate_id_candidates"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_image_id"],
            ["images.id"],
            name=op.f("fk_planted_failures_source_image_id_images"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_planted_failures")),
        sa.UniqueConstraint("image_id", name=op.f("uq_planted_failures_image_id")),
    )
    op.create_table(
        "evaluation_checks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("evaluation_id", sa.Uuid(), nullable=False),
        sa.Column("dimension", sa.String(length=16), nullable=False),
        sa.Column("check_name", sa.String(length=64), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("threshold", sa.Float(), nullable=True),
        sa.Column("passed", sa.Boolean(), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("evidence_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint(
            "dimension IN ('text', 'product', 'context', 'technical')",
            name=op.f("ck_evaluation_checks_dimension"),
        ),
        sa.CheckConstraint(
            "method IN ('deterministic', 'vlm')", name=op.f("ck_evaluation_checks_method")
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_id"],
            ["evaluations.id"],
            name=op.f("fk_evaluation_checks_evaluation_id_evaluations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation_checks")),
    )
    op.create_index(
        op.f("ix_evaluation_checks_evaluation_id"),
        "evaluation_checks",
        ["evaluation_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_constraint("fk_model_calls_run_id_runs", "model_calls", type_="foreignkey")
    # The runs are dropped below; ledger rows keep their cost but lose the dangling run id, so a
    # later upgrade can add the foreign key again.
    op.execute("UPDATE model_calls SET run_id = NULL WHERE run_id IS NOT NULL")
    op.drop_constraint("fk_runs_approved_candidate_id_candidates", "runs", type_="foreignkey")
    op.drop_index(op.f("ix_evaluation_checks_evaluation_id"), table_name="evaluation_checks")
    op.drop_table("evaluation_checks")
    op.drop_table("planted_failures")
    op.drop_index(op.f("ix_evaluations_candidate_id"), table_name="evaluations")
    op.drop_table("evaluations")
    op.drop_table("run_events")
    op.drop_index(op.f("ix_run_decisions_run_id"), table_name="run_decisions")
    op.drop_table("run_decisions")
    op.drop_table("labels")
    op.drop_index("ix_candidates_run_attempt_slot", table_name="candidates")
    op.drop_table("candidates")
    op.drop_index("ix_runs_tenant_created", table_name="runs")
    op.drop_index(
        "ix_runs_active_status",
        table_name="runs",
        postgresql_where=sa.text(
            "status IN ('queued', 'planning', 'generating', 'evaluating', 'repairing', 'fallback')"
        ),
    )
    op.drop_table("runs")
    op.drop_table("briefs")
    op.drop_index("ix_products_tenant_created", table_name="products")
    op.drop_table("products")
    op.drop_table("images")
    op.drop_index("ix_eval_reports_created_at", table_name="eval_reports")
    op.drop_table("eval_reports")
