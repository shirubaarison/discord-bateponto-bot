"""Popula o banco com pontos de exemplo para testar o dashboard sem usar o bot.

Uso:
    uv run python scripts/seed_demo.py --guild-id 123456789012345678 --fuso Europe/Lisbon

Os registros de demonstração usam IDs de usuário fictícios (900000000000000001...) e podem
ser apagados com --limpar.
"""

from __future__ import annotations

import argparse
import random
from datetime import UTC, datetime, time, timedelta

import psycopg
from psycopg.types.json import Jsonb

from bate_ponto_bot.config import Settings
from bate_ponto_bot.utils import canonical_timezone_name, load_timezone

DEMO_USER_IDS = [900_000_000_000_000_001 + offset for offset in range(4)]


def clear_demo_data(conn: psycopg.Connection, guild_id: int) -> None:
    conn.execute(
        "DELETE FROM time_entries WHERE guild_id = %s AND user_id = ANY(%s)",
        (guild_id, DEMO_USER_IDS),
    )


def seed(conn: psycopg.Connection, guild_id: int, timezone_name: str, days: int) -> int:
    tz = load_timezone(timezone_name)
    rng = random.Random(guild_id)
    today = datetime.now(tz).date()
    created = 0

    conn.execute(
        """
        INSERT INTO guild_settings (guild_id, timezone)
        VALUES (%s, %s)
        ON CONFLICT (guild_id) DO UPDATE SET timezone = EXCLUDED.timezone, updated_at = now()
        """,
        (guild_id, timezone_name),
    )
    clear_demo_data(conn, guild_id)

    for user_id in DEMO_USER_IDS:
        for day_offset in range(days, 0, -1):
            day = today - timedelta(days=day_offset)
            if day.weekday() >= 5 or rng.random() < 0.15:
                continue
            # Shifts start between 08:00 and 10:00 local time, so day boundaries matter.
            start_local = datetime.combine(day, time(8), tzinfo=tz) + timedelta(
                minutes=rng.randint(0, 120)
            )
            started_at = start_local.astimezone(UTC)
            ended_at = started_at + timedelta(hours=rng.uniform(4, 9))
            pause_seconds = rng.choice([0, 900, 1800, 3600])
            entry_id = insert_entry(
                conn, guild_id, user_id, started_at, ended_at, "closed", pause_seconds
            )
            insert_event(conn, entry_id, guild_id, user_id, "started", started_at)
            insert_event(conn, entry_id, guild_id, user_id, "finished", ended_at)
            created += 1

    # One person currently working, to fill "Trabalhando agora".
    started_at = datetime.now(UTC) - timedelta(hours=2, minutes=15)
    entry_id = insert_entry(conn, guild_id, DEMO_USER_IDS[0], started_at, None, "active", 0)
    insert_event(conn, entry_id, guild_id, DEMO_USER_IDS[0], "started", started_at)
    return created + 1


def insert_entry(
    conn: psycopg.Connection,
    guild_id: int,
    user_id: int,
    started_at: datetime,
    ended_at: datetime | None,
    status: str,
    pause_seconds: int,
) -> int:
    row = conn.execute(
        """
        INSERT INTO time_entries (
            guild_id, user_id, started_at, ended_at, status, pause_total_seconds, close_reason
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            guild_id,
            user_id,
            started_at,
            ended_at,
            status,
            pause_seconds,
            "manual" if ended_at else None,
        ),
    ).fetchone()
    assert row is not None
    return int(row[0])


def insert_event(
    conn: psycopg.Connection,
    entry_id: int,
    guild_id: int,
    user_id: int,
    event_type: str,
    occurred_at: datetime,
) -> None:
    conn.execute(
        """
        INSERT INTO time_events (entry_id, guild_id, user_id, event_type, occurred_at, metadata)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (entry_id, guild_id, user_id, event_type, occurred_at, Jsonb({"demo": True})),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--guild-id", type=int, required=True, help="ID do servidor do Discord")
    parser.add_argument("--fuso", default="America/Sao_Paulo", help="Fuso horário do servidor")
    parser.add_argument("--dias", type=int, default=21, help="Quantos dias de histórico gerar")
    parser.add_argument("--limpar", action="store_true", help="Apaga só os dados de demonstração")
    args = parser.parse_args()

    timezone_name = canonical_timezone_name(args.fuso)
    if timezone_name is None:
        raise SystemExit(f"Fuso horário desconhecido: {args.fuso}")

    with psycopg.connect(Settings().resolved_database_url) as conn:
        if args.limpar:
            clear_demo_data(conn, args.guild_id)
            print(f"Dados de demonstração removidos do servidor {args.guild_id}.")
            return
        created = seed(conn, args.guild_id, timezone_name, args.dias)
    print(f"{created} pontos criados no servidor {args.guild_id} (fuso {timezone_name}).")


if __name__ == "__main__":
    main()
