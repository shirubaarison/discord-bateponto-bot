from __future__ import annotations

from pydantic import Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    discord_token: str = Field(min_length=1)
    database_url: PostgresDsn
    dev_guild_id: int | None = Field(default=None, gt=0)
