from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import asyncpg


@dataclass(frozen=True)
class GuildSettings:
    guild_id: int
    control_channel_id: int | None
    audit_channel_id: int | None
    panel_message_id: int | None
    absence_tolerance_seconds: int
    control_message_ttl_seconds: int


@dataclass(frozen=True)
class TimeEntry:
    id: int
    guild_id: int
    user_id: int
    started_at: datetime
    ended_at: datetime | None
    status: str
    pause_started_at: datetime | None
    pause_reason: str | None
    pause_total_seconds: int
    absence_started_at: datetime | None
    absence_deadline_at: datetime | None
    close_reason: str | None
    status_message_id: int | None


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


def utcnow() -> datetime:
    return datetime.now(UTC)


def worked_seconds(entry: TimeEntry, at: datetime | None = None) -> int:
    now = at or utcnow()
    end = entry.ended_at or now
    pause_total = entry.pause_total_seconds
    if entry.status == "paused" and entry.pause_started_at:
        pause_total += _seconds_between(entry.pause_started_at, now)
    return max(0, _seconds_between(entry.started_at, end) - pause_total)


def _seconds_between(start: datetime | None, end: datetime) -> int:
    if not start:
        return 0
    return max(0, int((end - start).total_seconds()))


def _settings_from_row(row: asyncpg.Record) -> GuildSettings:
    return GuildSettings(
        guild_id=int(row["guild_id"]),
        control_channel_id=_optional_int(row["control_channel_id"]),
        audit_channel_id=_optional_int(row["audit_channel_id"]),
        panel_message_id=_optional_int(row["panel_message_id"]),
        absence_tolerance_seconds=int(row["absence_tolerance_seconds"]),
        control_message_ttl_seconds=int(row["control_message_ttl_seconds"]),
    )


def _entry_from_row(row: asyncpg.Record) -> TimeEntry:
    return TimeEntry(
        id=int(row["id"]),
        guild_id=int(row["guild_id"]),
        user_id=int(row["user_id"]),
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        status=row["status"],
        pause_started_at=row["pause_started_at"],
        pause_reason=row["pause_reason"],
        pause_total_seconds=int(row["pause_total_seconds"]),
        absence_started_at=row["absence_started_at"],
        absence_deadline_at=row["absence_deadline_at"],
        close_reason=row["close_reason"],
        status_message_id=_optional_int(row["status_message_id"]),
    )


def _optional_int(value: Any) -> int | None:
    return int(value) if value is not None else None
