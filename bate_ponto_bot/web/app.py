from __future__ import annotations

import logging
from datetime import UTC, datetime, time, timedelta
from typing import Any

import psycopg
from flask import Flask, Response, g, render_template, request
from psycopg.rows import dict_row

from bate_ponto_bot.config import Settings
from bate_ponto_bot.utils import LOCAL_TIMEZONE
from bate_ponto_bot.web.csv_export import generate_entries_csv, generate_users_summary_csv
from bate_ponto_bot.web.discord_api import (
    resolve_discord_guild,
    resolve_discord_guilds,
    resolve_discord_user,
    resolve_discord_users,
)
from bate_ponto_bot.web.queries import (
    get_active_entries,
    get_all_entries,
    get_dashboard_stats,
    get_entry_events,
    get_guilds,
    get_recent_events,
    get_user_entries,
    get_user_stats,
    get_users_summary,
)

LOGGER = logging.getLogger(__name__)


def parse_date_filters(
    preset: str | None,
    start_str: str | None,
    end_str: str | None,
) -> tuple[datetime | None, datetime | None, str]:
    """Parse local calendar dates into UTC datetime boundaries."""
    now = datetime.now(LOCAL_TIMEZONE)
    today_start_local = datetime.combine(now.date(), time.min, tzinfo=LOCAL_TIMEZONE)
    today_end_local = datetime.combine(now.date(), time.max, tzinfo=LOCAL_TIMEZONE)

    def as_utc(value: datetime) -> datetime:
        return value.astimezone(UTC)

    if preset == "today":
        return as_utc(today_start_local), as_utc(today_end_local), "today"
    if preset == "week":
        week_start = today_start_local - timedelta(days=now.weekday())
        return as_utc(week_start), as_utc(today_end_local), "week"
    if preset == "month":
        month_start = today_start_local.replace(day=1)
        return as_utc(month_start), as_utc(today_end_local), "month"
    if preset == "last7":
        return as_utc(today_start_local - timedelta(days=6)), as_utc(today_end_local), "last7"
    if preset == "last30":
        return as_utc(today_start_local - timedelta(days=29)), as_utc(today_end_local), "last30"

    start_dt: datetime | None = None
    end_dt: datetime | None = None

    if start_str:
        try:
            d = datetime.strptime(start_str.strip(), "%Y-%m-%d")
            start_dt = as_utc(datetime.combine(d.date(), time.min, tzinfo=LOCAL_TIMEZONE))
        except ValueError:
            pass

    if end_str:
        try:
            d = datetime.strptime(end_str.strip(), "%Y-%m-%d")
            end_dt = as_utc(datetime.combine(d.date(), time.max, tzinfo=LOCAL_TIMEZONE))
        except ValueError:
            pass

    active_preset = "custom" if (start_str or end_str) else "all"
    return start_dt, end_dt, active_preset


def get_db() -> psycopg.Connection:
    """Get or create the request-scoped database connection."""
    if "db" not in g:
        from flask import current_app

        g.db = psycopg.connect(current_app.config["DATABASE_URL"], row_factory=dict_row)
    return g.db


def close_db(exception: Any = None) -> None:
    """Close the request-scoped database connection on teardown."""
    db = g.pop("db", None)
    if db is not None:
        db.close()


def create_app(settings: Settings | None = None) -> Flask:
    """Application factory for the Bate-Ponto Flask web dashboard."""
    app = Flask(__name__)
    app_settings = settings or Settings()

    app.config["DATABASE_URL"] = app_settings.resolved_database_url
    app.config["DISCORD_TOKEN"] = app_settings.discord_token
    app.config["SECRET_KEY"] = app_settings.dashboard_secret_key
    app.config["DEV_GUILD_ID"] = app_settings.dev_guild_id

    app.teardown_appcontext(close_db)

    @app.route("/healthz")
    def healthz() -> tuple[dict[str, str], int]:
        try:
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            return {"status": "ok", "db": "connected"}, 200
        except Exception as e:
            LOGGER.exception("Health check database failure")
            return {"status": "error", "db": str(e)}, 500

    @app.route("/")
    def index() -> str:
        guild_id_param = request.args.get("guild_id", type=int)
        preset = request.args.get("preset")
        start_date_str = request.args.get("start_date")
        end_date_str = request.args.get("end_date")

        start_dt, end_dt, active_preset = parse_date_filters(preset, start_date_str, end_date_str)

        conn = get_db()
        guilds = get_guilds(conn)
        if guild_id_param is None and guilds:
            guild_id_param = int(guilds[0]["guild_id"])

        stats = get_dashboard_stats(
            conn, guild_id=guild_id_param, start_date=start_dt, end_date=end_dt
        )
        active_entries = get_active_entries(conn, guild_id=guild_id_param)
        users_summary = get_users_summary(
            conn, guild_id=guild_id_param, start_date=start_dt, end_date=end_dt
        )

        # Resolve Discord user info for all unique users
        user_ids = list(
            {int(u["user_id"]) for u in users_summary} | {int(e["user_id"]) for e in active_entries}
        )
        resolved_users = resolve_discord_users(app.config.get("DISCORD_TOKEN"), user_ids)
        guild_ids = [int(guild["guild_id"]) for guild in guilds]
        resolved_guilds = resolve_discord_guilds(app.config.get("DISCORD_TOKEN"), guild_ids)

        return render_template(
            "index.html",
            guilds=guilds,
            selected_guild_id=guild_id_param,
            stats=stats,
            active_entries=active_entries,
            users=users_summary,
            resolved_users=resolved_users,
            resolved_guilds=resolved_guilds,
            active_preset=active_preset,
            start_date=start_date_str or "",
            end_date=end_date_str or "",
        )

    @app.route("/users/<int:user_id>")
    @app.route("/users/<int:guild_id>/<int:user_id>")
    def user_detail(user_id: int, guild_id: int | None = None) -> str:
        guild_id_param = guild_id or request.args.get("guild_id", type=int)
        preset = request.args.get("preset")
        start_date_str = request.args.get("start_date")
        end_date_str = request.args.get("end_date")

        start_dt, end_dt, active_preset = parse_date_filters(preset, start_date_str, end_date_str)

        conn = get_db()
        user_stats = get_user_stats(
            conn, user_id=user_id, guild_id=guild_id_param, start_date=start_dt, end_date=end_dt
        )
        entries = get_user_entries(
            conn, user_id=user_id, guild_id=guild_id_param, start_date=start_dt, end_date=end_dt
        )

        resolved_user = resolve_discord_user(app.config.get("DISCORD_TOKEN"), user_id)
        resolved_guild = (
            resolve_discord_guild(app.config.get("DISCORD_TOKEN"), guild_id_param)
            if guild_id_param is not None
            else None
        )

        return render_template(
            "user_detail.html",
            user_id=user_id,
            guild_id=guild_id_param,
            user=resolved_user,
            guild=resolved_guild,
            stats=user_stats,
            entries=entries,
            active_preset=active_preset,
            start_date=start_date_str or "",
            end_date=end_date_str or "",
        )

    @app.route("/events")
    def events_page() -> str:
        guild_id_param = request.args.get("guild_id", type=int)
        conn = get_db()
        guilds = get_guilds(conn)
        if guild_id_param is None and guilds:
            guild_id_param = int(guilds[0]["guild_id"])

        events = get_recent_events(conn, guild_id=guild_id_param, limit=100)

        user_ids = list({int(ev["user_id"]) for ev in events})
        resolved_users = resolve_discord_users(app.config.get("DISCORD_TOKEN"), user_ids)
        guild_ids = [int(guild["guild_id"]) for guild in guilds]
        resolved_guilds = resolve_discord_guilds(app.config.get("DISCORD_TOKEN"), guild_ids)

        return render_template(
            "events.html",
            events=events,
            guilds=guilds,
            selected_guild_id=guild_id_param,
            resolved_users=resolved_users,
            resolved_guilds=resolved_guilds,
        )

    @app.route("/api/entries/<int:entry_id>/events")
    def api_entry_events(entry_id: int) -> dict[str, Any]:
        conn = get_db()
        events = get_entry_events(conn, entry_id)
        return {"entry_id": entry_id, "events": events}

    # CSV EXPORTS
    @app.route("/export/summary")
    @app.route("/export/summary.csv")
    def export_summary() -> Response:
        guild_id_param = request.args.get("guild_id", type=int)
        preset = request.args.get("preset")
        start_date_str = request.args.get("start_date")
        end_date_str = request.args.get("end_date")

        start_dt, end_dt, _ = parse_date_filters(preset, start_date_str, end_date_str)

        conn = get_db()
        users_summary = get_users_summary(
            conn, guild_id=guild_id_param, start_date=start_dt, end_date=end_dt
        )
        user_ids = [int(u["user_id"]) for u in users_summary]
        resolved = resolve_discord_users(app.config.get("DISCORD_TOKEN"), user_ids)

        csv_content = generate_users_summary_csv(users_summary, resolved)
        today_tag = datetime.now(UTC).strftime("%Y%m%d")
        filename = f"bate-ponto-resumo-colaboradores-{today_tag}.csv"

        return Response(
            csv_content,
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.route("/export/entries")
    @app.route("/export/entries.csv")
    def export_entries() -> Response:
        guild_id_param = request.args.get("guild_id", type=int)
        user_id_param = request.args.get("user_id", type=int)
        preset = request.args.get("preset")
        start_date_str = request.args.get("start_date")
        end_date_str = request.args.get("end_date")

        start_dt, end_dt, _ = parse_date_filters(preset, start_date_str, end_date_str)

        conn = get_db()
        entries = get_all_entries(
            conn,
            guild_id=guild_id_param,
            user_id=user_id_param,
            start_date=start_dt,
            end_date=end_dt,
            limit=5000,
        )
        user_ids = list({int(e["user_id"]) for e in entries})
        resolved = resolve_discord_users(app.config.get("DISCORD_TOKEN"), user_ids)

        csv_content = generate_entries_csv(entries, resolved)
        today_tag = datetime.now(UTC).strftime("%Y%m%d")
        filename = f"bate-ponto-registros-detalhados-{today_tag}.csv"

        return Response(
            csv_content,
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.route("/export/user/<int:user_id>")
    @app.route("/export/user/<int:user_id>.csv")
    @app.route("/export/user/<int:guild_id>/<int:user_id>")
    def export_single_user(user_id: int, guild_id: int | None = None) -> Response:
        guild_id_param = guild_id or request.args.get("guild_id", type=int)
        preset = request.args.get("preset")
        start_date_str = request.args.get("start_date")
        end_date_str = request.args.get("end_date")

        start_dt, end_dt, _ = parse_date_filters(preset, start_date_str, end_date_str)

        conn = get_db()
        entries = get_user_entries(
            conn,
            user_id=user_id,
            guild_id=guild_id_param,
            start_date=start_dt,
            end_date=end_dt,
            limit=5000,
        )
        resolved = {user_id: resolve_discord_user(app.config.get("DISCORD_TOKEN"), user_id)}

        csv_content = generate_entries_csv(entries, resolved)
        today_tag = datetime.now(UTC).strftime("%Y%m%d")
        filename = f"bate-ponto-usuario-{user_id}-{today_tag}.csv"

        return Response(
            csv_content,
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return app


def main() -> None:
    """Run Flask dashboard CLI."""
    settings = Settings()
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app = create_app(settings)
    LOGGER.info(
        "Iniciando dashboard Flask em http://%s:%s",
        settings.dashboard_host,
        settings.dashboard_port,
    )
    app.run(
        host=settings.dashboard_host,
        port=settings.dashboard_port,
        debug=settings.dashboard_debug,
    )


if __name__ == "__main__":
    main()
