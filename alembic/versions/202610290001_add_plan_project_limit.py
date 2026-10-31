"""Add the active-project cap to plan configuration.

Revision ID: 202610290001
Revises: 202610280001
Create Date: 2026-10-29 12:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202610290001"
down_revision = "202610280001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "plans",
        sa.Column("max_active_projects", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("plans", "max_active_projects")