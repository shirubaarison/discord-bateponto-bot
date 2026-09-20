from __future__ import annotations

import csv
import io
from typing import Any


def generate_users_summary_csv(
    users: list[dict[str, Any]],
    resolved_users: dict[int, dict[str, Any]] | None = None,
) -> str:
    """Generate CSV string with UTF-8 BOM for collaborator summary stats."""
    resolved_users = resolved_users or {}
    output = io.StringIO()
    # Write UTF-8 BOM for Excel compatibility
    output.write("\ufeff")

    writer = csv.writer(output, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(
        [
            "ID do Usuário",
            "Nome / Tag Discord",
            "ID do Servidor",
            "Status Atual",
            "Total de Turnos",
            "Turnos Concluídos",
            "Tempo Trabalhado (HH:MM:SS)",
            "Horas Trabalhadas (Decimal)",
            "Segundos Trabalhados",
            "Tempo Pausado (HH:MM:SS)",
            "Média por Turno (HH:MM:SS)",
            "Encerramentos por Ausência",
            "Primeiro Registro",
            "Última Atividade",
        ]
    )

    for u in users:
        uid = int(u["user_id"])
        user_info = resolved_users.get(uid, {})
        display_name = user_info.get("display_name") or user_info.get("username") or f"User {uid}"
        status_map = {
            "active": "Ativo",
            "paused": "Pausado",
            "offline": "Offline / Inativo",
        }
        status_str = status_map.get(u.get("current_status", "offline"), u.get("current_status", ""))

        writer.writerow(
            [
                str(uid),
                display_name,
                str(u["guild_id"]),
                status_str,
                u["shifts_count"],
                u.get("completed_shifts", u["shifts_count"]),
                u["worked_formatted"],
                f"{u['worked_hours']:.2f}".replace(".", ","),
                u["worked_seconds"],
                u["pause_formatted"],
                u["avg_shift_formatted"],
                u["auto_absence_count"],
                u["first_started_at_formatted"],
                u["last_activity_at_formatted"],
            ]
        )

    return output.getvalue()


def generate_entries_csv(
    entries: list[dict[str, Any]],
    resolved_users: dict[int, dict[str, Any]] | None = None,
) -> str:
    """Generate CSV string with UTF-8 BOM for detailed time entries."""
    resolved_users = resolved_users or {}
    output = io.StringIO()
    output.write("\ufeff")

    writer = csv.writer(output, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(
        [
            "ID do Registro",
            "ID do Servidor",
            "ID do Usuário",
            "Nome / Tag Discord",
            "Status",
            "Início (Formatado)",
            "Início (ISO)",
            "Fim (Formatado)",
            "Fim (ISO)",
            "Tempo Trabalhado (HH:MM:SS)",
            "Horas Trabalhadas (Decimal)",
            "Segundos Trabalhados",
            "Tempo Pausado (HH:MM:SS)",
            "Segundos Pausados",
            "Motivo de Encerramento",
        ]
    )

    for e in entries:
        uid = int(e["user_id"])
        user_info = resolved_users.get(uid, {})
        display_name = user_info.get("display_name") or user_info.get("username") or f"User {uid}"
        worked_hours = round(e["worked_seconds"] / 3600, 2)

        status_map = {
            "active": "Ativo",
            "paused": "Pausado",
            "closed": "Finalizado",
        }
        status_str = status_map.get(e.get("status", ""), e.get("status", ""))

        close_reason_map = {
            "auto_absence": "Ausência automática",
            "pause_timeout": "Pausa excedeu 24 horas",
            "active_timeout": "Ponto excedeu 24 horas",
            "manual": "Manual",
            None: "-",
        }
        close_reason_str = close_reason_map.get(e.get("close_reason"), e.get("close_reason") or "-")

        writer.writerow(
            [
                e["id"],
                e["guild_id"],
                str(uid),
                display_name,
                status_str,
                e["started_at_formatted"],
                e["started_at"].isoformat() if e.get("started_at") else "",
                e["ended_at_formatted"],
                e["ended_at"].isoformat() if e.get("ended_at") else "",
                e["worked_formatted"],
                f"{worked_hours:.2f}".replace(".", ","),
                e["worked_seconds"],
                e["pause_formatted"],
                e.get("pause_total_seconds", 0),
                close_reason_str,
            ]
        )

    return output.getvalue()
