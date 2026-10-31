"""Add explicit customer visibility for project plans.

Revision ID: 202610280001
Revises: 202610270001
Create Date: 2026-10-28 12:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202610280001"
down_revision = "202610270001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "plans",
        sa.Column("is_selectable", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("plans", "is_selectable")