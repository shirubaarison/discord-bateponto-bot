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
