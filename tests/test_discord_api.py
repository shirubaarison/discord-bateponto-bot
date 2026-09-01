from __future__ import annotations

from bate_ponto_bot.web.discord_api import resolve_discord_user, resolve_discord_users


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
