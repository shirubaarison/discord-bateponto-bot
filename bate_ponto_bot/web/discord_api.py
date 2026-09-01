from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any

LOGGER = logging.getLogger(__name__)

# Cache structure: user_id -> (timestamp, data_dict)
_USER_CACHE: dict[int, tuple[float, dict[str, Any]]] = {}
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
