"""per-account Xiaoling dialogue routes

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-13
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dialogue_routes",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("public_peer_name", sa.String(length=40), nullable=False),
        sa.Column("public_peer_token", sa.String(length=64), nullable=False),
        sa.Column("admin_thread_id", sa.String(length=64), nullable=True),
        sa.Column("public_thread_id", sa.String(length=64), nullable=True),
        sa.Column("distill_thread_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
        sa.UniqueConstraint("public_peer_name"),
    )


def downgrade() -> None:
    op.drop_table("dialogue_routes")
