from __future__ import annotations

from urllib.parse import quote

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    discord_token: str = Field(min_length=1)
    database_url: str | None = None
    postgres_db: str = "bate_ponto"
    postgres_user: str = "postgres"
    postgres_password: str = "postgres"
    postgres_host: str | None = None
    postgres_port: int = Field(default=5432, gt=0, le=65535)
    dev_guild_id: int | None = Field(default=None, gt=0)
    log_level: str = "INFO"
    dashboard_host: str = "0.0.0.0"
    dashboard_port: int = Field(default=5000, gt=0, le=65535)
    dashboard_debug: bool = False
    dashboard_secret_key: str = "bate-ponto-secret-key-change-in-prod"

    @property
    def resolved_database_url(self) -> str:
        if self.postgres_host:
            user = quote(self.postgres_user, safe="")
            password = quote(self.postgres_password, safe="")
            database = quote(self.postgres_db, safe="")
            return f"postgresql://{user}:{password}@{self.postgres_host}:{self.postgres_port}/{database}"

        if self.database_url:
            return self.database_url

        raise ValueError("Configure DATABASE_URL or POSTGRES_HOST.")
