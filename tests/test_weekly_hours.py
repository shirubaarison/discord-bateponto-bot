from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock

from bate_ponto_bot.db import Database
from bate_ponto_bot.models import UserTimeInfo, WeeklyHours
from bate_ponto_bot.views import TimeClockView, current_week_worked_seconds, weekly_hours_fields


def test_control_panel_includes_weekly_information_button() -> None:
    async def get_button_labels() -> list[str | None]:
        view = TimeClockView(MagicMock())
        try:
            return [item.label for item in view.children]
        finally:
            view.stop()

    labels = asyncio.run(get_button_labels())

    assert "Ver informações" in labels


def test_list_weekly_hours_maps_database_rows() -> None:
    database = Database("postgresql://unused")
    database.fetch = AsyncMock(
        return_value=[
            {"week_start": date(2026, 9, 14), "worked_seconds": 99_000},
            {"week_start": date(2026, 9, 7), "worked_seconds": 36_000},
        ]
    )

    result = asyncio.run(database.list_weekly_hours(111, 222))

    assert result == [
        WeeklyHours(week_start=date(2026, 9, 14), worked_seconds=99_000),
        WeeklyHours(week_start=date(2026, 9, 7), worked_seconds=36_000),
    ]
    database.fetch.assert_awaited_once()
    assert database.fetch.await_args.args[-3:] == (111, 222, 12)


def test_weekly_hours_fields_formats_week_ranges_and_durations() -> None:
    fields = weekly_hours_fields(
        [WeeklyHours(week_start=date(2026, 9, 14), worked_seconds=99_000)],
        current_week_start=date(2026, 9, 14),
    )

    assert fields == [("Semana vigente • 14/09/2026 a 20/09/2026", "**27:30:00**", False)]


def test_current_week_worked_seconds_defaults_to_zero() -> None:
    items = [WeeklyHours(week_start=date(2026, 9, 7), worked_seconds=36_000)]

    assert current_week_worked_seconds(items, week_start=date(2026, 9, 14)) == 0


def test_get_user_time_info_maps_database_row() -> None:
    database = Database("postgresql://unused")
    first = datetime(2026, 7, 4, 20, 13, tzinfo=UTC)
    last = datetime(2026, 9, 18, 16, 11, tzinfo=UTC)
    database.fetchrow = AsyncMock(
        return_value={
            "first_started_at": first,
            "last_activity_at": last,
            "worked_seconds": 466_615,
            "shifts_count": 42,
        }
    )

    result = asyncio.run(database.get_user_time_info(111, 222))

    assert result == UserTimeInfo(
        first_started_at=first,
        last_activity_at=last,
        worked_seconds=466_615,
        shifts_count=42,
    )


def test_welcome_embed_lists_setup_commands_and_dashboard() -> None:
    from bate_ponto_bot.bot import build_welcome_embed

    without_url = build_welcome_embed().description or ""
    assert "/config painel" in without_url
    assert "/config voz-adicionar" in without_url
    assert "/config fuso" in without_url
    assert "http" not in without_url

    assert "https://ponto.example.com" in (
        build_welcome_embed("https://ponto.example.com").description or ""
    )


def test_pick_welcome_channel_prefers_writable_system_channel() -> None:
    from bate_ponto_bot.bot import pick_welcome_channel

    def channel(position: int, *, can_send: bool) -> MagicMock:
        mock = MagicMock(position=position)
        mock.permissions_for.return_value = MagicMock(view_channel=True, send_messages=can_send)
        return mock

    locked_system = channel(0, can_send=False)
    first, second = channel(2, can_send=True), channel(1, can_send=True)
    guild = MagicMock(system_channel=locked_system, text_channels=[first, second])
    assert pick_welcome_channel(guild) is second

    open_system = channel(5, can_send=True)
    guild.system_channel = open_system
    assert pick_welcome_channel(guild) is open_system

    guild.system_channel = None
    guild.text_channels = [channel(0, can_send=False)]
    assert pick_welcome_channel(guild) is None
