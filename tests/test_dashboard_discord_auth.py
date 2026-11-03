from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

from flask import Flask
from flask.testing import FlaskClient

from bate_ponto_bot.config import Settings
from bate_ponto_bot.web.app import create_app

OWN_GUILD = 111
OTHER_GUILD = 333
DISCORD_USER_ID = 999


def discord_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "discord_token": "fake_token",
        "database_url": "postgresql://user:pass@localhost:5432/db",
        "postgres_host": None,
        "dashboard_secret_key": "test-secret-key-that-is-longer-than-32-characters",
        "dashboard_secure_cookies": False,
        "dashboard_proxy_fix": False,
        "discord_client_id": 123,
        "discord_client_secret": "client-secret",
        "discord_redirect_uri": "http://localhost/auth/discord/callback",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def make_app(**overrides: object) -> Flask:
    app = create_app(discord_settings(**overrides))
    app.config["TESTING"] = True
    return app


@contextmanager
def dashboard_mocks(linked_guilds: set[int]) -> Iterator[dict[str, MagicMock]]:
    """Patch the database layer so only the auth/scoping logic runs for real."""
    stats = {
        "total_users": 0,
        "active_now": 0,
        "paused_now": 0,
        "working_now": 0,
        "total_entries": 0,
        "total_worked_seconds": 0,
        "total_worked_formatted": "00:00:00",
        "total_worked_hours": 0.0,
        "total_pause_seconds": 0,
        "total_pause_formatted": "00:00:00",
        "avg_shift_formatted": "00:00:00",
        "total_auto_absence": 0,
    }
    targets = {
        "get_db": MagicMock(),
        "get_guilds": [{"guild_id": OWN_GUILD}, {"guild_id": OTHER_GUILD}],
        "get_dashboard_user_guild_ids": linked_guilds,
        "get_dashboard_stats": stats,
        "get_active_entries": [],
        "get_users_summary": [],
        "get_all_entries": [],
        "get_user_entries": [],
        "get_user_stats": stats,
        "get_user_weekly_totals": [],
        "get_recent_events": [],
        "get_entry_events": [],
        "get_entry_guild_id": OTHER_GUILD,
        "finish_active_entry": True,
        "save_dashboard_user_login": None,
        "resolve_discord_guilds": {},
        "resolve_discord_guild": {"guild_id": OWN_GUILD, "name": "Meu Servidor"},
        "exchange_oauth_code": "access-token",
        "fetch_oauth_user": {"id": str(DISCORD_USER_ID), "username": "ana", "global_name": "Ana"},
        "fetch_oauth_guilds": [
            {"id": str(OWN_GUILD), "owner": False, "permissions": str(1 << 5)},
            {"id": str(OTHER_GUILD), "owner": False, "permissions": "0"},
        ],
    }
    with ExitStack() as stack:
        mocks = {
            name: stack.enter_context(patch(f"bate_ponto_bot.web.app.{name}", return_value=value))
            for name, value in targets.items()
        }
        yield mocks


def discord_login(client: FlaskClient) -> None:
    start = client.get("/auth/discord?next=/events")
    assert start.status_code == 302
    location = start.headers["Location"]
    assert location.startswith("https://discord.com/oauth2/authorize?")
    state = parse_qs(urlsplit(location).query)["state"][0]

    callback = client.get(f"/auth/discord/callback?code=abc&state={state}")
    assert callback.status_code == 302
    assert callback.headers["Location"] == "/events"


def test_discord_login_creates_account_with_manageable_guilds() -> None:
    client = make_app().test_client()
    with dashboard_mocks({OWN_GUILD}) as mocks:
        discord_login(client)

        mocks["exchange_oauth_code"].assert_called_once_with(
            123, "client-secret", "abc", "http://localhost/auth/discord/callback"
        )
        save_kwargs = mocks["save_dashboard_user_login"].call_args.kwargs
        assert save_kwargs["discord_user_id"] == DISCORD_USER_ID
        assert save_kwargs["username"] == "ana"
        assert save_kwargs["guild_ids"] == {OWN_GUILD}


def test_discord_user_only_sees_linked_guilds() -> None:
    client = make_app().test_client()
    with dashboard_mocks({OWN_GUILD}) as mocks:
        discord_login(client)

        index = client.get("/")
        assert index.status_code == 200
        mocks["get_dashboard_stats"].assert_called_with(
            mocks["get_db"].return_value, guild_id=OWN_GUILD, start_date=None, end_date=None
        )

        assert client.get(f"/?guild_id={OTHER_GUILD}").status_code == 404
        assert client.get(f"/events?guild_id={OTHER_GUILD}").status_code == 404
        assert client.get(f"/users/{OTHER_GUILD}/222").status_code == 404
        assert client.get(f"/export/summary?guild_id={OTHER_GUILD}").status_code == 404
        assert client.get(f"/export/user/{OTHER_GUILD}/222").status_code == 404
        assert client.get(f"/export/entries?guild_id={OTHER_GUILD}").status_code == 404

        # Without an explicit guild, exports fall back to a linked guild, never "all guilds".
        assert client.get("/export/entries").status_code == 200
        assert mocks["get_all_entries"].call_args.kwargs["guild_id"] == OWN_GUILD
        assert client.get("/users/222").status_code == 200
        assert mocks["get_user_entries"].call_args.kwargs["guild_id"] == OWN_GUILD

        # Entries belonging to another guild are hidden.
        assert client.get("/api/entries/10/events").status_code == 404
        mocks["get_entry_events"].assert_not_called()


def test_discord_user_cannot_modify_entries_of_other_guilds() -> None:
    client = make_app().test_client()
    with dashboard_mocks({OWN_GUILD}) as mocks:
        discord_login(client)
        page = client.get("/")
        csrf = page.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

        finish = client.post("/entries/10/finish", data={"csrf_token": csrf})
        assert finish.status_code == 404
        mocks["finish_active_entry"].assert_not_called()

        mocks["get_entry_guild_id"].return_value = OWN_GUILD
        finish = client.post("/entries/10/finish", data={"csrf_token": csrf})
        assert finish.status_code == 302
        mocks["finish_active_entry"].assert_called_once()


def test_discord_user_without_guilds_sees_invite_page() -> None:
    client = make_app().test_client()
    with dashboard_mocks(set()):
        discord_login(client)

        page = client.get("/")
        assert page.status_code == 200
        assert "Nenhum servidor disponível" in page.text
        assert "scope=bot+applications.commands" in page.text
        assert client.get("/export/entries").status_code == 404


def test_deleted_account_is_logged_out() -> None:
    client = make_app().test_client()
    with dashboard_mocks({OWN_GUILD}) as mocks:
        discord_login(client)
        mocks["get_dashboard_user_guild_ids"].return_value = None

        response = client.get("/")
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]


def test_discord_callback_rejects_invalid_state() -> None:
    client = make_app().test_client()
    with dashboard_mocks({OWN_GUILD}) as mocks:
        client.get("/auth/discord")
        response = client.get("/auth/discord/callback?code=abc&state=forged")

        assert response.status_code == 400
        mocks["exchange_oauth_code"].assert_not_called()
        assert client.get("/").status_code == 302


def test_discord_only_configuration_disables_password_login() -> None:
    client = make_app().test_client()

    page = client.get("/login")
    assert page.status_code == 200
    assert "Entrar com Discord" in page.text
    assert 'name="password"' not in page.text
    assert client.post("/login", data={"username": "x", "password": "y"}).status_code == 404
