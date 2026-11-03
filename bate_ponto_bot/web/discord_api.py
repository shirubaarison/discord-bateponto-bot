from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

LOGGER = logging.getLogger(__name__)

# Cache structure: user_id -> (timestamp, data_dict)
_USER_CACHE: dict[int, tuple[float, dict[str, Any]]] = {}
_GUILD_CACHE: dict[int, tuple[float, dict[str, Any]]] = {}
CACHE_TTL_SECONDS = 3600  # 1 hour


def _default_user_info(user_id: int) -> dict[str, Any]:
    # Discord default avatar index is (user_id >> 22) % 6
    default_avatar_idx = (user_id >> 22) % 6
    return {
        "user_id": user_id,
        "username": f"User {user_id}",
        "display_name": f"User {user_id}",
        "discriminator": "0",
        "avatar_url": f"https://cdn.discordapp.com/embed/avatars/{default_avatar_idx}.png",
        "is_resolved": False,
    }


def resolve_discord_user(token: str | None, user_id: int) -> dict[str, Any]:
    """Resolve Discord user ID to profile info (username, display name, avatar).

    Uses in-memory cache with fallback on failure or missing token.
    """
    now = time.time()
    if user_id in _USER_CACHE:
        cached_time, cached_data = _USER_CACHE[user_id]
        if now - cached_time < CACHE_TTL_SECONDS:
            return cached_data

    if not token or token.strip() == "" or token == "coloque_o_token_do_bot_aqui":
        default_info = _default_user_info(user_id)
        _USER_CACHE[user_id] = (now, default_info)
        return default_info

    url = f"https://discord.com/api/v10/users/{user_id}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bot {token}",
            "User-Agent": "BatePontoDashboard/1.0",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=3.0) as response:
            if response.status == 200:
                data = json.loads(response.read().decode("utf-8"))
                username = data.get("username", f"User {user_id}")
                global_name = data.get("global_name") or username
                avatar = data.get("avatar")
                if avatar:
                    avatar_url = f"https://cdn.discordapp.com/avatars/{user_id}/{avatar}.png"
                else:
                    default_avatar_idx = (user_id >> 22) % 6
                    avatar_url = (
                        f"https://cdn.discordapp.com/embed/avatars/{default_avatar_idx}.png"
                    )

                user_info = {
                    "user_id": user_id,
                    "username": username,
                    "display_name": global_name,
                    "discriminator": data.get("discriminator", "0"),
                    "avatar_url": avatar_url,
                    "is_resolved": True,
                }
                _USER_CACHE[user_id] = (now, user_info)
                return user_info
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, Exception) as err:
        LOGGER.debug("Could not resolve Discord user %s: %s", user_id, err)

    # Fallback to default user
    fallback = _default_user_info(user_id)
    # Cache the fallback for a shorter time (5 min) to avoid hammer
    _USER_CACHE[user_id] = (now - CACHE_TTL_SECONDS + 300, fallback)
    return fallback


def resolve_discord_users(token: str | None, user_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Resolve a list of Discord user IDs."""
    result: dict[int, dict[str, Any]] = {}
    for uid in user_ids:
        result[uid] = resolve_discord_user(token, uid)
    return result


def _default_guild_info(guild_id: int) -> dict[str, Any]:
    return {
        "guild_id": guild_id,
        "name": f"Servidor {guild_id}",
        "icon_url": None,
        "is_resolved": False,
    }


def resolve_discord_guild(token: str | None, guild_id: int) -> dict[str, Any]:
    """Resolve a Discord guild ID to its name and icon."""
    now = time.time()
    if guild_id in _GUILD_CACHE:
        cached_time, cached_data = _GUILD_CACHE[guild_id]
        if now - cached_time < CACHE_TTL_SECONDS:
            return cached_data

    if not token or token.strip() == "" or token == "coloque_o_token_do_bot_aqui":
        fallback = _default_guild_info(guild_id)
        _GUILD_CACHE[guild_id] = (now, fallback)
        return fallback

    req = urllib.request.Request(
        f"https://discord.com/api/v10/guilds/{guild_id}",
        headers={
            "Authorization": f"Bot {token}",
            "User-Agent": "BatePontoDashboard/1.0",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=3.0) as response:
            if response.status == 200:
                data = json.loads(response.read().decode("utf-8"))
                icon = data.get("icon")
                guild_info = {
                    "guild_id": guild_id,
                    "name": data.get("name") or f"Servidor {guild_id}",
                    "icon_url": (
                        f"https://cdn.discordapp.com/icons/{guild_id}/{icon}.png" if icon else None
                    ),
                    "is_resolved": True,
                }
                _GUILD_CACHE[guild_id] = (now, guild_info)
                return guild_info
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, Exception) as err:
        LOGGER.debug("Could not resolve Discord guild %s: %s", guild_id, err)

    fallback = _default_guild_info(guild_id)
    _GUILD_CACHE[guild_id] = (now - CACHE_TTL_SECONDS + 300, fallback)
    return fallback


def resolve_discord_guilds(
    token: str | None, guild_ids: list[int]
) -> dict[int, dict[str, Any]]:
    """Resolve a list of Discord guild IDs."""
    return {guild_id: resolve_discord_guild(token, guild_id) for guild_id in guild_ids}


# OAuth2 ("Login with Discord")

DISCORD_AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
DISCORD_API_BASE = "https://discord.com/api/v10"
OAUTH_SCOPES = "identify guilds"
ADMINISTRATOR_PERMISSION = 1 << 3
MANAGE_GUILD_PERMISSION = 1 << 5
# View Channel + Send Messages + Manage Messages (see README).
BOT_INVITE_PERMISSIONS = (1 << 10) | (1 << 11) | (1 << 13)


class DiscordOAuthError(Exception):
    """Raised when the Discord OAuth2 flow cannot be completed."""


def build_authorize_url(client_id: int, redirect_uri: str, state: str) -> str:
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "scope": OAUTH_SCOPES,
            "redirect_uri": redirect_uri,
            "state": state,
            "prompt": "none",
        }
    )
    return f"{DISCORD_AUTHORIZE_URL}?{query}"


def build_bot_invite_url(client_id: int) -> str:
    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "scope": "bot applications.commands",
            "permissions": BOT_INVITE_PERMISSIONS,
        }
    )
    return f"{DISCORD_AUTHORIZE_URL}?{query}"


def _oauth_request(request: urllib.request.Request) -> Any:
    request.add_header("User-Agent", "BatePontoDashboard/1.0")
    try:
        with urllib.request.urlopen(request, timeout=5.0) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError) as err:
        raise DiscordOAuthError(str(err)) from err


def exchange_oauth_code(client_id: int, client_secret: str, code: str, redirect_uri: str) -> str:
    """Exchange an authorization code for a user access token."""
    body = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{DISCORD_API_BASE}/oauth2/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    data = _oauth_request(request)
    access_token = data.get("access_token") if isinstance(data, dict) else None
    if not isinstance(access_token, str) or not access_token:
        raise DiscordOAuthError("Discord did not return an access token")
    return access_token


def _get_with_bearer(path: str, access_token: str) -> Any:
    request = urllib.request.Request(
        f"{DISCORD_API_BASE}{path}",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    return _oauth_request(request)


def fetch_oauth_user(access_token: str) -> dict[str, Any]:
    data = _get_with_bearer("/users/@me", access_token)
    if not isinstance(data, dict) or "id" not in data:
        raise DiscordOAuthError("Unexpected /users/@me response")
    return data


def fetch_oauth_guilds(access_token: str) -> list[dict[str, Any]]:
    data = _get_with_bearer("/users/@me/guilds", access_token)
    if not isinstance(data, list):
        raise DiscordOAuthError("Unexpected /users/@me/guilds response")
    return data


def manageable_guild_ids(guilds: list[dict[str, Any]]) -> set[int]:
    """Guild IDs where the user is owner, administrator or has Manage Server."""
    result: set[int] = set()
    for guild in guilds:
        try:
            permissions = int(guild.get("permissions") or 0)
            guild_id = int(guild["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if guild.get("owner") or permissions & (ADMINISTRATOR_PERMISSION | MANAGE_GUILD_PERMISSION):
            result.add(guild_id)
    return result
