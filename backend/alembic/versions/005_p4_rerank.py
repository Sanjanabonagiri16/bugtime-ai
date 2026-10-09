"""P4 investigation reranker columns

Revision ID: 005
Revises: 004
Create Date: 2026-10-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "investigations",
        sa.Column("reranker_mode", sa.String(32), nullable=False, server_default="disabled"),
    )
    op.add_column(
        "investigations",
        sa.Column("reranker_model_path", sa.String(500), nullable=True),
    )
    op.add_column(
        "investigations",
        sa.Column("reranker_checkpoint_hash", sa.String(64), nullable=True),
    )
    op.add_column(
        "investigations",
        sa.Column("reranker_fallback", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("investigations", "reranker_fallback")
    op.drop_column("investigations", "reranker_checkpoint_hash")
    op.drop_column("investigations", "reranker_model_path")
    op.drop_column("investigations", "reranker_mode")
