"""P2 commit search tables

Revision ID: 003
Revises: 002
Create Date: 2026-10-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE job_kind ADD VALUE IF NOT EXISTS 'commit_search'")
    op.execute("ALTER TYPE run_type ADD VALUE IF NOT EXISTS 'commit_probe'")

    for type_sql in (
        """
        DO $$ BEGIN
            CREATE TYPE commit_search_status AS ENUM (
                'queued', 'validating', 'searching', 'verifying',
                'completed', 'failed', 'cancelled', 'timed_out'
            );
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
        """,
        """
        DO $$ BEGIN
            CREATE TYPE commit_search_outcome AS ENUM (
                'verified_boundary', 'inconclusive', 'invalid_range',
                'inconsistent_reproduction', 'unsupported_history', 'budget_exhausted'
            );
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
        """,
        """
        DO $$ BEGIN
            CREATE TYPE step_phase AS ENUM (
                'validate_good', 'validate_bad', 'search',
                'verify_parent', 'verify_candidate'
            );
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
        """,
        """
        DO $$ BEGIN
            CREATE TYPE step_classification AS ENUM (
                'good', 'bad', 'skip', 'abort', 'testing'
            );
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
        """,
    ):
        op.execute(type_sql)

    op.create_table(
        "reproduction_bundles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("investigation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_sha", sa.String(64), nullable=False),
        sa.Column("test_node_id", sa.String(500), nullable=False),
        sa.Column("file_manifest", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column("artifact_path", sa.String(1000), nullable=False),
        sa.Column("content_hash", sa.String(128), nullable=False),
        sa.Column("failure_exception_type", sa.String(200), nullable=True),
        sa.Column("failure_message_fragment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    css = postgresql.ENUM(name="commit_search_status", create_type=False)
    cso = postgresql.ENUM(name="commit_search_outcome", create_type=False)

    op.create_table(
        "commit_searches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("investigation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("confirmed_execution_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("execution_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("bundle_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("reproduction_bundles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("bad_sha", sa.String(64), nullable=False),
        sa.Column("good_sha", sa.String(64), nullable=False),
        sa.Column("test_node_id", sa.String(500), nullable=False),
        sa.Column("runner_image", sa.String(300), nullable=True),
        sa.Column("runner_image_digest", sa.String(200), nullable=True),
        sa.Column("status", css, nullable=False, server_default="queued"),
        sa.Column("outcome", cso, nullable=True),
        sa.Column("stage", sa.String(50), server_default="queued"),
        sa.Column("max_commits", sa.Integer(), server_default="20"),
        sa.Column("max_executions", sa.Integer(), server_default="30"),
        sa.Column("max_duration_seconds", sa.Integer(), server_default="600"),
        sa.Column("executions_used", sa.Integer(), server_default="0"),
        sa.Column("commits_tested", sa.Integer(), server_default="0"),
        sa.Column("candidate_shas", postgresql.ARRAY(sa.String()), server_default="{}"),
        sa.Column("verified_sha", sa.String(64), nullable=True),
        sa.Column("parent_sha", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), server_default="false"),
        sa.Column(
            "assumption_note",
            sa.Text(),
            server_default=(
                "Search assumes the bug was introduced once within the selected range. "
                "Bisect does not prove there were no intermediate fixes and reintroductions."
            ),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    sp = postgresql.ENUM(name="step_phase", create_type=False)
    sc = postgresql.ENUM(name="step_classification", create_type=False)

    op.create_table(
        "commit_search_steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("search_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("commit_searches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sha", sa.String(64), nullable=False),
        sa.Column("phase", sp, nullable=False),
        sa.Column("classification", sc, nullable=False),
        sa.Column("classification_reason", sa.Text(), nullable=True),
        sa.Column("attempt", sa.Integer(), server_default="1"),
        sa.Column("execution_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("execution_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "commit_metadata",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("repository_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sha", sa.String(64), nullable=False),
        sa.Column("parent_shas", postgresql.ARRAY(sa.String()), server_default="{}"),
        sa.Column("author_name", sa.String(200), nullable=True),
        sa.Column("author_email", sa.String(300), nullable=True),
        sa.Column("authored_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("repository_id", "sha", name="uq_commit_metadata_repo_sha"),
    )

    op.add_column(
        "jobs",
        sa.Column(
            "commit_search_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("commit_searches.id", ondelete="SET NULL", name="fk_jobs_commit_search"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_constraint("fk_jobs_commit_search", "jobs", type_="foreignkey")
    op.drop_column("jobs", "commit_search_id")
    op.drop_table("commit_metadata")
    op.drop_table("commit_search_steps")
    op.drop_table("commit_searches")
    op.drop_table("reproduction_bundles")
