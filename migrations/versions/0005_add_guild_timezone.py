"""Store a time zone per guild instead of assuming America/Fortaleza.

Revision ID: 0005_add_guild_timezone
Revises: 0004_add_dashboard_users
Create Date: 2026-10-30
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005_add_guild_timezone"
down_revision: str | None = "0004_add_dashboard_users"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing guilds keep the previous hardcoded behavior.
    op.execute(
        """
        ALTER TABLE guild_settings
            ADD COLUMN IF NOT EXISTS timezone TEXT NOT NULL DEFAULT 'America/Fortaleza'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE guild_settings
            DROP COLUMN IF EXISTS timezone
        """
    )
