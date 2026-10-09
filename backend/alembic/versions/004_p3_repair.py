"""P3 repair / patch attempt tables

Revision ID: 004
Revises: 003
Create Date: 2026-10-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE job_kind ADD VALUE IF NOT EXISTS 'repair'")
    op.execute("ALTER TYPE run_type ADD VALUE IF NOT EXISTS 'repair_probe'")
    op.execute("ALTER TYPE run_type ADD VALUE IF NOT EXISTS 'repair_suite'")

    for type_sql in (
        """
        DO $$ BEGIN
            CREATE TYPE repair_status AS ENUM (
                'queued', 'preparing', 'baseline', 'retrieving', 'proposing',
                'validating', 'verifying', 'deciding', 'completed', 'failed',
                'cancelled', 'timed_out'
            );
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
        """,
        """
        DO $$ BEGIN
            CREATE TYPE repair_outcome AS ENUM (
                'verified_fix', 'partial_verification', 'verification_failed',
                'baseline_invalid', 'budget_exhausted'
            );
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
        """,
        """
        DO $$ BEGIN
            CREATE TYPE review_decision AS ENUM ('accepted', 'rejected');
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
        """,
    ):
        op.execute(type_sql)

    rs = postgresql.ENUM(name="repair_status", create_type=False)
    ro = postgresql.ENUM(name="repair_outcome", create_type=False)
    rd = postgresql.ENUM(name="review_decision", create_type=False)

    op.create_table(
        "repair_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "investigation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("investigations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "commit_search_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("commit_searches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "bundle_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("reproduction_bundles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_sha", sa.String(64), nullable=False),
        sa.Column("introducing_sha", sa.String(64), nullable=True),
        sa.Column("bundle_content_hash", sa.String(128), nullable=False),
        sa.Column("runner_image", sa.String(300), nullable=True),
        sa.Column("runner_image_digest", sa.String(200), nullable=True),
        sa.Column("graph_thread_id", sa.String(100), nullable=False),
        sa.Column("status", rs, nullable=False, server_default="queued"),
        sa.Column("outcome", ro, nullable=True),
        sa.Column("stage", sa.String(50), server_default="queued"),
        sa.Column("attempt_count", sa.Integer(), server_default="0"),
        sa.Column("max_attempts", sa.Integer(), server_default="3"),
        sa.Column("max_duration_seconds", sa.Integer(), server_default="600"),
        sa.Column("max_tokens", sa.Integer(), server_default="8000"),
        sa.Column("provider", sa.String(100), nullable=True),
        sa.Column("model", sa.String(200), nullable=True),
        sa.Column("selected_attempt_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("limitations", sa.Text(), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "patch_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "repair_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("repair_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(100), nullable=True),
        sa.Column("model", sa.String(200), nullable=True),
        sa.Column("prompt_version", sa.String(50), server_default="p3-v1"),
        sa.Column("context_manifest", postgresql.JSONB(), nullable=True),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("assumptions", sa.Text(), nullable=True),
        sa.Column("limitations", sa.Text(), nullable=True),
        sa.Column("source_references", postgresql.JSONB(), nullable=True),
        sa.Column("patch_artifact_path", sa.String(1000), nullable=True),
        sa.Column("patch_hash", sa.String(128), nullable=True),
        sa.Column("changed_files", postgresql.JSONB(), nullable=True),
        sa.Column("new_test_path", sa.String(500), nullable=True),
        sa.Column("validation_ok", sa.Boolean(), nullable=True),
        sa.Column("validation_error", sa.Text(), nullable=True),
        sa.Column("feedback", sa.Text(), nullable=True),
        sa.Column("tokens_prompt", sa.Integer(), nullable=True),
        sa.Column("tokens_completion", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(50), server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "verification_comparisons",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "attempt_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("patch_attempts.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("baseline_repro_ids", postgresql.JSONB(), nullable=True),
        sa.Column("baseline_suite_id", sa.String(64), nullable=True),
        sa.Column("patched_repro_ids", postgresql.JSONB(), nullable=True),
        sa.Column("patched_suite_id", sa.String(64), nullable=True),
        sa.Column("new_test_pre_id", sa.String(64), nullable=True),
        sa.Column("new_test_post_id", sa.String(64), nullable=True),
        sa.Column("reproduction_before", postgresql.JSONB(), nullable=True),
        sa.Column("reproduction_after", postgresql.JSONB(), nullable=True),
        sa.Column("suite_before", postgresql.JSONB(), nullable=True),
        sa.Column("suite_after", postgresql.JSONB(), nullable=True),
        sa.Column("new_failures", postgresql.JSONB(), nullable=True),
        sa.Column("missing_tests", postgresql.JSONB(), nullable=True),
        sa.Column("unchanged_failures", postgresql.JSONB(), nullable=True),
        sa.Column("outcome", ro, nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "patch_reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "attempt_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("patch_attempts.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("patch_hash", sa.String(128), nullable=False),
        sa.Column("decision", rd, nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.add_column(
        "jobs",
        sa.Column("repair_run_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_jobs_repair_run",
        "jobs",
        "repair_runs",
        ["repair_run_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_jobs_repair_run", "jobs", type_="foreignkey")
    op.drop_column("jobs", "repair_run_id")
    op.drop_table("patch_reviews")
    op.drop_table("verification_comparisons")
    op.drop_table("patch_attempts")
    op.drop_table("repair_runs")
    op.execute("DROP TYPE IF EXISTS review_decision")
    op.execute("DROP TYPE IF EXISTS repair_outcome")
    op.execute("DROP TYPE IF EXISTS repair_status")
