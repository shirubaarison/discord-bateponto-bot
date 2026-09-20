from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import asyncpg

from bate_ponto_bot.models import (
    GuildSettings,
    TimeEntry,
    UserTimeInfo,
    WeeklyHours,
    _entry_from_row,
    _settings_from_row,
    _user_time_info_from_row,
    _weekly_hours_from_row,
)
from bate_ponto_bot.utils import _seconds_between, utcnow, worked_seconds

__all__ = [
    "Database",
    "GuildSettings",
    "TimeEntry",
    "UserTimeInfo",
    "WeeklyHours",
    "utcnow",
    "worked_seconds",
]


class Database:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self.pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(self._database_url)

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()

    async def fetchrow(self, query: str, *args: Any) -> asyncpg.Record | None:
        assert self.pool is not None
        return await self.pool.fetchrow(query, *args)

    async def fetch(self, query: str, *args: Any) -> list[asyncpg.Record]:
        assert self.pool is not None
        return await self.pool.fetch(query, *args)

    async def execute(self, query: str, *args: Any) -> str:
        assert self.pool is not None
        return await self.pool.execute(query, *args)

    async def _add_event(
        self,
        conn: asyncpg.Connection,
        entry: TimeEntry,
        event_type: str,
        occurred_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await conn.execute(
            """
            INSERT INTO time_events (entry_id, guild_id, user_id, event_type, occurred_at, metadata)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb)
            """,
            entry.id,
            entry.guild_id,
            entry.user_id,
            event_type,
            occurred_at or utcnow(),
            json.dumps(metadata or {}),
        )

    async def get_settings(self, guild_id: int) -> GuildSettings:
        row = await self.fetchrow(
            """
            INSERT INTO guild_settings (guild_id)
            VALUES ($1)
            ON CONFLICT (guild_id) DO UPDATE SET updated_at = guild_settings.updated_at
            RETURNING *
            """,
            guild_id,
        )
        assert row is not None
        return _settings_from_row(row)

    async def get_settings_for_control_channel(
        self,
        guild_id: int,
        channel_id: int,
    ) -> GuildSettings | None:
        row = await self.fetchrow(
            """
            SELECT *
            FROM guild_settings
            WHERE guild_id = $1 AND control_channel_id = $2
            """,
            guild_id,
            channel_id,
        )
        return _settings_from_row(row) if row else None

    async def set_control_channel(
        self, guild_id: int, channel_id: int, panel_message_id: int | None = None
    ) -> None:
        await self.execute(
            """
            INSERT INTO guild_settings (guild_id, control_channel_id, panel_message_id)
            VALUES ($1, $2, $3)
            ON CONFLICT (guild_id) DO UPDATE SET
                control_channel_id = EXCLUDED.control_channel_id,
                panel_message_id = COALESCE(
                    EXCLUDED.panel_message_id,
                    guild_settings.panel_message_id
                ),
                updated_at = now()
            """,
            guild_id,
            channel_id,
            panel_message_id,
        )

    async def set_panel_message(self, guild_id: int, message_id: int) -> None:
        await self.execute(
            """
            UPDATE guild_settings
            SET panel_message_id = $2, updated_at = now()
            WHERE guild_id = $1
            """,
            guild_id,
            message_id,
        )

    async def set_audit_channel(self, guild_id: int, channel_id: int) -> None:
        await self.execute(
            """
            INSERT INTO guild_settings (guild_id, audit_channel_id)
            VALUES ($1, $2)
            ON CONFLICT (guild_id) DO UPDATE SET
                audit_channel_id = EXCLUDED.audit_channel_id,
                updated_at = now()
            """,
            guild_id,
            channel_id,
        )

    async def set_tolerance(self, guild_id: int, seconds: int) -> None:
        await self.execute(
            """
            INSERT INTO guild_settings (guild_id, absence_tolerance_seconds)
            VALUES ($1, $2)
            ON CONFLICT (guild_id) DO UPDATE SET
                absence_tolerance_seconds = EXCLUDED.absence_tolerance_seconds,
                updated_at = now()
            """,
            guild_id,
            seconds,
        )

    async def set_control_message_ttl(self, guild_id: int, seconds: int) -> None:
        await self.execute(
            """
            INSERT INTO guild_settings (guild_id, control_message_ttl_seconds)
            VALUES ($1, $2)
            ON CONFLICT (guild_id) DO UPDATE SET
                control_message_ttl_seconds = EXCLUDED.control_message_ttl_seconds,
                updated_at = now()
            """,
            guild_id,
            seconds,
        )

    async def add_allowed_voice_channel(self, guild_id: int, channel_id: int) -> None:
        await self.execute(
            """
            INSERT INTO allowed_voice_channels (guild_id, channel_id)
            VALUES ($1, $2)
            ON CONFLICT DO NOTHING
            """,
            guild_id,
            channel_id,
        )

    async def remove_allowed_voice_channel(self, guild_id: int, channel_id: int) -> None:
        await self.execute(
            """
            DELETE FROM allowed_voice_channels
            WHERE guild_id = $1 AND channel_id = $2
            """,
            guild_id,
            channel_id,
        )

    async def list_allowed_voice_channels(self, guild_id: int) -> set[int]:
        rows = await self.fetch(
            """
            SELECT channel_id
            FROM allowed_voice_channels
            WHERE guild_id = $1
            """,
            guild_id,
        )
        return {int(row["channel_id"]) for row in rows}

    async def start_entry(self, guild_id: int, user_id: int) -> TimeEntry:
        assert self.pool is not None
        now = utcnow()
        async with self.pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO time_entries (guild_id, user_id, started_at, status)
                VALUES ($1, $2, $3, 'active')
                RETURNING *
                """,
                guild_id,
                user_id,
                now,
            )
            assert row is not None
            entry = _entry_from_row(row)
            await self._add_event(conn, entry, "started", now)
            return entry

    async def get_open_entry(self, guild_id: int, user_id: int) -> TimeEntry | None:
        row = await self.fetchrow(
            """
            SELECT *
            FROM time_entries
            WHERE guild_id = $1 AND user_id = $2 AND ended_at IS NULL
            """,
            guild_id,
            user_id,
        )
        return _entry_from_row(row) if row else None

    async def get_entry(self, entry_id: int) -> TimeEntry | None:
        row = await self.fetchrow(
            """
            SELECT *
            FROM time_entries
            WHERE id = $1
            """,
            entry_id,
        )
        return _entry_from_row(row) if row else None

    async def set_status_message(self, entry_id: int, message_id: int | None) -> None:
        await self.execute(
            """
            UPDATE time_entries
            SET status_message_id = $2
            WHERE id = $1
            """,
            entry_id,
            message_id,
        )

    async def finish_entry(
        self,
        entry: TimeEntry,
        ended_at: datetime | None = None,
        close_reason: str | None = None,
    ) -> TimeEntry:
        assert self.pool is not None
        now = ended_at or utcnow()
        extra_pause = (
            _seconds_between(entry.pause_started_at, now) if entry.status == "paused" else 0
        )
        async with self.pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                """
                UPDATE time_entries
                SET ended_at = $2,
                    status = 'closed',
                    pause_total_seconds = pause_total_seconds + $3,
                    pause_started_at = NULL,
                    pause_reason = NULL,
                    absence_started_at = NULL,
                    absence_deadline_at = NULL,
                    close_reason = $4
                WHERE id = $1 AND ended_at IS NULL
                RETURNING *
                """,
                entry.id,
                now,
                extra_pause,
                close_reason,
            )
            was_updated = row is not None
            if row is None:
                row = await conn.fetchrow("SELECT * FROM time_entries WHERE id = $1", entry.id)
            assert row is not None
            updated = _entry_from_row(row)

            if was_updated:
                await self._add_event(conn, updated, "finished", now)
                if close_reason == "auto_absence":
                    await self._add_event(conn, updated, "auto_absence_closed", now)

            return updated

    async def finish_expired_paused_entries(
        self, pause_cutoff: datetime, ended_at: datetime | None = None
    ) -> list[TimeEntry]:
        """Close paused entries whose current pause started before ``pause_cutoff``."""
        assert self.pool is not None
        now = ended_at or utcnow()
        async with self.pool.acquire() as conn, conn.transaction():
            rows = await conn.fetch(
                """
                UPDATE time_entries
                SET ended_at = $1,
                    status = 'closed',
                    pause_total_seconds = pause_total_seconds
                        + EXTRACT(EPOCH FROM ($1 - pause_started_at))::INTEGER,
                    pause_started_at = NULL,
                    pause_reason = NULL,
                    absence_started_at = NULL,
                    absence_deadline_at = NULL,
                    close_reason = 'pause_timeout'
                WHERE status = 'paused'
                  AND ended_at IS NULL
                  AND pause_started_at IS NOT NULL
                  AND pause_started_at <= $2
                RETURNING *
                """,
                now,
                pause_cutoff,
            )
            entries = [_entry_from_row(row) for row in rows]
            for entry in entries:
                await self._add_event(
                    conn,
                    entry,
                    "pause_timeout_closed",
                    now,
                    {"pause_limit_seconds": 24 * 60 * 60},
                )
            return entries

    async def finish_expired_active_entries(
        self, started_cutoff: datetime
    ) -> list[TimeEntry]:
        """Close active entries at the 24-hour maximum from their start time."""
        assert self.pool is not None
        async with self.pool.acquire() as conn, conn.transaction():
            rows = await conn.fetch(
                """
                UPDATE time_entries
                SET ended_at = started_at + INTERVAL '24 hours',
                    status = 'closed',
                    absence_started_at = NULL,
                    absence_deadline_at = NULL,
                    close_reason = 'active_timeout'
                WHERE status = 'active'
                  AND ended_at IS NULL
                  AND started_at <= $1
                RETURNING *
                """,
                started_cutoff,
            )
            entries = [_entry_from_row(row) for row in rows]
            for entry in entries:
                await self._add_event(
                    conn,
                    entry,
                    "active_timeout_closed",
                    entry.ended_at,
                    {"active_limit_seconds": 24 * 60 * 60},
                )
            return entries

    async def pause_entry(
        self, entry: TimeEntry, reason: str, pause_started_at: datetime | None = None
    ) -> TimeEntry:
        if entry.status != "active":
            return entry

        assert self.pool is not None
        started_at = pause_started_at or utcnow()
        async with self.pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                """
                UPDATE time_entries
                SET status = 'paused',
                    pause_started_at = $2,
                    pause_reason = $3,
                    absence_started_at = NULL,
                    absence_deadline_at = NULL
                WHERE id = $1 AND status = 'active' AND ended_at IS NULL
                RETURNING *
                """,
                entry.id,
                started_at,
                reason,
            )
            if row is None:
                return entry
            updated = _entry_from_row(row)
            await self._add_event(conn, updated, "paused", started_at, {"reason": reason})
            return updated

    async def resume_entry(self, entry: TimeEntry) -> TimeEntry:
        if entry.status != "paused":
            return entry

        assert self.pool is not None
        now = utcnow()
        pause_seconds = _seconds_between(entry.pause_started_at, now)
        async with self.pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                """
                UPDATE time_entries
                SET status = 'active',
                    pause_total_seconds = pause_total_seconds + $2,
                    pause_started_at = NULL,
                    pause_reason = NULL,
                    absence_started_at = NULL,
                    absence_deadline_at = NULL
                WHERE id = $1 AND status = 'paused' AND ended_at IS NULL
                RETURNING *
                """,
                entry.id,
                pause_seconds,
            )
            if row is None:
                return entry
            updated = _entry_from_row(row)
            await self._add_event(conn, updated, "resumed", now)
            return updated

    async def set_absence_deadline(
        self, entry: TimeEntry, started_at: datetime, deadline_at: datetime
    ) -> TimeEntry:
        row = await self.fetchrow(
            """
            UPDATE time_entries
            SET absence_started_at = $2, absence_deadline_at = $3
            WHERE id = $1 AND status = 'active'
            RETURNING *
            """,
            entry.id,
            started_at,
            deadline_at,
        )
        assert row is not None
        return _entry_from_row(row)

    async def clear_absence(self, entry: TimeEntry) -> TimeEntry:
        row = await self.fetchrow(
            """
            UPDATE time_entries
            SET absence_started_at = NULL, absence_deadline_at = NULL
            WHERE id = $1
            RETURNING *
            """,
            entry.id,
        )
        assert row is not None
        return _entry_from_row(row)

    async def list_history(self, guild_id: int, user_id: int, limit: int = 5) -> list[TimeEntry]:
        rows = await self.fetch(
            """
            SELECT *
            FROM time_entries
            WHERE guild_id = $1 AND user_id = $2 AND ended_at IS NOT NULL
            ORDER BY started_at DESC
            LIMIT $3
            """,
            guild_id,
            user_id,
            limit,
        )
        return [_entry_from_row(row) for row in rows]

    async def list_weekly_hours(
        self, guild_id: int, user_id: int, limit: int = 12
    ) -> list[WeeklyHours]:
        """Return worked time grouped by the local week in which each shift started."""
        rows = await self.fetch(
            """
            WITH entry_totals AS (
                SELECT
                    date_trunc(
                        'week',
                        started_at AT TIME ZONE 'America/Fortaleza'
                    )::date AS week_start,
                    GREATEST(
                        0,
                        EXTRACT(EPOCH FROM (COALESCE(ended_at, now()) - started_at))::BIGINT
                        - pause_total_seconds::BIGINT
                        - CASE
                            WHEN status = 'paused' AND pause_started_at IS NOT NULL
                            THEN EXTRACT(EPOCH FROM (now() - pause_started_at))::BIGINT
                            ELSE 0
                          END
                    ) AS worked_seconds
                FROM time_entries
                WHERE guild_id = $1 AND user_id = $2
            )
            SELECT week_start, SUM(worked_seconds)::BIGINT AS worked_seconds
            FROM entry_totals
            GROUP BY week_start
            ORDER BY week_start DESC
            LIMIT $3
            """,
            guild_id,
            user_id,
            limit,
        )
        return [_weekly_hours_from_row(row) for row in rows]

    async def get_user_time_info(self, guild_id: int, user_id: int) -> UserTimeInfo | None:
        row = await self.fetchrow(
            """
            SELECT
                MIN(started_at) AS first_started_at,
                MAX(COALESCE(ended_at, started_at)) AS last_activity_at,
                COUNT(*)::BIGINT AS shifts_count,
                COALESCE(
                    SUM(
                        GREATEST(
                            0,
                            EXTRACT(
                                EPOCH FROM (COALESCE(ended_at, now()) - started_at)
                            )::BIGINT
                            - pause_total_seconds::BIGINT
                            - CASE
                                WHEN status = 'paused' AND pause_started_at IS NOT NULL
                                THEN EXTRACT(EPOCH FROM (now() - pause_started_at))::BIGINT
                                ELSE 0
                              END
                        )
                    ),
                    0
                )::BIGINT AS worked_seconds
            FROM time_entries
            WHERE guild_id = $1 AND user_id = $2
            """,
            guild_id,
            user_id,
        )
        if row is None or row["first_started_at"] is None:
            return None
        return _user_time_info_from_row(row)

    async def list_pending_absences(self) -> list[TimeEntry]:
        rows = await self.fetch(
            """
            SELECT *
            FROM time_entries
            WHERE ended_at IS NULL
              AND status = 'active'
              AND absence_deadline_at IS NOT NULL
            """
        )
        return [_entry_from_row(row) for row in rows]

    async def claim_unnotified_auto_absence_closure(
        self,
        guild_id: int,
        user_id: int,
    ) -> TimeEntry | None:
        assert self.pool is not None
        async with self.pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT te.*
                FROM time_entries te
                WHERE te.guild_id = $1
                  AND te.user_id = $2
                  AND te.ended_at IS NOT NULL
                  AND (
                      te.close_reason = 'auto_absence'
                      OR EXISTS (
                          SELECT 1
                          FROM time_events closed_event
                          WHERE closed_event.entry_id = te.id
                            AND closed_event.event_type = 'auto_absence_closed'
                      )
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM time_events notified_event
                      WHERE notified_event.entry_id = te.id
                        AND notified_event.event_type = 'auto_absence_return_notified'
                  )
                ORDER BY te.ended_at DESC
                LIMIT 1
                FOR UPDATE OF te SKIP LOCKED
                """,
                guild_id,
                user_id,
            )
            if row is None:
                return None

            entry = _entry_from_row(row)
            await self._add_event(conn, entry, "auto_absence_return_notified")
            return entry

    async def add_event(
        self,
        entry: TimeEntry,
        event_type: str,
        occurred_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self.execute(
            """
            INSERT INTO time_events (entry_id, guild_id, user_id, event_type, occurred_at, metadata)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb)
            """,
            entry.id,
            entry.guild_id,
            entry.user_id,
            event_type,
            occurred_at or utcnow(),
            json.dumps(metadata or {}),
        )
