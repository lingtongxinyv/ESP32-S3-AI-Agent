"""drop conversation session unique constraint

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-12
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.drop_constraint("uq_conversation_device_session", type_="unique")


def downgrade() -> None:
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.create_unique_constraint(
            "uq_conversation_device_session", ["device_id", "session"]
        )
