"""Store dashboard accounts created through Discord login and the guilds they manage.

Revision ID: 0004_add_dashboard_users
Revises: 0003_add_entry_edit_audit
Create Date: 2026-10-30
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004_add_dashboard_users"
down_revision: str | None = "0003_add_entry_edit_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS dashboard_users (
            discord_user_id BIGINT PRIMARY KEY,
            username TEXT NOT NULL,
            display_name TEXT,
            avatar_hash TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_login_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS dashboard_user_guilds (
            discord_user_id BIGINT NOT NULL
                REFERENCES dashboard_users(discord_user_id) ON DELETE CASCADE,
            guild_id BIGINT NOT NULL,
            refreshed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (discord_user_id, guild_id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_dashboard_user_guilds_guild
            ON dashboard_user_guilds (guild_id)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS dashboard_user_guilds")
    op.execute("DROP TABLE IF EXISTS dashboard_users")
