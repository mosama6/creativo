"""Allow several outputs on one generation.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "generation_outputs",
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
    )
    op.drop_constraint("generation_outputs_generation_id_key", "generation_outputs", type_="unique")
    op.create_unique_constraint(
        "uq_generation_outputs_generation_position",
        "generation_outputs",
        ["generation_id", "position"],
    )
    op.alter_column("generation_outputs", "position", server_default=None)


def downgrade() -> None:
    op.drop_constraint(
        "uq_generation_outputs_generation_position", "generation_outputs", type_="unique"
    )
    op.create_unique_constraint(
        "generation_outputs_generation_id_key", "generation_outputs", ["generation_id"]
    )
    op.drop_column("generation_outputs", "position")
