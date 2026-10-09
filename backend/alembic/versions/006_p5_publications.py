"""P5 pull request publications

Revision ID: 006
Revises: 005
Create Date: 2026-10-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE publication_status AS ENUM (
                'preview', 'stale_base', 'revalidating', 'ready',
                'publishing', 'published', 'failed', 'conflict'
            );
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
        """
    )
    ps = postgresql.ENUM(name="publication_status", create_type=False)
    op.create_table(
        "pull_request_publications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "repair_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("repair_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "patch_attempt_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("patch_attempts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "investigation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("investigations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "repository_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("identity_key", sa.String(200), nullable=False),
        sa.Column("patch_hash", sa.String(128), nullable=False),
        sa.Column("verified_base_sha", sa.String(64), nullable=False),
        sa.Column("current_remote_sha", sa.String(64), nullable=True),
        sa.Column("owner", sa.String(200), nullable=False),
        sa.Column("repo_name", sa.String(200), nullable=False),
        sa.Column("target_branch", sa.String(200), nullable=False),
        sa.Column("feature_branch", sa.String(200), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("preview_hash", sa.String(128), nullable=False),
        sa.Column("status", ps, nullable=False, server_default="preview"),
        sa.Column("published_commit_sha", sa.String(64), nullable=True),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column("pr_url", sa.String(500), nullable=True),
        sa.Column("pr_draft", sa.Boolean(), nullable=True),
        sa.Column("revalidation_outcome", sa.String(100), nullable=True),
        sa.Column("revalidation_summary", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_pull_request_publications_identity_key",
        "pull_request_publications",
        ["identity_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_pull_request_publications_identity_key", table_name="pull_request_publications")
    op.drop_table("pull_request_publications")
    op.execute("DROP TYPE IF EXISTS publication_status")
