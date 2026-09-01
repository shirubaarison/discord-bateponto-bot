from __future__ import annotations

from datetime import UTC, datetime

from bate_ponto_bot.web.csv_export import generate_entries_csv, generate_users_summary_csv


def test_generate_users_summary_csv() -> None:
    users = [
        {
            "user_id": 12345,
            "guild_id": 999,
            "current_status": "active",
            "shifts_count": 5,
            "completed_shifts": 4,
            "worked_formatted": "10:30:00",
            "worked_hours": 10.5,
            "worked_seconds": 37800,
            "pause_formatted": "00:45:00",
            "avg_shift_formatted": "02:06:00",
            "auto_absence_count": 1,
            "first_started_at_formatted": "1 de janeiro de 2026 às 08:00",
            "last_activity_at_formatted": "5 de janeiro de 2026 às 18:30",
        }
    ]
    resolved = {
        12345: {
            "user_id": 12345,
            "username": "alice",
            "display_name": "Alice Silva",
        }
    }

    csv_out = generate_users_summary_csv(users, resolved)

    # Check UTF-8 BOM
    assert csv_out.startswith("\ufeff")
    assert "ID do Usuário;Nome / Tag Discord;ID do Servidor" in csv_out
    assert "12345;Alice Silva;999;Ativo;5;4;10:30:00;10,50;37800;00:45:00;02:06:00;1" in csv_out


def test_generate_entries_csv() -> None:
    entries = [
        {
            "id": 1,
            "guild_id": 999,
            "user_id": 12345,
            "status": "closed",
            "started_at": datetime(2026, 1, 1, 8, 0, tzinfo=UTC),
            "started_at_formatted": "1 de janeiro de 2026 às 08:00",
            "ended_at": datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
            "ended_at_formatted": "1 de janeiro de 2026 às 12:00",
            "worked_seconds": 14400,
            "worked_formatted": "04:00:00",
            "pause_formatted": "00:00:00",
            "pause_total_seconds": 0,
            "close_reason": "auto_absence",
        }
    ]
    resolved = {
        12345: {
            "user_id": 12345,
            "username": "alice",
            "display_name": "Alice Silva",
        }
    }

    csv_out = generate_entries_csv(entries, resolved)

    assert csv_out.startswith("\ufeff")
    assert "ID do Registro;ID do Servidor;ID do Usuário" in csv_out
    assert "1;999;12345;Alice Silva;Finalizado" in csv_out
    assert "04:00:00;4,00;14400;00:00:00;0;Ausência automática" in csv_out
