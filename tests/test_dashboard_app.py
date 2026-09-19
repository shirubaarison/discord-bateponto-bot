from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from bate_ponto_bot.config import Settings
from bate_ponto_bot.web.app import create_app, parse_date_filters


def test_parse_date_filters_presets() -> None:
    start_dt, end_dt, preset = parse_date_filters("today", None, None)
    assert preset == "today"
    assert start_dt is not None
    assert end_dt is not None
    assert start_dt <= end_dt

    start_dt, end_dt, preset = parse_date_filters("week", None, None)
    assert preset == "week"
    assert start_dt is not None

    start_dt, end_dt, preset = parse_date_filters("month", None, None)
    assert preset == "month"
    assert start_dt is not None

    start_dt, end_dt, preset = parse_date_filters("last7", None, None)
    assert preset == "last7"
    assert start_dt is not None

    start_dt, end_dt, preset = parse_date_filters("last30", None, None)
    assert preset == "last30"
    assert start_dt is not None


def test_parse_date_filters_custom_dates() -> None:
    start_dt, end_dt, preset = parse_date_filters(None, "2026-06-01", "2026-06-30")
    assert preset == "custom"
    assert start_dt is not None
    assert end_dt is not None
    local_timezone = ZoneInfo("America/Fortaleza")
    start_local = start_dt.astimezone(local_timezone)
    end_local = end_dt.astimezone(local_timezone)
    assert start_local == datetime(2026, 6, 1, 0, 0, tzinfo=local_timezone)
    assert end_local == datetime(2026, 6, 30, 23, 59, 59, 999999, tzinfo=local_timezone)


def test_parse_date_filters_empty() -> None:
    start_dt, end_dt, preset = parse_date_filters(None, None, None)
    assert preset == "all"
    assert start_dt is None
    assert end_dt is None


def test_flask_app_routes() -> None:
    settings = Settings(
        discord_token="fake_token",
        database_url="postgresql://user:pass@localhost:5432/db",
        postgres_host=None,
    )
    app = create_app(settings)
    app.config["TESTING"] = True

    mock_stats = {
        "total_users": 2,
        "active_now": 1,
        "paused_now": 0,
        "working_now": 1,
        "total_entries": 3,
        "total_worked_seconds": 7200,
        "total_worked_formatted": "02:00:00",
        "total_worked_hours": 2.0,
        "total_pause_seconds": 0,
        "total_pause_formatted": "00:00:00",
        "avg_shift_formatted": "00:40:00",
        "total_auto_absence": 0,
    }
    mock_users = [
        {
            "guild_id": 111,
            "user_id": 222,
            "shifts_count": 3,
            "completed_shifts": 2,
            "worked_seconds": 7200,
            "worked_formatted": "02:00:00",
            "worked_hours": 2.0,
            "pause_seconds": 0,
            "pause_formatted": "00:00:00",
            "avg_shift_formatted": "00:40:00",
            "auto_absence_count": 0,
            "first_started_at_formatted": "1 de junho de 2026 às 08:00",
            "last_activity_at_formatted": "1 de junho de 2026 às 10:00",
            "current_status": "active",
        }
    ]
    mock_entries = [
        {
            "id": 10,
            "guild_id": 111,
            "user_id": 222,
            "status": "closed",
            "started_at": datetime(2026, 6, 1, 8, 0, tzinfo=UTC),
            "started_at_formatted": "1 de junho de 2026 às 08:00",
            "ended_at": datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
            "ended_at_formatted": "1 de junho de 2026 às 10:00",
            "worked_seconds": 7200,
            "worked_formatted": "02:00:00",
            "pause_formatted": "00:00:00",
            "pause_total_seconds": 0,
            "close_reason": None,
        }
    ]
    mock_events = [
        {
            "id": 1,
            "entry_id": 10,
            "guild_id": 111,
            "user_id": 222,
            "event_type": "started",
            "occurred_at": datetime(2026, 6, 1, 8, 0, tzinfo=UTC),
            "occurred_at_formatted": "1 de junho de 2026 às 08:00",
            "metadata": {},
        }
    ]

    with (
        patch("bate_ponto_bot.web.app.get_db") as mock_get_db,
        patch("bate_ponto_bot.web.app.get_guilds", return_value=[{"guild_id": 111}]),
        patch("bate_ponto_bot.web.app.get_dashboard_stats", return_value=mock_stats),
        patch("bate_ponto_bot.web.app.get_active_entries", return_value=[]),
        patch("bate_ponto_bot.web.app.get_users_summary", return_value=mock_users),
        patch("bate_ponto_bot.web.app.get_user_stats", return_value=mock_stats),
        patch("bate_ponto_bot.web.app.get_user_entries", return_value=mock_entries),
        patch("bate_ponto_bot.web.app.get_all_entries", return_value=mock_entries),
        patch("bate_ponto_bot.web.app.get_recent_events", return_value=mock_events),
        patch("bate_ponto_bot.web.app.get_entry_events", return_value=mock_events),
    ):
        mock_conn = MagicMock()
        mock_get_db.return_value = mock_conn

        client = app.test_client()

        # 1. Test Index route
        res = client.get("/")
        assert res.status_code == 200
        assert b"Painel de controle de ponto" in res.data
        assert "Horário de Fortaleza" in res.text
        assert b"Colaboradores" in res.data

        # 2. Test Export Summary CSV
        res_csv = client.get("/export/summary")
        assert res_csv.status_code == 200
        assert res_csv.headers["Content-Type"].startswith("text/csv")

        # 3. Test Export Entries CSV
        res_entries_csv = client.get("/export/entries")
        assert res_entries_csv.status_code == 200
        assert res_entries_csv.headers["Content-Type"].startswith("text/csv")
        assert "ID do Registro" in res_entries_csv.text

        # 4. Test User Detail Page
        res_user = client.get("/users/222")
        assert res_user.status_code == 200
        assert b"Total de Turnos" in res_user.data

        # 5. Test Export Single User CSV
        res_user_csv = client.get("/export/user/222")
        assert res_user_csv.status_code == 200
        assert res_user_csv.headers["Content-Type"].startswith("text/csv")

        # 6. Test Events / Audit Page
        res_events = client.get("/events")
        assert res_events.status_code == 200
        assert b"Registro de eventos" in res_events.data

        # 7. Test API Entry Events endpoint
        res_api = client.get("/api/entries/10/events")
        assert res_api.status_code == 200
        assert res_api.json["entry_id"] == 10
        assert len(res_api.json["events"]) == 1

        # 8. Test Health Check endpoint
        res_health = client.get("/healthz")
        assert res_health.status_code == 200
        assert res_health.json["status"] == "ok"
