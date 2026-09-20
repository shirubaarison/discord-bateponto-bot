from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from bate_ponto_bot.utils import format_dt, format_seconds, utcnow


def _calc_entry_worked_seconds(entry: dict[str, Any], at: datetime | None = None) -> int:
    """Compute worked seconds for a dict row of time_entries."""
    now = at or utcnow()
    started_at = entry["started_at"]
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)

    ended_at = entry.get("ended_at")
    if ended_at and ended_at.tzinfo is None:
        ended_at = ended_at.replace(tzinfo=UTC)

    end = ended_at or now
    pause_total = int(entry.get("pause_total_seconds") or 0)

    if entry.get("status") == "paused" and entry.get("pause_started_at"):
        pause_started_at = entry["pause_started_at"]
        if pause_started_at.tzinfo is None:
            pause_started_at = pause_started_at.replace(tzinfo=UTC)
        pause_total += max(0, int((now - pause_started_at).total_seconds()))

    total_diff = max(0, int((end - started_at).total_seconds()))
    return max(0, total_diff - pause_total)


def get_guilds(conn: Connection) -> list[dict[str, Any]]:
    """Fetch distinct guilds configured or with entries."""
    query = """
        SELECT DISTINCT COALESCE(gs.guild_id, te.guild_id) AS guild_id,
               gs.absence_tolerance_seconds,
               gs.control_channel_id,
               gs.audit_channel_id
        FROM guild_settings gs
        FULL OUTER JOIN (SELECT DISTINCT guild_id FROM time_entries) te
          ON gs.guild_id = te.guild_id
        ORDER BY guild_id
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query)
        return list(cur.fetchall())


def get_dashboard_stats(
    conn: Connection,
    guild_id: int | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> dict[str, Any]:
    """Calculate overall KPI metrics for the dashboard within the date filter."""
    where_clauses = ["1=1"]
    params: list[Any] = []

    if guild_id:
        params.append(guild_id)
        where_clauses.append("guild_id = %s")

    if start_date:
        params.append(start_date)
        where_clauses.append("started_at >= %s")

    if end_date:
        params.append(end_date)
        where_clauses.append("started_at <= %s")

    where_sql = " AND ".join(where_clauses)

    query = f"""
        SELECT
            id,
            guild_id,
            user_id,
            started_at,
            ended_at,
            status,
            pause_started_at,
            pause_reason,
            pause_total_seconds,
            close_reason
        FROM time_entries
        WHERE {where_sql}
        ORDER BY started_at DESC
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
        entries = list(cur.fetchall())

    now = utcnow()
    total_users_set: set[int] = set()
    active_users_now = 0
    paused_users_now = 0
    total_worked_seconds = 0
    total_pause_seconds = 0
    total_auto_absence = 0

    for entry in entries:
        total_users_set.add(int(entry["user_id"]))
        w_sec = _calc_entry_worked_seconds(entry, at=now)
        total_worked_seconds += w_sec
        total_pause_seconds += int(entry["pause_total_seconds"] or 0)
        if entry.get("status") == "paused" and entry.get("pause_started_at"):
            p_start = entry["pause_started_at"]
            if p_start.tzinfo is None:
                p_start = p_start.replace(tzinfo=UTC)
            total_pause_seconds += max(0, int((now - p_start).total_seconds()))

        if entry["ended_at"] is None:
            if entry["status"] == "active":
                active_users_now += 1
            elif entry["status"] == "paused":
                paused_users_now += 1

        if entry.get("close_reason") == "auto_absence":
            total_auto_absence += 1

    total_entries = len(entries)
    avg_shift_seconds = int(total_worked_seconds / total_entries) if total_entries > 0 else 0

    return {
        "total_users": len(total_users_set),
        "active_now": active_users_now,
        "paused_now": paused_users_now,
        "working_now": active_users_now + paused_users_now,
        "total_entries": total_entries,
        "total_worked_seconds": total_worked_seconds,
        "total_worked_formatted": format_seconds(total_worked_seconds),
        "total_worked_hours": round(total_worked_seconds / 3600, 2),
        "total_pause_seconds": total_pause_seconds,
        "total_pause_formatted": format_seconds(total_pause_seconds),
        "avg_shift_formatted": format_seconds(avg_shift_seconds),
        "total_auto_absence": total_auto_absence,
    }


def get_active_entries(conn: Connection, guild_id: int | None = None) -> list[dict[str, Any]]:
    """Get all currently active or paused time entries."""
    where_clauses = ["ended_at IS NULL"]
    params: list[Any] = []

    if guild_id:
        params.append(guild_id)
        where_clauses.append("guild_id = %s")

    where_sql = " AND ".join(where_clauses)
    query = f"""
        SELECT *
        FROM time_entries
        WHERE {where_sql}
        ORDER BY started_at ASC
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
        rows = list(cur.fetchall())

    now = utcnow()
    results: list[dict[str, Any]] = []
    for row in rows:
        worked_sec = _calc_entry_worked_seconds(row, at=now)
        results.append(
            {
                **row,
                "worked_seconds": worked_sec,
                "worked_formatted": format_seconds(worked_sec),
                "started_at_formatted": format_dt(row["started_at"]),
                "pause_started_at_formatted": (
                    format_dt(row["pause_started_at"]) if row.get("pause_started_at") else None
                ),
            }
        )
    return results


def get_users_summary(
    conn: Connection,
    guild_id: int | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> list[dict[str, Any]]:
    """Get aggregated statistics per user for the overview table."""
    where_clauses = ["1=1"]
    params: list[Any] = []

    if guild_id:
        params.append(guild_id)
        where_clauses.append("guild_id = %s")

    if start_date:
        params.append(start_date)
        where_clauses.append("started_at >= %s")

    if end_date:
        params.append(end_date)
        where_clauses.append("started_at <= %s")

    where_sql = " AND ".join(where_clauses)

    query = f"""
        SELECT *
        FROM time_entries
        WHERE {where_sql}
        ORDER BY started_at DESC
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
        entries = list(cur.fetchall())

    now = utcnow()
    users_map: dict[tuple[int, int], dict[str, Any]] = {}

    for entry in entries:
        key = (int(entry["guild_id"]), int(entry["user_id"]))
        w_sec = _calc_entry_worked_seconds(entry, at=now)
        p_sec = int(entry["pause_total_seconds"] or 0)
        if entry.get("status") == "paused" and entry.get("pause_started_at"):
            p_start = entry["pause_started_at"]
            if p_start.tzinfo is None:
                p_start = p_start.replace(tzinfo=UTC)
            p_sec += max(0, int((now - p_start).total_seconds()))

        is_auto_absence = 1 if entry.get("close_reason") == "auto_absence" else 0

        if key not in users_map:
            current_status = "offline"
            if entry["ended_at"] is None:
                current_status = entry["status"]

            users_map[key] = {
                "guild_id": entry["guild_id"],
                "user_id": entry["user_id"],
                "shifts_count": 0,
                "completed_shifts": 0,
                "worked_seconds": 0,
                "pause_seconds": 0,
                "auto_absence_count": 0,
                "first_started_at": entry["started_at"],
                "last_activity_at": entry.get("ended_at") or entry["started_at"],
                "current_status": current_status,
                "open_entry_id": entry["id"] if entry["ended_at"] is None else None,
            }

        u = users_map[key]
        u["shifts_count"] += 1
        if entry["ended_at"] is not None:
            u["completed_shifts"] += 1
        u["worked_seconds"] += w_sec
        u["pause_seconds"] += p_sec
        u["auto_absence_count"] += is_auto_absence

        if entry["started_at"] < u["first_started_at"]:
            u["first_started_at"] = entry["started_at"]

        activity_time = entry.get("ended_at") or entry["started_at"]
        if activity_time > u["last_activity_at"]:
            u["last_activity_at"] = activity_time

        if entry["ended_at"] is None and u["current_status"] == "offline":
            u["current_status"] = entry["status"]
            u["open_entry_id"] = entry["id"]

    results: list[dict[str, Any]] = []
    for u in users_map.values():
        shifts = u["shifts_count"]
        w_sec = u["worked_seconds"]
        avg_sec = int(w_sec / shifts) if shifts > 0 else 0

        results.append(
            {
                **u,
                "worked_formatted": format_seconds(w_sec),
                "worked_hours": round(w_sec / 3600, 2),
                "pause_formatted": format_seconds(u["pause_seconds"]),
                "avg_shift_formatted": format_seconds(avg_sec),
                "first_started_at_formatted": format_dt(u["first_started_at"]),
                "last_activity_at_formatted": format_dt(u["last_activity_at"]),
            }
        )

    # Sort primarily by worked_seconds DESC
    results.sort(key=lambda x: x["worked_seconds"], reverse=True)
    return results


def get_user_stats(
    conn: Connection,
    user_id: int,
    guild_id: int | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> dict[str, Any]:
    """Get aggregated stats for a single user."""
    where_clauses = ["user_id = %s"]
    params: list[Any] = [user_id]

    if guild_id:
        params.append(guild_id)
        where_clauses.append("guild_id = %s")

    if start_date:
        params.append(start_date)
        where_clauses.append("started_at >= %s")

    if end_date:
        params.append(end_date)
        where_clauses.append("started_at <= %s")

    where_sql = " AND ".join(where_clauses)
    query = f"""
        SELECT *
        FROM time_entries
        WHERE {where_sql}
        ORDER BY started_at DESC
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
        entries = list(cur.fetchall())

    now = utcnow()
    shifts_count = len(entries)
    total_worked_seconds = 0
    total_pause_seconds = 0
    total_auto_absence = 0
    current_status = "offline"
    open_entry = None

    for entry in entries:
        w_sec = _calc_entry_worked_seconds(entry, at=now)
        total_worked_seconds += w_sec
        p_sec = int(entry["pause_total_seconds"] or 0)
        if entry.get("status") == "paused" and entry.get("pause_started_at"):
            p_start = entry["pause_started_at"]
            if p_start.tzinfo is None:
                p_start = p_start.replace(tzinfo=UTC)
            p_sec += max(0, int((now - p_start).total_seconds()))
        total_pause_seconds += p_sec

        if entry.get("close_reason") == "auto_absence":
            total_auto_absence += 1

        if entry["ended_at"] is None and open_entry is None:
            open_entry = entry
            current_status = entry["status"]

    avg_seconds = int(total_worked_seconds / shifts_count) if shifts_count > 0 else 0

    return {
        "user_id": user_id,
        "guild_id": guild_id,
        "shifts_count": shifts_count,
        "total_worked_seconds": total_worked_seconds,
        "total_worked_formatted": format_seconds(total_worked_seconds),
        "total_worked_hours": round(total_worked_seconds / 3600, 2),
        "total_pause_seconds": total_pause_seconds,
        "total_pause_formatted": format_seconds(total_pause_seconds),
        "avg_shift_formatted": format_seconds(avg_seconds),
        "total_auto_absence": total_auto_absence,
        "current_status": current_status,
        "open_entry": open_entry,
    }


def get_user_entries(
    conn: Connection,
    user_id: int,
    guild_id: int | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Get detailed time entries for a user with computed durations."""
    where_clauses = ["user_id = %s"]
    params: list[Any] = [user_id]

    if guild_id:
        params.append(guild_id)
        where_clauses.append("guild_id = %s")

    if start_date:
        params.append(start_date)
        where_clauses.append("started_at >= %s")

    if end_date:
        params.append(end_date)
        where_clauses.append("started_at <= %s")

    params.append(limit)
    limit_clause = "LIMIT %s"
    params.append(offset)
    offset_clause = "OFFSET %s"

    where_sql = " AND ".join(where_clauses)
    query = f"""
        SELECT *
        FROM time_entries
        WHERE {where_sql}
        ORDER BY started_at DESC
        {limit_clause} {offset_clause}
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
        rows = list(cur.fetchall())

    now = utcnow()
    results: list[dict[str, Any]] = []
    for row in rows:
        w_sec = _calc_entry_worked_seconds(row, at=now)
        results.append(
            {
                **row,
                "worked_seconds": w_sec,
                "worked_formatted": format_seconds(w_sec),
                "pause_formatted": format_seconds(int(row["pause_total_seconds"] or 0)),
                "started_at_formatted": format_dt(row["started_at"]),
                "ended_at_formatted": (
                    format_dt(row["ended_at"]) if row.get("ended_at") else "Em andamento"
                ),
            }
        )
    return results


def get_user_weekly_totals(
    conn: Connection,
    user_id: int,
    guild_id: int | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> list[dict[str, Any]]:
    """Get worked time grouped by local calendar week for the profile chart."""
    where_clauses = ["user_id = %s"]
    params: list[Any] = [user_id]

    if guild_id:
        params.append(guild_id)
        where_clauses.append("guild_id = %s")
    if start_date:
        params.append(start_date)
        where_clauses.append("started_at >= %s")
    if end_date:
        params.append(end_date)
        where_clauses.append("started_at <= %s")

    query = f"""
        WITH entry_totals AS (
            SELECT
                started_at,
                COALESCE(ended_at, now()) AS effective_end,
                GREATEST(
                    1,
                    EXTRACT(EPOCH FROM (COALESCE(ended_at, now()) - started_at))::BIGINT
                ) AS elapsed_seconds,
                GREATEST(
                    0,
                    EXTRACT(EPOCH FROM (COALESCE(ended_at, now()) - started_at))::BIGINT
                    - pause_total_seconds::BIGINT
                    - CASE
                        WHEN status = 'paused' AND pause_started_at IS NOT NULL
                        THEN EXTRACT(EPOCH FROM (now() - pause_started_at))::BIGINT
                        ELSE 0
                      END
                ) AS net_worked_seconds
            FROM time_entries
            WHERE {" AND ".join(where_clauses)}
        ), weekly_slices AS (
            SELECT
                local_week::date AS week_start,
                net_worked_seconds,
                elapsed_seconds,
                GREATEST(
                    0,
                    EXTRACT(
                        EPOCH FROM (
                            LEAST(
                                effective_end,
                                (local_week + INTERVAL '7 days')
                                    AT TIME ZONE 'America/Fortaleza'
                            )
                            - GREATEST(
                                started_at,
                                local_week AT TIME ZONE 'America/Fortaleza'
                            )
                        )
                    )::BIGINT
                ) AS overlap_seconds
            FROM entry_totals
            CROSS JOIN LATERAL generate_series(
                date_trunc('week', started_at AT TIME ZONE 'America/Fortaleza'),
                date_trunc('week', effective_end AT TIME ZONE 'America/Fortaleza'),
                INTERVAL '1 week'
            ) AS generated(local_week)
        )
        SELECT
            week_start,
            ROUND(
                SUM(
                    net_worked_seconds::NUMERIC
                    * overlap_seconds
                    / elapsed_seconds
                )
            )::BIGINT AS worked_seconds
        FROM weekly_slices
        GROUP BY week_start
        ORDER BY week_start ASC
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
        rows = list(cur.fetchall())

    max_seconds = max((int(row["worked_seconds"]) for row in rows), default=0)
    return [
        {
            "date": row["week_start"].isoformat(),
            "date_label": row["week_start"].strftime("%d/%m"),
            "date_full": (
                f"{row['week_start'].strftime('%d/%m/%Y')} a "
                f"{(row['week_start'] + timedelta(days=6)).strftime('%d/%m/%Y')}"
            ),
            "worked_seconds": int(row["worked_seconds"]),
            "worked_formatted": format_seconds(int(row["worked_seconds"])),
            "bar_percent": (
                max(3, round(int(row["worked_seconds"]) / max_seconds * 100))
                if max_seconds
                else 0
            ),
        }
        for row in rows
    ]


def get_all_entries(
    conn: Connection,
    guild_id: int | None = None,
    user_id: int | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """Get all detailed entries for export or global view."""
    where_clauses = ["1=1"]
    params: list[Any] = []

    if guild_id:
        params.append(guild_id)
        where_clauses.append("guild_id = %s")

    if user_id:
        params.append(user_id)
        where_clauses.append("user_id = %s")

    if start_date:
        params.append(start_date)
        where_clauses.append("started_at >= %s")

    if end_date:
        params.append(end_date)
        where_clauses.append("started_at <= %s")

    params.append(limit)
    limit_clause = "LIMIT %s"

    where_sql = " AND ".join(where_clauses)
    query = f"""
        SELECT *
        FROM time_entries
        WHERE {where_sql}
        ORDER BY started_at DESC
        {limit_clause}
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
        rows = list(cur.fetchall())

    now = utcnow()
    results: list[dict[str, Any]] = []
    for row in rows:
        w_sec = _calc_entry_worked_seconds(row, at=now)
        results.append(
            {
                **row,
                "worked_seconds": w_sec,
                "worked_formatted": format_seconds(w_sec),
                "pause_formatted": format_seconds(int(row["pause_total_seconds"] or 0)),
                "started_at_formatted": format_dt(row["started_at"]),
                "ended_at_formatted": (
                    format_dt(row["ended_at"]) if row.get("ended_at") else "Em andamento"
                ),
            }
        )
    return results


def get_entry_events(conn: Connection, entry_id: int) -> list[dict[str, Any]]:
    """Get timeline events for an entry."""
    query = """
        SELECT *
        FROM time_events
        WHERE entry_id = %s
        ORDER BY occurred_at ASC
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, [entry_id])
        rows = list(cur.fetchall())

    return [
        {
            **row,
            "occurred_at_formatted": format_dt(row["occurred_at"]),
        }
        for row in rows
    ]


def get_recent_events(
    conn: Connection,
    guild_id: int | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Get recent activity events across the system."""
    where_clauses = ["1=1"]
    params: list[Any] = []

    if guild_id:
        params.append(guild_id)
        where_clauses.append("guild_id = %s")

    params.append(limit)
    limit_clause = "LIMIT %s"

    where_sql = " AND ".join(where_clauses)
    query = f"""
        SELECT *
        FROM time_events
        WHERE {where_sql}
        ORDER BY occurred_at DESC
        {limit_clause}
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
        rows = list(cur.fetchall())

    return [
        {
            **row,
            "occurred_at_formatted": format_dt(row["occurred_at"]),
        }
        for row in rows
    ]


def finish_active_entry(conn: Connection, entry_id: int) -> bool:
    """Close an open time entry from the authenticated dashboard."""
    finished_at = utcnow()
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            UPDATE time_entries
            SET ended_at = %s,
                pause_total_seconds = pause_total_seconds + CASE
                    WHEN status = 'paused' AND pause_started_at IS NOT NULL
                    THEN GREATEST(
                        0,
                        EXTRACT(EPOCH FROM (%s - pause_started_at))::INTEGER
                    )
                    ELSE 0
                END,
                status = 'closed',
                pause_started_at = NULL,
                pause_reason = NULL,
                absence_started_at = NULL,
                absence_deadline_at = NULL,
                close_reason = 'admin_dashboard'
            WHERE id = %s AND ended_at IS NULL
            RETURNING id, guild_id, user_id
            """,
            (finished_at, finished_at, entry_id),
        )
        entry = cur.fetchone()
        if entry is None:
            return False

        cur.execute(
            """
            INSERT INTO time_events (
                entry_id, guild_id, user_id, event_type, occurred_at, metadata
            )
            VALUES (%s, %s, %s, 'finished', %s, %s)
            """,
            (
                entry["id"],
                entry["guild_id"],
                entry["user_id"],
                finished_at,
                Jsonb({"source": "dashboard"}),
            ),
        )
    return True
