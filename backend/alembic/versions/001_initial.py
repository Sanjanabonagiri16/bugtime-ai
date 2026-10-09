"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-10-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "repositories",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("owner", sa.String(200), nullable=False),
        sa.Column("github_url", sa.String(500), nullable=False),
        sa.Column("branch", sa.String(200), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "indexing", "ready", "failed", name="repository_status"),
            nullable=False,
        ),
        sa.Column("active_index_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("local_path", sa.String(1000), nullable=True),
        sa.Column("is_demo", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "repository_indexes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "repository_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("commit_sha", sa.String(64), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "queued",
                "fetching",
                "scanning",
                "chunking",
                "embedding",
                "saving",
                "ready",
                "failed",
                name="index_status",
            ),
            nullable=False,
        ),
        sa.Column("embedding_model", sa.String(200), nullable=False),
        sa.Column("embedding_dimension", sa.Integer(), nullable=False),
        sa.Column("file_count", sa.Integer(), server_default="0"),
        sa.Column("chunk_count", sa.Integer(), server_default="0"),
        sa.Column("skipped_count", sa.Integer(), server_default="0"),
        sa.Column("processed_files", sa.Integer(), server_default="0"),
        sa.Column("total_files_estimate", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_foreign_key(
        "fk_repositories_active_index",
        "repositories",
        "repository_indexes",
        ["active_index_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "source_files",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "index_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("repository_indexes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("path", sa.String(1000), nullable=False),
        sa.Column("language", sa.String(50), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("line_count", sa.Integer(), server_default="0"),
        sa.Column("indexing_status", sa.String(50), server_default="indexed"),
        sa.Column("skip_reason", sa.Text(), nullable=True),
        sa.Column("source_type", sa.String(50), server_default="code"),
    )

    op.create_table(
        "code_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "source_file_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("source_files.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "index_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("repository_indexes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.Column("symbol_name", sa.String(500), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("vector_point_id", sa.String(100), nullable=True),
    )

    op.create_table(
        "investigations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "repository_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "index_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("repository_indexes.id"),
            nullable=False,
        ),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("expected_behavior", sa.Text(), nullable=True),
        sa.Column("actual_behavior", sa.Text(), nullable=True),
        sa.Column("logs", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "queued",
                "retrieving",
                "evidence_ready",
                "failed",
                name="investigation_status",
            ),
            nullable=False,
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "investigation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("investigations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "chunk_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("code_chunks.id"),
            nullable=False,
        ),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("semantic_score", sa.Float(), nullable=True),
        sa.Column("exact_match", sa.Boolean(), server_default=sa.false()),
        sa.Column(
            "match_reason",
            sa.Enum("semantic", "exact", "both", name="match_reason"),
            nullable=False,
        ),
    )

    op.create_table(
        "activity_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "repository_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "investigation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("investigations.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("stage", sa.String(100), nullable=False),
        sa.Column("level", sa.String(20), server_default="info"),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "kind",
            sa.Enum("import_repo", "reindex", "cleanup", "retrieve", name="job_kind"),
            nullable=False,
        ),
        sa.Column(
            "repository_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("repositories.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "investigation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("investigations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "queued",
                "running",
                "completed",
                "failed",
                "interrupted",
                name="job_status",
            ),
            nullable=False,
        ),
        sa.Column("attempt", sa.Integer(), server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("jobs")
    op.drop_table("activity_events")
    op.drop_table("evidence")
    op.drop_table("investigations")
    op.drop_table("code_chunks")
    op.drop_table("source_files")
    op.drop_constraint("fk_repositories_active_index", "repositories", type_="foreignkey")
    op.drop_table("repository_indexes")
    op.drop_table("repositories")
    for name in (
        "job_status",
        "job_kind",
        "match_reason",
        "investigation_status",
        "index_status",
        "repository_status",
    ):
        sa.Enum(name=name).drop(op.get_bind(), checkfirst=True)
