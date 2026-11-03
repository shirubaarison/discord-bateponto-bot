from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from bate_ponto_bot.commands import timezone_choices
from bate_ponto_bot.models import _settings_from_row
from bate_ponto_bot.utils import (
    DEFAULT_TIMEZONE,
    canonical_timezone_name,
    format_dt,
    load_timezone,
)
from bate_ponto_bot.web.app import parse_date_filters, parse_datetime_local_input
from bate_ponto_bot.web.queries import get_active_entries, get_user_weekly_totals

LISBON = ZoneInfo("Europe/Lisbon")


def test_canonical_timezone_name_ignores_case_and_spaces() -> None:
    assert canonical_timezone_name("america/sao paulo") == "America/Sao_Paulo"
    assert canonical_timezone_name("UTC") == "UTC"
    assert canonical_timezone_name("Mars/Olympus_Mons") is None


def test_load_timezone_falls_back_to_default() -> None:
    assert load_timezone("Europe/Lisbon") == LISBON
    assert load_timezone("not/a-zone") == DEFAULT_TIMEZONE
    assert load_timezone(None) == DEFAULT_TIMEZONE


def test_format_dt_uses_given_timezone() -> None:
    value = datetime(2026, 9, 19, 15, 30, tzinfo=UTC)

    assert format_dt(value) == "19 de setembro de 2026 às 12:30"
    assert format_dt(value, LISBON) == "19 de setembro de 2026 às 16:30"


def test_timezone_choices_suggest_and_filter() -> None:
    suggested = [choice.value for choice in timezone_choices("")]
    assert suggested[0] == "America/Sao_Paulo"

    filtered = [choice.value for choice in timezone_choices("lisbon")]
    assert filtered == ["Europe/Lisbon"]
    assert len(timezone_choices("a")) == 25


def test_settings_row_defaults_timezone_for_old_rows() -> None:
    row = {
        "guild_id": 1,
        "control_channel_id": None,
        "audit_channel_id": None,
        "panel_message_id": None,
        "absence_tolerance_seconds": 300,
        "control_message_ttl_seconds": 30,
    }
    assert _settings_from_row(row).timezone == "America/Fortaleza"  # type: ignore[arg-type]
    assert _settings_from_row({**row, "timezone": "Europe/Lisbon"}).timezone == "Europe/Lisbon"  # type: ignore[arg-type]


def test_date_filters_use_guild_timezone() -> None:
    start_dt, end_dt, _ = parse_date_filters(None, "2026-06-01", "2026-06-01", LISBON)

    assert start_dt == datetime(2026, 5, 31, 23, 0, tzinfo=UTC)
    assert end_dt is not None
    assert end_dt.astimezone(LISBON).date().isoformat() == "2026-06-01"


def test_datetime_local_input_uses_guild_timezone() -> None:
    assert parse_datetime_local_input("2026-06-01T09:00", LISBON) == datetime(
        2026, 6, 1, 8, 0, tzinfo=UTC
    )


def test_dashboard_formats_entries_in_each_guild_timezone() -> None:
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    started_at = datetime(2026, 9, 19, 15, 30, tzinfo=UTC)
    entry = {
        "id": 1,
        "user_id": 222,
        "status": "active",
        "started_at": started_at,
        "ended_at": None,
        "pause_started_at": None,
        "pause_total_seconds": 0,
    }
    cursor.fetchall.side_effect = [
        [{**entry, "guild_id": 111}, {**entry, "id": 2, "guild_id": 333}],
        [{"guild_id": 333, "timezone": "Europe/Lisbon"}],
    ]

    result = get_active_entries(connection)

    assert result[0]["started_at_formatted"] == "19 de setembro de 2026 às 12:30"
    assert result[1]["started_at_formatted"] == "19 de setembro de 2026 às 16:30"


def test_weekly_totals_query_uses_guild_timezone() -> None:
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = []

    get_user_weekly_totals(connection, user_id=222, guild_id=111)

    query = cursor.execute.call_args.args[0]
    assert "guild_settings" in query
    assert "AT TIME ZONE tz" in query
