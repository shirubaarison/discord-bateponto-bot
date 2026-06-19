"""Persist open point status message IDs.

Revision ID: 0002_add_status_message_id
Revises: 0001_initial_schema
Create Date: 2026-06-16
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_add_status_message_id"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE time_entries
            ADD COLUMN IF NOT EXISTS status_message_id BIGINT
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE time_entries
            DROP COLUMN IF EXISTS status_message_id
        """
    )
