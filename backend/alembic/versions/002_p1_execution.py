"""P1 execution / reproduction tables

Revision ID: 002
Revises: 001
Create Date: 2026-10-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE job_kind ADD VALUE IF NOT EXISTS 'test_discovery'")
    op.execute("ALTER TYPE job_kind ADD VALUE IF NOT EXISTS 'reproduction'")

    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE run_type AS ENUM ('collection', 'reproduction');
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE execution_status AS ENUM (
                'queued', 'preparing', 'running', 'completed',
                'failed', 'cancelled', 'timed_out'
            );
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE test_outcome AS ENUM (
                'collected', 'passed', 'failed', 'skipped', 'error'
            );
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
        """
    )

    run_type = postgresql.ENUM(
        "collection", "reproduction", name="run_type", create_type=False
    )
    execution_status = postgresql.ENUM(
        "queued",
        "preparing",
        "running",
        "completed",
        "failed",
        "cancelled",
        "timed_out",
        name="execution_status",
        create_type=False,
    )
    test_outcome = postgresql.ENUM(
        "collected",
        "passed",
        "failed",
        "skipped",
        "error",
        name="test_outcome",
        create_type=False,
    )

    op.create_table(
        "execution_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "investigation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("investigations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("pinned_sha", sa.String(64), nullable=False),
        sa.Column("run_type", run_type, nullable=False),
        sa.Column("selected_tests", postgresql.ARRAY(sa.String()), server_default="{}"),
        sa.Column("runner_image", sa.String(300), nullable=True),
        sa.Column("runner_image_digest", sa.String(200), nullable=True),
        sa.Column("status", execution_status, nullable=False),
        sa.Column("stage", sa.String(50), server_default="queued"),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_category", sa.String(100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("container_id", sa.String(100), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.execute(
        """
        DO $$ BEGIN
            ALTER TABLE jobs ADD COLUMN execution_id UUID;
        EXCEPTION WHEN duplicate_column THEN null;
        END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            ALTER TABLE jobs
              ADD CONSTRAINT fk_jobs_execution
              FOREIGN KEY (execution_id) REFERENCES execution_runs(id)
              ON DELETE SET NULL;
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
        """
    )

    op.create_table(
        "test_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "execution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("execution_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("test_identifier", sa.String(500), nullable=False),
        sa.Column("outcome", test_outcome, nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column("traceback", sa.Text(), nullable=True),
        sa.Column("in_evidence", sa.Boolean(), server_default=sa.false()),
    )

    op.create_table(
        "execution_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "execution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("execution_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("artifact_type", sa.String(50), nullable=False),
        sa.Column("storage_path", sa.String(1000), nullable=False),
        sa.Column("byte_size", sa.Integer(), server_default="0"),
        sa.Column("truncated", sa.Boolean(), server_default=sa.false()),
    )

    op.create_table(
        "reproduction_confirmations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "execution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("execution_runs.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("matching_failed_tests", postgresql.ARRAY(sa.String()), server_default="{}"),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("reproduction_confirmations")
    op.drop_table("execution_artifacts")
    op.drop_table("test_results")
    op.execute("ALTER TABLE jobs DROP CONSTRAINT IF EXISTS fk_jobs_execution")
    op.execute("ALTER TABLE jobs DROP COLUMN IF EXISTS execution_id")
    op.drop_table("execution_runs")
    op.execute("DROP TYPE IF EXISTS test_outcome")
    op.execute("DROP TYPE IF EXISTS execution_status")
    op.execute("DROP TYPE IF EXISTS run_type")
