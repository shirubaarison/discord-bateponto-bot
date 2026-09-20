from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
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


@dataclass(frozen=True)
class WeeklyHours:
    week_start: date
    worked_seconds: int


@dataclass(frozen=True)
class UserTimeInfo:
    first_started_at: datetime
    last_activity_at: datetime
    worked_seconds: int
    shifts_count: int


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


def _weekly_hours_from_row(row: asyncpg.Record) -> WeeklyHours:
    return WeeklyHours(
        week_start=row["week_start"],
        worked_seconds=int(row["worked_seconds"]),
    )


def _user_time_info_from_row(row: asyncpg.Record) -> UserTimeInfo:
    return UserTimeInfo(
        first_started_at=row["first_started_at"],
        last_activity_at=row["last_activity_at"],
        worked_seconds=int(row["worked_seconds"]),
        shifts_count=int(row["shifts_count"]),
    )


def _optional_int(value: Any) -> int | None:
    return int(value) if value is not None else None
