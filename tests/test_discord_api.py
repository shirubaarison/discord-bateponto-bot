from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from bate_ponto_bot.web.discord_api import (
    build_authorize_url,
    build_bot_invite_url,
    manageable_guild_ids,
    resolve_discord_guild,
    resolve_discord_guilds,
    resolve_discord_user,
    resolve_discord_users,
)


def test_resolve_discord_user_fallback_without_token() -> None:
    user = resolve_discord_user(None, 123456789)

    assert user["user_id"] == 123456789
    assert user["username"] == "User 123456789"
    assert user["display_name"] == "User 123456789"
    assert user["is_resolved"] is False
    assert "cdn.discordapp.com/embed/avatars" in user["avatar_url"]


def test_resolve_discord_users_batch() -> None:
    results = resolve_discord_users(None, [111, 222])

    assert 111 in results
    assert 222 in results
    assert results[111]["user_id"] == 111
    assert results[222]["user_id"] == 222


def test_resolve_discord_guild_fallback_without_token() -> None:
    guild = resolve_discord_guild(None, 333)

    assert guild["guild_id"] == 333
    assert guild["name"] == "Servidor 333"
    assert guild["is_resolved"] is False


def test_resolve_discord_guilds_batch() -> None:
    results = resolve_discord_guilds(None, [333, 444])

    assert results[333]["name"] == "Servidor 333"
    assert results[444]["name"] == "Servidor 444"


def test_manageable_guild_ids_requires_owner_admin_or_manage_server() -> None:
    guilds = [
        {"id": "1", "owner": True, "permissions": "0"},
        {"id": "2", "owner": False, "permissions": str(1 << 3)},
        {"id": "3", "owner": False, "permissions": str(1 << 5)},
        {"id": "4", "owner": False, "permissions": str(1 << 10)},
        {"id": "not-a-number", "permissions": str(1 << 5)},
    ]

    assert manageable_guild_ids(guilds) == {1, 2, 3}


def test_build_authorize_url_requests_identify_and_guilds() -> None:
    url = build_authorize_url(123, "https://ponto.example.com/auth/discord/callback", "abc")
    query = parse_qs(urlsplit(url).query)

    assert url.startswith("https://discord.com/oauth2/authorize?")
    assert query["client_id"] == ["123"]
    assert query["scope"] == ["identify guilds"]
    assert query["state"] == ["abc"]
    assert query["redirect_uri"] == ["https://ponto.example.com/auth/discord/callback"]


def test_build_bot_invite_url_includes_bot_scopes() -> None:
    query = parse_qs(urlsplit(build_bot_invite_url(123)).query)

    assert query["scope"] == ["bot applications.commands"]
    assert query["permissions"] == [str((1 << 10) | (1 << 11) | (1 << 13))]
