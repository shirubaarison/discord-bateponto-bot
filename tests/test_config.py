from __future__ import annotations

from bate_ponto_bot.config import Settings


def test_resolved_database_url_encodes_structured_postgres_settings() -> None:
    settings = Settings(
        discord_token="token",
        database_url="postgresql://bad:url@localhost:5432/bad",
        postgres_db="bate_ponto",
        postgres_user="bate:ponto",
        postgres_password="p@ss/word?#",
        postgres_host="postgres",
        postgres_port=5432,
    )

    assert (
        settings.resolved_database_url
        == "postgresql://bate%3Aponto:p%40ss%2Fword%3F%23@postgres:5432/bate_ponto"
    )


def test_resolved_database_url_uses_database_url_without_postgres_host() -> None:
    settings = Settings(
        discord_token="token",
        database_url="postgresql://user:password@example.com:5432/db",
        postgres_host=None,
    )

    assert settings.resolved_database_url == "postgresql://user:password@example.com:5432/db"
