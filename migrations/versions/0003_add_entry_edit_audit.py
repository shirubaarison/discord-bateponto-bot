"""Track administrative time-entry edits."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003_add_entry_edit_audit"
down_revision: str | None = "0002_add_status_message_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS time_entry_edits (
            id BIGSERIAL PRIMARY KEY,
            entry_id BIGINT NOT NULL REFERENCES time_entries(id) ON DELETE CASCADE,
            editor_username TEXT NOT NULL,
            edited_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            old_started_at TIMESTAMPTZ NOT NULL,
            old_ended_at TIMESTAMPTZ,
            new_started_at TIMESTAMPTZ NOT NULL,
            new_ended_at TIMESTAMPTZ NOT NULL,
            old_pause_total_seconds INTEGER NOT NULL,
            new_pause_total_seconds INTEGER NOT NULL,
            old_close_reason TEXT,
            new_close_reason TEXT NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_time_entry_edits_entry
            ON time_entry_edits (entry_id, edited_at DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS time_entry_edits")
