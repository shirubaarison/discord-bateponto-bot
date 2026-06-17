"""Create initial time clock schema.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-06-16
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_settings (
            guild_id BIGINT PRIMARY KEY,
            control_channel_id BIGINT,
            audit_channel_id BIGINT,
            panel_message_id BIGINT,
            absence_tolerance_seconds INTEGER NOT NULL DEFAULT 300,
            control_message_ttl_seconds INTEGER NOT NULL DEFAULT 30,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        ALTER TABLE guild_settings
            ADD COLUMN IF NOT EXISTS control_message_ttl_seconds INTEGER NOT NULL DEFAULT 30
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS allowed_voice_channels (
            guild_id BIGINT NOT NULL,
            channel_id BIGINT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (guild_id, channel_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS time_entries (
            id BIGSERIAL PRIMARY KEY,
            guild_id BIGINT NOT NULL,
            user_id BIGINT NOT NULL,
            started_at TIMESTAMPTZ NOT NULL,
            ended_at TIMESTAMPTZ,
            status TEXT NOT NULL CHECK (status IN ('active', 'paused', 'closed')),
            pause_started_at TIMESTAMPTZ,
            pause_reason TEXT,
            pause_total_seconds INTEGER NOT NULL DEFAULT 0,
            absence_started_at TIMESTAMPTZ,
            absence_deadline_at TIMESTAMPTZ,
            close_reason TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        ALTER TABLE time_entries
            ADD COLUMN IF NOT EXISTS close_reason TEXT
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_time_entries_one_open
            ON time_entries (guild_id, user_id)
            WHERE ended_at IS NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_time_entries_user_history
            ON time_entries (guild_id, user_id, started_at DESC)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS time_events (
            id BIGSERIAL PRIMARY KEY,
            entry_id BIGINT REFERENCES time_entries(id) ON DELETE CASCADE,
            guild_id BIGINT NOT NULL,
            user_id BIGINT NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TIMESTAMPTZ NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_time_events_entry
            ON time_events (entry_id, occurred_at)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS time_events")
    op.execute("DROP TABLE IF EXISTS time_entries")
    op.execute("DROP TABLE IF EXISTS allowed_voice_channels")
    op.execute("DROP TABLE IF EXISTS guild_settings")
