from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    import discord

    from bate_ponto_bot.models import TimeEntry

LOCAL_TIMEZONE = ZoneInfo("America/Fortaleza")


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


def format_dt(value: datetime | None) -> str:
    if value is None:
        return "desconhecido"
    local_value = value.astimezone(LOCAL_TIMEZONE)
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
