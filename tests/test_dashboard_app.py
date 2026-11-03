from __future__ import annotations

import re
from datetime import UTC, date, datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from flask.testing import FlaskClient
from werkzeug.security import generate_password_hash
from werkzeug.test import TestResponse

from bate_ponto_bot.config import Settings
from bate_ponto_bot.web.app import create_app, parse_date_filters
from bate_ponto_bot.web.queries import finish_active_entry, get_user_weekly_totals

TEST_PASSWORD = "test-password"


def dashboard_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "discord_token": "fake_token",
        "database_url": "postgresql://user:pass@localhost:5432/db",
        "postgres_host": None,
        "dashboard_secret_key": "test-secret-key-that-is-longer-than-32-characters",
        "dashboard_username": "Admin",
        "dashboard_password_hash": generate_password_hash(TEST_PASSWORD),
        "dashboard_secure_cookies": False,
        "dashboard_proxy_fix": False,
    }
    values.update(overrides)
    return Settings(**values)


def login(
    client: FlaskClient, *, password: str = TEST_PASSWORD, next_target: str | None = None
) -> TestResponse:
    login_page = client.get("/login")
    csrf_match = re.search(r'name="csrf_token" value="([^"]+)"', login_page.text)
    assert csrf_match is not None
    data = {
        "csrf_token": csrf_match.group(1),
        "username": "Admin",
        "password": password,
    }
    if next_target is not None:
        data["next"] = next_target
    return client.post("/login", data=data)


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
    settings = dashboard_settings()
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
    mock_active_entries = [
        {
            "id": 10,
            "guild_id": 111,
            "user_id": 222,
            "status": "active",
            "started_at_formatted": "1 de junho de 2026 às 08:00",
            "worked_formatted": "02:00:00",
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
        patch(
            "bate_ponto_bot.web.app.get_guilds",
            return_value=[{"guild_id": 111}, {"guild_id": 333}],
        ),
        patch("bate_ponto_bot.web.app.get_dashboard_stats", return_value=mock_stats),
        patch("bate_ponto_bot.web.app.get_active_entries", return_value=mock_active_entries),
        patch(
            "bate_ponto_bot.web.app.finish_active_entry", return_value=True
        ) as mock_finish_active_entry,
        patch("bate_ponto_bot.web.app.get_users_summary", return_value=mock_users),
        patch("bate_ponto_bot.web.app.get_user_stats", return_value=mock_stats),
        patch("bate_ponto_bot.web.app.get_user_entries", return_value=mock_entries),
        patch(
            "bate_ponto_bot.web.app.get_user_weekly_totals",
            return_value=[
                {
                    "date": "2026-06-01",
                    "date_label": "01/06",
                    "date_full": "01/06/2026",
                    "worked_seconds": 7200,
                    "worked_formatted": "02:00:00",
                    "bar_percent": 100,
                }
            ],
        ),
        patch("bate_ponto_bot.web.app.get_all_entries", return_value=mock_entries),
        patch("bate_ponto_bot.web.app.get_recent_events", return_value=mock_events),
        patch("bate_ponto_bot.web.app.get_entry_events", return_value=mock_events),
        patch(
            "bate_ponto_bot.web.app.resolve_discord_guilds",
            return_value={
                111: {"guild_id": 111, "name": "Meu Servidor"},
                333: {"guild_id": 333, "name": "Outro servidor"},
            },
        ),
        patch(
            "bate_ponto_bot.web.app.resolve_discord_guild",
            return_value={"guild_id": 111, "name": "Meu Servidor"},
        ),
    ):
        mock_conn = MagicMock()
        mock_get_db.return_value = mock_conn

        client = app.test_client()

        # 1. Authentication is required
        res_unauthenticated = client.get("/")
        assert res_unauthenticated.status_code == 302
        assert "/login?next=/" in res_unauthenticated.headers["Location"]
        assert client.get("/export/entries").status_code == 302
        assert client.get("/api/entries/10/events").status_code == 302

        res_login = login(client)
        assert res_login.status_code == 302
        assert res_login.headers["Location"] == "/"

        # 2. Test Index route
        res = client.get("/")
        assert res.status_code == 200
        assert b"Painel de controle de ponto" in res.data
        assert "Horário de Fortaleza" not in res.text
        assert "Todos os Servidores" not in res.text
        assert '<option value="111" selected>' in res.text
        assert "Meu Servidor" in res.text
        assert "brand-mark" in res.text
        assert b"Colaboradores" in res.data
        assert '<div class="stat-value">2h</div>' in res.text
        assert '<div class="stat-title">Tempo em Pausa</div>' not in res.text
        assert '<div class="stat-title">Média por Turno</div>' not in res.text
        assert '<div class="stat-title">Fechamentos por Ausência</div>' not in res.text
        assert "<th>Tempo em Pausa</th>" not in res.text
        assert "data-theme-toggle" in res.text
        assert "Encerrar ponto" in res.text
        assert "Exportar todos os registros" not in res.text
        assert "default-src 'self'" in res.headers["Content-Security-Policy"]
        assert res.headers["X-Frame-Options"] == "DENY"

        csrf_match = re.search(r'name="csrf_token" value="([^"]+)"', res.text)
        assert csrf_match is not None
        finish_response = client.post(
            "/entries/10/finish",
            data={"csrf_token": csrf_match.group(1), "guild_id": "111"},
        )
        assert finish_response.status_code == 302
        mock_finish_active_entry.assert_called_once_with(mock_conn, 10)

        # 3. Test Export Summary CSV
        res_csv = client.get("/export/summary")
        assert res_csv.status_code == 200
        assert res_csv.headers["Content-Type"].startswith("text/csv")

        # 4. Test Export Entries CSV
        res_entries_csv = client.get("/export/entries")
        assert res_entries_csv.status_code == 200
        assert res_entries_csv.headers["Content-Type"].startswith("text/csv")
        assert "ID do Registro" in res_entries_csv.text

        # 5. Test User Detail Page
        res_user = client.get("/users/222")
        assert res_user.status_code == 200
        assert b"Total de Turnos" in res_user.data
        assert "Horas trabalhadas por semana" in res_user.text
        assert "weekly-chart-bar" in res_user.text
        assert '<div class="stat-title">Tempo em Pausa</div>' not in res_user.text
        assert '<div class="stat-title">Fechamentos por Ausência</div>' not in res_user.text
        assert "<th>Pausa</th>" not in res_user.text

        # 6. Test Export Single User CSV
        res_user_csv = client.get("/export/user/222")
        assert res_user_csv.status_code == 200
        assert res_user_csv.headers["Content-Type"].startswith("text/csv")

        # 7. Test Events / Audit Page
        res_events = client.get("/events")
        assert res_events.status_code == 200
        assert b"Registro de eventos" in res_events.data
        assert "Detalhes / Metadados" not in res_events.text

        # 8. Test API Entry Events endpoint
        res_api = client.get("/api/entries/10/events")
        assert res_api.status_code == 200
        assert res_api.json["entry_id"] == 10
        assert len(res_api.json["events"]) == 1

        # 9. Test Health Check endpoint
        res_health = client.get("/healthz")
        assert res_health.status_code == 200
        assert res_health.json["status"] == "ok"

        csrf_match = re.search(r'name="csrf_token" value="([^"]+)"', res.text)
        assert csrf_match is not None
        logout_response = client.post("/logout", data={"csrf_token": csrf_match.group(1)})
        assert logout_response.status_code == 302
        assert client.get("/").status_code == 302


def test_dashboard_login_rejects_invalid_credentials_and_csrf() -> None:
    app = create_app(dashboard_settings())
    app.config["TESTING"] = True
    client = app.test_client()

    invalid_credentials = login(client, password="wrong-password")
    assert invalid_credentials.status_code == 401
    assert "Usuário ou senha inválidos." in invalid_credentials.text

    invalid_csrf = client.post(
        "/login",
        data={
            "csrf_token": "invalid",
            "username": "Admin",
            "password": TEST_PASSWORD,
        },
    )
    assert invalid_csrf.status_code == 400


def test_dashboard_login_rate_limit_and_safe_redirect() -> None:
    app = create_app(dashboard_settings(dashboard_login_max_attempts=2))
    app.config["TESTING"] = True
    client = app.test_client()

    assert login(client, password="wrong-password").status_code == 401
    assert login(client, password="wrong-password").status_code == 401
    assert login(client, password="wrong-password").status_code == 429

    redirect_app = create_app(dashboard_settings())
    redirect_app.config["TESTING"] = True
    fresh_client = redirect_app.test_client()
    authenticated = login(fresh_client, next_target="https://example.com/phishing")
    assert authenticated.status_code == 302
    assert authenticated.headers["Location"] == "/"


def test_dashboard_rejects_insecure_auth_configuration() -> None:
    settings = Settings(
        discord_token="fake_token",
        database_url="postgresql://user:pass@localhost:5432/db",
        postgres_host=None,
    )

    try:
        create_app(settings)
    except ValueError as error:
        assert "DASHBOARD_USERNAME" in str(error)
    else:
        raise AssertionError("Dashboard accepted missing authentication settings")


def test_dashboard_sets_secure_session_cookie() -> None:
    app = create_app(dashboard_settings(dashboard_secure_cookies=True))
    app.config["TESTING"] = True

    response = app.test_client().get("/login")
    cookie = response.headers["Set-Cookie"]

    assert "Secure" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie


def test_finish_active_entry_updates_entry_and_creates_event() -> None:
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = {"id": 10, "guild_id": 111, "user_id": 222}

    result = finish_active_entry(connection, 10)

    assert result is True
    assert cursor.execute.call_count == 2
    update_query = cursor.execute.call_args_list[0].args[0]
    event_query = cursor.execute.call_args_list[1].args[0]
    assert "close_reason = 'admin_dashboard'" in update_query
    assert "INSERT INTO time_events" in event_query


def test_finish_active_entry_does_not_create_duplicate_event() -> None:
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = None

    result = finish_active_entry(connection, 10)

    assert result is False
    assert cursor.execute.call_count == 1


def test_get_user_weekly_totals_formats_chart_data() -> None:
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = [
        {"week_start": date(2026, 9, 14), "worked_seconds": 3600},
        {"week_start": date(2026, 9, 21), "worked_seconds": 7200},
    ]

    result = get_user_weekly_totals(connection, user_id=222, guild_id=111)

    assert result == [
        {
            "date": "2026-09-14",
            "date_label": "14/09",
            "date_full": "14/09/2026 a 20/09/2026",
            "worked_seconds": 3600,
            "worked_formatted": "01:00:00",
            "bar_percent": 50,
        },
        {
            "date": "2026-09-21",
            "date_label": "21/09",
            "date_full": "21/09/2026 a 27/09/2026",
            "worked_seconds": 7200,
            "worked_formatted": "02:00:00",
            "bar_percent": 100,
        },
    ]
