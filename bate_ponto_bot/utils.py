from __future__ import annotations

from datetime import UTC, datetime
from functools import cache
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

if TYPE_CHECKING:
    import discord

    from bate_ponto_bot.models import TimeEntry

DEFAULT_TIMEZONE_NAME = "America/Fortaleza"
DEFAULT_TIMEZONE = ZoneInfo(DEFAULT_TIMEZONE_NAME)


@cache
def timezone_names() -> tuple[str, ...]:
    """IANA time zone names users can pick, e.g. ``America/Sao_Paulo``."""
    return tuple(
        sorted(
            name
            for name in available_timezones()
            if name == "UTC" or ("/" in name and not name.startswith(("Etc/", "SystemV/")))
        )
    )


def canonical_timezone_name(value: str) -> str | None:
    """Match user input to a known time zone name, ignoring case."""
    wanted = value.strip().replace(" ", "_").casefold()
    return next((name for name in timezone_names() if name.casefold() == wanted), None)


def load_timezone(name: str | None) -> ZoneInfo:
    """Return the ZoneInfo for a stored name, falling back to the default."""
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return DEFAULT_TIMEZONE


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


def format_seconds(total_seconds: int) -> str:
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def format_dt(value: datetime | None, tz: ZoneInfo = DEFAULT_TIMEZONE) -> str:
    if value is None:
        return "desconhecido"
    # PostgreSQL TIMESTAMPTZ values are normally timezone-aware. Treat a naive
    # value as UTC as a defensive fallback instead of inheriting the host TZ.
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    local_value = value.astimezone(tz)
    month_names = [
        "janeiro",
        "fevereiro",
        "março",
        "abril",
        "maio",
        "junho",
        "julho",
        "agosto",
        "setembro",
        "outubro",
        "novembro",
        "dezembro",
    ]
    month = month_names[local_value.month - 1]
    return (
        f"{local_value.day} de {month} de {local_value.year} "
        f"às {local_value.hour:02d}:{local_value.minute:02d}"
    )


def format_duration_words(total_seconds: int) -> str:
    minutes, seconds = divmod(total_seconds, 60)
    parts: list[str] = []
    if minutes:
        suffix = "s" if minutes != 1 else ""
        parts.append(f"{minutes} minuto{suffix}")
    if seconds:
        suffix = "s" if seconds != 1 else ""
        parts.append(f"{seconds} segundo{suffix}")
    return " e ".join(parts) if parts else "0 segundos"


def channel_mention(guild: discord.Guild, channel_id: int | None) -> str:
    if not channel_id:
        return "não configurado"
    channel = guild.get_channel(channel_id)
    return channel.mention if channel else f"`{channel_id}`"
