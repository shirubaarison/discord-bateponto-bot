from __future__ import annotations

import hmac
import logging
import secrets
import time as time_module
from collections import deque
from datetime import UTC, datetime, time, timedelta
from threading import Lock
from typing import Any
from urllib.parse import urlsplit

import psycopg
from flask import Flask, Response, g, redirect, render_template, request, session, url_for
from psycopg.rows import dict_row
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash

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
    finish_active_entry,
    get_active_entries,
    get_all_entries,
    get_dashboard_stats,
    get_entry_events,
    get_guilds,
    get_recent_events,
    get_user_daily_totals,
    get_user_entries,
    get_user_stats,
    get_users_summary,
)

LOGGER = logging.getLogger(__name__)
AUTH_SESSION_KEY = "dashboard_user"
CSRF_SESSION_KEY = "csrf_token"
DEFAULT_SECRET_KEY = "bate-ponto-secret-key-change-in-prod"
EXAMPLE_SECRET_KEY = "gere-uma-chave-aleatoria-com-pelo-menos-32-caracteres"
MAX_TRACKED_LOGIN_CLIENTS = 2048


def _safe_redirect_target(value: str | None) -> str | None:
    """Allow redirects only to local absolute paths."""
    if not value or not value.startswith("/") or value.startswith("//"):
        return None
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        return None
    return value


def _constant_time_text_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


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

    dashboard_username = (app_settings.dashboard_username or "").strip()
    dashboard_password_hash = (app_settings.dashboard_password_hash or "").strip()
    if not dashboard_username or not dashboard_password_hash:
        raise ValueError(
            "Configure DASHBOARD_USERNAME and DASHBOARD_PASSWORD_HASH before starting the dashboard."
        )
    if not dashboard_password_hash.startswith(("scrypt:", "pbkdf2:")):
        raise ValueError("DASHBOARD_PASSWORD_HASH must be a Werkzeug scrypt or PBKDF2 hash.")
    try:
        check_password_hash(dashboard_password_hash, "")
    except ValueError as error:
        raise ValueError("DASHBOARD_PASSWORD_HASH is not a valid password hash.") from error
    if (
        app_settings.dashboard_secret_key in {DEFAULT_SECRET_KEY, EXAMPLE_SECRET_KEY}
        or len(app_settings.dashboard_secret_key) < 32
    ):
        raise ValueError("DASHBOARD_SECRET_KEY must be changed and contain at least 32 characters.")

    app.config["DATABASE_URL"] = app_settings.resolved_database_url
    app.config["DISCORD_TOKEN"] = app_settings.discord_token
    app.config["SECRET_KEY"] = app_settings.dashboard_secret_key
    app.config["DEV_GUILD_ID"] = app_settings.dev_guild_id
    app.config.update(
        MAX_CONTENT_LENGTH=16 * 1024,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=app_settings.dashboard_session_hours),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_PATH="/",
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=app_settings.dashboard_secure_cookies,
        SESSION_REFRESH_EACH_REQUEST=False,
    )

    if app_settings.dashboard_proxy_fix:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)  # type: ignore[method-assign]

    app.teardown_appcontext(close_db)

    failed_logins: dict[str, deque[float]] = {}
    failed_logins_lock = Lock()

    def csrf_token() -> str:
        token = session.get(CSRF_SESSION_KEY)
        if not isinstance(token, str):
            token = secrets.token_urlsafe(32)
            session[CSRF_SESSION_KEY] = token
        return token

    def valid_csrf_token() -> bool:
        supplied_csrf = request.form.get("csrf_token", "")
        expected_csrf = session.get(CSRF_SESSION_KEY, "")
        return bool(expected_csrf) and _constant_time_text_equal(
            supplied_csrf, str(expected_csrf)
        )

    def login_client_key() -> str:
        return request.remote_addr or "unknown"

    def reserve_login_attempt(client_key: str) -> bool:
        with failed_logins_lock:
            if client_key not in failed_logins and len(failed_logins) >= MAX_TRACKED_LOGIN_CLIENTS:
                failed_logins.pop(next(iter(failed_logins)))
            attempts = failed_logins.setdefault(client_key, deque())
            now = time_module.monotonic()
            cutoff = now - app_settings.dashboard_login_window_seconds
            while attempts and attempts[0] < cutoff:
                attempts.popleft()
            if len(attempts) >= app_settings.dashboard_login_max_attempts:
                return False
            attempts.append(now)
            return True

    def clear_login_attempts(client_key: str) -> None:
        with failed_logins_lock:
            failed_logins.pop(client_key, None)

    @app.context_processor
    def inject_auth_context() -> dict[str, Any]:
        return {
            "csrf_token": csrf_token,
            "authenticated_username": session.get(AUTH_SESSION_KEY),
        }

    @app.before_request
    def require_dashboard_login() -> Response | None:
        if request.endpoint in {"login", "healthz", "static"}:
            return None
        if _constant_time_text_equal(str(session.get(AUTH_SESSION_KEY, "")), dashboard_username):
            return None

        next_path = request.full_path[:-1] if request.full_path.endswith("?") else request.full_path
        return redirect(url_for("login", next=next_path))

    @app.after_request
    def apply_security_headers(response: Response) -> Response:
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault(
            "Content-Security-Policy",
            "; ".join(
                (
                    "default-src 'self'",
                    "base-uri 'self'",
                    "object-src 'none'",
                    "frame-ancestors 'none'",
                    "form-action 'self'",
                    "script-src 'self' https://unpkg.com",
                    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
                    "font-src 'self' https://fonts.gstatic.com",
                    "img-src 'self' data: https://cdn.discordapp.com",
                    "connect-src 'self'",
                )
            ),
        )
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response

    @app.route("/login", methods=["GET", "POST"])
    def login() -> str | Response | tuple[str, int]:
        if session.get(AUTH_SESSION_KEY) == dashboard_username:
            return redirect(url_for("index"))

        error: str | None = None
        status = 200
        next_target = _safe_redirect_target(request.values.get("next"))

        if request.method == "POST":
            supplied_csrf = request.form.get("csrf_token", "")
            expected_csrf = session.get(CSRF_SESSION_KEY, "")
            if not expected_csrf or not _constant_time_text_equal(
                supplied_csrf, str(expected_csrf)
            ):
                return render_template(
                    "login.html", error="Sessão inválida. Recarregue a página."
                ), 400

            client_key = login_client_key()
            if not reserve_login_attempt(client_key):
                return (
                    render_template(
                        "login.html",
                        error="Muitas tentativas. Aguarde alguns minutos e tente novamente.",
                    ),
                    429,
                )

            supplied_username = request.form.get("username", "")[:256]
            supplied_password = request.form.get("password", "")[:1024]
            username_matches = _constant_time_text_equal(supplied_username, dashboard_username)
            password_matches = check_password_hash(dashboard_password_hash, supplied_password)

            if username_matches and password_matches:
                clear_login_attempts(client_key)
                session.clear()
                session[AUTH_SESSION_KEY] = dashboard_username
                session.permanent = True
                csrf_token()
                return redirect(next_target or url_for("index"))

            error = "Usuário ou senha inválidos."
            status = 401

        return render_template("login.html", error=error, next_target=next_target), status

    @app.post("/logout")
    def logout() -> Response | tuple[str, int]:
        if not valid_csrf_token():
            return "Requisição inválida.", 400
        session.clear()
        return redirect(url_for("login"))

    @app.post("/entries/<int:entry_id>/finish")
    def finish_entry_from_dashboard(entry_id: int) -> Response | tuple[str, int]:
        if not valid_csrf_token():
            return "Requisição inválida.", 400

        conn = get_db()
        finish_active_entry(conn, entry_id)
        guild_id = request.form.get("guild_id", type=int)
        return redirect(url_for("index", guild_id=guild_id))

    @app.route("/healthz")
    def healthz() -> tuple[dict[str, str], int]:
        try:
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            return {"status": "ok", "db": "connected"}, 200
        except Exception:
            LOGGER.exception("Health check database failure")
            return {"status": "error", "db": "unavailable"}, 500

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
        daily_totals = get_user_daily_totals(
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
            daily_totals=daily_totals,
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
