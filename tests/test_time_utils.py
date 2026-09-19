from __future__ import annotations

from datetime import UTC, datetime, timedelta

from bate_ponto_bot.bot import format_dt, format_duration_words, format_seconds
from bate_ponto_bot.db import TimeEntry, worked_seconds


def make_entry(**overrides) -> TimeEntry:
    values = {
        "id": 1,
        "guild_id": 10,
        "user_id": 20,
        "started_at": datetime(2026, 6, 18, 8, 0, tzinfo=UTC),
        "ended_at": None,
        "status": "active",
        "pause_started_at": None,
        "pause_reason": None,
        "pause_total_seconds": 0,
        "absence_started_at": None,
        "absence_deadline_at": None,
        "close_reason": None,
        "status_message_id": None,
    }
    values.update(overrides)
    return TimeEntry(**values)


def test_worked_seconds_for_active_entry() -> None:
    started_at = datetime(2026, 6, 18, 8, 0, tzinfo=UTC)
    entry = make_entry(started_at=started_at, pause_total_seconds=15 * 60)

    result = worked_seconds(entry, at=started_at + timedelta(hours=2))

    assert result == 105 * 60


def test_worked_seconds_for_paused_entry_includes_current_pause() -> None:
    started_at = datetime(2026, 6, 18, 8, 0, tzinfo=UTC)
    pause_started_at = started_at + timedelta(hours=1)
    entry = make_entry(
        started_at=started_at,
        status="paused",
        pause_started_at=pause_started_at,
        pause_reason="manual",
        pause_total_seconds=10 * 60,
    )

    result = worked_seconds(entry, at=started_at + timedelta(hours=2))

    assert result == 50 * 60


def test_worked_seconds_for_closed_entry_uses_end_time() -> None:
    started_at = datetime(2026, 6, 18, 8, 0, tzinfo=UTC)
    entry = make_entry(
        started_at=started_at,
        ended_at=started_at + timedelta(hours=3),
        status="closed",
        pause_total_seconds=30 * 60,
    )

    result = worked_seconds(entry, at=started_at + timedelta(hours=5))

    assert result == 150 * 60


def test_worked_seconds_never_returns_negative_duration() -> None:
    started_at = datetime(2026, 6, 18, 8, 0, tzinfo=UTC)
    entry = make_entry(started_at=started_at, pause_total_seconds=60)

    result = worked_seconds(entry, at=started_at + timedelta(seconds=30))

    assert result == 0


def test_format_seconds_uses_clock_style_duration() -> None:
    assert format_seconds(0) == "00:00:00"
    assert format_seconds(65) == "00:01:05"
    assert format_seconds(3661) == "01:01:01"
    assert format_seconds(27 * 60 * 60 + 2) == "27:00:02"


def test_format_duration_words_uses_singular_and_plural_units() -> None:
    assert format_duration_words(0) == "0 segundos"
    assert format_duration_words(1) == "1 segundo"
    assert format_duration_words(60) == "1 minuto"
    assert format_duration_words(61) == "1 minuto e 1 segundo"
    assert format_duration_words(125) == "2 minutos e 5 segundos"


def test_format_dt_converts_utc_to_fortaleza_time() -> None:
    value = datetime(2026, 9, 19, 15, 30, tzinfo=UTC)

    assert format_dt(value) == "19 de setembro de 2026 às 12:30"


def test_format_dt_treats_naive_database_values_as_utc() -> None:
    value = datetime(2026, 9, 19, 15, 30)

    assert format_dt(value) == "19 de setembro de 2026 às 12:30"
