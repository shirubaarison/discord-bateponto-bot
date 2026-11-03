from __future__ import annotations

import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import psycopg
from flask import (
    Flask,
    Response,
    abort,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from psycopg.rows import dict_row
from werkzeug.middleware.proxy_fix import ProxyFix

from bate_ponto_bot.config import Settings
from bate_ponto_bot.utils import DEFAULT_TIMEZONE
from bate_ponto_bot.web.csv_export import generate_entries_csv, generate_users_summary_csv
from bate_ponto_bot.web.discord_api import (
    DiscordOAuthError,
    build_authorize_url,
    build_bot_invite_url,
    exchange_oauth_code,
    fetch_oauth_guilds,
    fetch_oauth_user,
    manageable_guild_ids,
    resolve_discord_guild,
    resolve_discord_guilds,
    resolve_discord_user,
    resolve_discord_users,
)
from bate_ponto_bot.web.queries import (
    edit_entry_times,
    finish_active_entry,
    get_active_entries,
    get_all_entries,
    get_dashboard_stats,
    get_dashboard_user_guild_ids,
    get_entry_events,
    get_entry_guild_id,
    get_guild_timezone,
    get_guilds,
    get_recent_events,
    get_user_entries,
    get_user_stats,
    get_user_weekly_totals,
    get_users_summary,
    save_dashboard_user_login,
)

LOGGER = logging.getLogger(__name__)
AUTH_SESSION_KEY = "dashboard_user"
AUTH_KIND_SESSION_KEY = "auth_kind"
DISCORD_USER_SESSION_KEY = "discord_user_id"
OAUTH_STATE_SESSION_KEY = "oauth_state"
OAUTH_NEXT_SESSION_KEY = "oauth_next"
CSRF_SESSION_KEY = "csrf_token"
DEFAULT_SECRET_KEY = "bate-ponto-secret-key-change-in-prod"
EXAMPLE_SECRET_KEY = "gere-uma-chave-aleatoria-com-pelo-menos-32-caracteres"


@dataclass(frozen=True)
class Viewer:
    """The authenticated dashboard user.

    ``guild_ids`` is None for super-admins, who can see every guild.
    """

    name: str
    discord_user_id: int | None = None
    guild_ids: frozenset[int] | None = None

    @property
    def is_admin(self) -> bool:
        return self.guild_ids is None

    @property
    def audit_name(self) -> str:
        if self.discord_user_id is None:
            return self.name
        return f"{self.name} (Discord {self.discord_user_id})"


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
    tz: ZoneInfo = DEFAULT_TIMEZONE,
) -> tuple[datetime | None, datetime | None, str]:
    """Parse calendar dates in the guild's time zone into UTC datetime boundaries."""
    now = datetime.now(tz)
    today_start_local = datetime.combine(now.date(), time.min, tzinfo=tz)
    today_end_local = datetime.combine(now.date(), time.max, tzinfo=tz)

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
            start_dt = as_utc(datetime.combine(d.date(), time.min, tzinfo=tz))
        except ValueError:
            pass

    if end_str:
        try:
            d = datetime.strptime(end_str.strip(), "%Y-%m-%d")
            end_dt = as_utc(datetime.combine(d.date(), time.max, tzinfo=tz))
        except ValueError:
            pass

    active_preset = "custom" if (start_str or end_str) else "all"
    return start_dt, end_dt, active_preset


def parse_datetime_local_input(value: str, tz: ZoneInfo = DEFAULT_TIMEZONE) -> datetime:
    parsed = datetime.strptime(value.strip(), "%Y-%m-%dT%H:%M")
    return parsed.replace(tzinfo=tz).astimezone(UTC)


def parse_duration_input(value: str) -> int:
    parts = value.strip().split(":")
    if len(parts) != 3:
        raise ValueError("duration must be HH:MM:SS")
    hours, minutes, seconds = (int(part) for part in parts)
    if hours < 0 or not 0 <= minutes <= 59 or not 0 <= seconds <= 59:
        raise ValueError("invalid duration")
    return hours * 3600 + minutes * 60 + seconds


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

    discord_client_secret = (app_settings.discord_client_secret or "").strip()
    if not app_settings.discord_client_id or not discord_client_secret:
        raise ValueError(
            "Configure DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET before starting the dashboard."
        )
    discord_client_id: int = app_settings.discord_client_id
    admin_discord_ids = app_settings.admin_discord_ids
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
        # Lax (not Strict) so the cookie survives the redirect back from Discord's
        # OAuth page; state-changing requests are still protected by CSRF tokens.
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=app_settings.dashboard_secure_cookies,
        SESSION_REFRESH_EACH_REQUEST=False,
    )

    if app_settings.dashboard_proxy_fix:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)  # type: ignore[method-assign]

    app.teardown_appcontext(close_db)

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

    def current_viewer() -> Viewer | None:
        if "viewer" in g:
            return g.viewer

        viewer: Viewer | None = None
        discord_user_id = session.get(DISCORD_USER_SESSION_KEY)
        if session.get(AUTH_KIND_SESSION_KEY) == "discord" and isinstance(discord_user_id, int):
            guild_ids = get_dashboard_user_guild_ids(get_db(), discord_user_id)
            if guild_ids is not None:
                viewer = Viewer(
                    name=str(session.get(AUTH_SESSION_KEY, "")),
                    discord_user_id=discord_user_id,
                    guild_ids=None if discord_user_id in admin_discord_ids else frozenset(guild_ids),
                )

        g.viewer = viewer
        return viewer

    def require_viewer() -> Viewer:
        viewer = current_viewer()
        if viewer is None:
            abort(401)
        return viewer

    def visible_guilds(conn: psycopg.Connection) -> list[dict[str, Any]]:
        viewer = require_viewer()
        guilds = get_guilds(conn)
        if viewer.guild_ids is None:
            return guilds
        return [guild for guild in guilds if int(guild["guild_id"]) in viewer.guild_ids]

    def scope_guild_id(
        requested: int | None,
        guilds: list[dict[str, Any]],
        *,
        default_first: bool,
    ) -> int | None:
        """Pick the guild a request works on, rejecting guilds the viewer cannot access.

        The super-admin may pass None to query every guild; Discord users always get
        a concrete guild they manage.
        """
        viewer = require_viewer()
        guild_ids = [int(guild["guild_id"]) for guild in guilds]
        if viewer.is_admin:
            if requested is None and default_first and guild_ids:
                return guild_ids[0]
            return requested
        if requested is None:
            if not guild_ids:
                abort(404)
            return guild_ids[0]
        if requested not in guild_ids:
            abort(404)
        return requested

    def scope_guild_id_for_viewer(conn: psycopg.Connection, requested: int | None) -> int | None:
        if require_viewer().is_admin:
            return requested
        return scope_guild_id(requested, visible_guilds(conn), default_first=False)

    def require_entry_access(conn: psycopg.Connection, entry_id: int) -> None:
        viewer = require_viewer()
        if viewer.guild_ids is None:
            return
        guild_id = get_entry_guild_id(conn, entry_id)
        if guild_id is None or guild_id not in viewer.guild_ids:
            abort(404)

    def discord_redirect_uri() -> str:
        return app_settings.discord_redirect_uri or url_for("discord_callback", _external=True)

    def render_login(error: str | None = None, next_target: str | None = None) -> str:
        return render_template("login.html", error=error, next_target=next_target)

    @app.context_processor
    def inject_auth_context() -> dict[str, Any]:
        viewer = g.get("viewer")
        return {
            "csrf_token": csrf_token,
            "viewer": viewer,
            "authenticated_username": viewer.name if viewer else None,
            "bot_invite_url": build_bot_invite_url(discord_client_id),
        }

    @app.before_request
    def require_dashboard_login() -> Response | None:
        if request.endpoint in {"login", "discord_login", "discord_callback", "healthz", "static"}:
            return None
        if current_viewer() is not None:
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

    @app.get("/login")
    def login() -> str | Response:
        if current_viewer() is not None:
            return redirect(url_for("index"))
        return render_login(next_target=_safe_redirect_target(request.args.get("next")))

    @app.get("/auth/discord")
    def discord_login() -> Response:
        state = secrets.token_urlsafe(32)
        session[OAUTH_STATE_SESSION_KEY] = state
        session[OAUTH_NEXT_SESSION_KEY] = _safe_redirect_target(request.args.get("next"))
        return redirect(build_authorize_url(discord_client_id, discord_redirect_uri(), state))

    @app.get("/auth/discord/callback")
    def discord_callback() -> Response | tuple[str, int]:
        expected_state = session.pop(OAUTH_STATE_SESSION_KEY, None)
        next_target = _safe_redirect_target(session.pop(OAUTH_NEXT_SESSION_KEY, None))
        supplied_state = request.args.get("state", "")
        if not isinstance(expected_state, str) or not _constant_time_text_equal(
            supplied_state, expected_state
        ):
            return render_login("Sessão de login expirada. Tente novamente."), 400

        code = request.args.get("code", "")
        if request.args.get("error") or not code:
            return render_login("Login com o Discord cancelado."), 400

        try:
            access_token = exchange_oauth_code(
                discord_client_id, discord_client_secret, code, discord_redirect_uri()
            )
            discord_user = fetch_oauth_user(access_token)
            discord_guilds = fetch_oauth_guilds(access_token)
        except DiscordOAuthError as error:
            LOGGER.warning("Discord OAuth login failed: %s", error)
            return render_login("Não foi possível entrar com o Discord. Tente novamente."), 502

        discord_user_id = int(discord_user["id"])
        username = str(discord_user.get("username") or discord_user_id)
        display_name = discord_user.get("global_name") or None
        save_dashboard_user_login(
            get_db(),
            discord_user_id=discord_user_id,
            username=username,
            display_name=display_name,
            avatar_hash=discord_user.get("avatar") or None,
            guild_ids=manageable_guild_ids(discord_guilds),
        )

        session.clear()
        session[AUTH_KIND_SESSION_KEY] = "discord"
        session[DISCORD_USER_SESSION_KEY] = discord_user_id
        session[AUTH_SESSION_KEY] = display_name or username
        session.permanent = True
        csrf_token()
        return redirect(next_target or url_for("index"))

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
        require_entry_access(conn, entry_id)
        finish_active_entry(conn, entry_id)
        guild_id = request.form.get("guild_id", type=int)
        return redirect(url_for("index", guild_id=guild_id))

    @app.post("/entries/<int:entry_id>/edit")
    def edit_entry_from_dashboard(entry_id: int) -> Response | tuple[str, int]:
        if not valid_csrf_token():
            return "Requisição inválida.", 400

        conn = get_db()
        require_entry_access(conn, entry_id)
        tz = get_guild_timezone(conn, get_entry_guild_id(conn, entry_id))
        try:
            started_at = parse_datetime_local_input(request.form.get("started_at", ""), tz)
            ended_at = parse_datetime_local_input(request.form.get("ended_at", ""), tz)
            worked_seconds = parse_duration_input(request.form.get("worked_duration", ""))
        except ValueError:
            return "Informe início, fim e tempo trabalhado válidos.", 400

        if ended_at <= started_at:
            return "O fim deve ser posterior ao início.", 400
        if worked_seconds > int((ended_at - started_at).total_seconds()):
            return "O tempo trabalhado não pode exceder o intervalo entre início e fim.", 400

        entry = edit_entry_times(
            conn,
            entry_id=entry_id,
            started_at=started_at,
            ended_at=ended_at,
            editor_username=require_viewer().audit_name,
            worked_seconds=worked_seconds,
        )
        if entry is None:
            return "Somente pontos finalizados podem ser editados.", 409

        return redirect(
            url_for(
                "user_detail",
                user_id=entry["user_id"],
                guild_id=entry["guild_id"],
            )
        )

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
        preset = request.args.get("preset")
        start_date_str = request.args.get("start_date")
        end_date_str = request.args.get("end_date")

        conn = get_db()
        guilds = visible_guilds(conn)
        if not guilds and not require_viewer().is_admin:
            return render_template("no_guilds.html")
        guild_id_param = scope_guild_id(
            request.args.get("guild_id", type=int), guilds, default_first=True
        )
        tz = get_guild_timezone(conn, guild_id_param)
        start_dt, end_dt, active_preset = parse_date_filters(
            preset, start_date_str, end_date_str, tz
        )

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
        preset = request.args.get("preset")
        start_date_str = request.args.get("start_date")
        end_date_str = request.args.get("end_date")

        conn = get_db()
        guild_id_param = scope_guild_id_for_viewer(
            conn, guild_id or request.args.get("guild_id", type=int)
        )
        start_dt, end_dt, active_preset = parse_date_filters(
            preset, start_date_str, end_date_str, get_guild_timezone(conn, guild_id_param)
        )
        user_stats = get_user_stats(
            conn, user_id=user_id, guild_id=guild_id_param, start_date=start_dt, end_date=end_dt
        )
        entries = get_user_entries(
            conn, user_id=user_id, guild_id=guild_id_param, start_date=start_dt, end_date=end_dt
        )
        weekly_totals = get_user_weekly_totals(
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
            weekly_totals=weekly_totals,
            active_preset=active_preset,
            start_date=start_date_str or "",
            end_date=end_date_str or "",
        )

    @app.route("/events")
    def events_page() -> str:
        conn = get_db()
        guilds = visible_guilds(conn)
        if not guilds and not require_viewer().is_admin:
            return render_template("no_guilds.html")
        guild_id_param = scope_guild_id(
            request.args.get("guild_id", type=int), guilds, default_first=True
        )

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
        require_entry_access(conn, entry_id)
        events = get_entry_events(conn, entry_id)
        return {"entry_id": entry_id, "events": events}

    # CSV EXPORTS
    @app.route("/export/summary")
    @app.route("/export/summary.csv")
    def export_summary() -> Response:
        preset = request.args.get("preset")
        start_date_str = request.args.get("start_date")
        end_date_str = request.args.get("end_date")

        conn = get_db()
        guild_id_param = scope_guild_id_for_viewer(conn, request.args.get("guild_id", type=int))
        start_dt, end_dt, _ = parse_date_filters(
            preset, start_date_str, end_date_str, get_guild_timezone(conn, guild_id_param)
        )
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
        user_id_param = request.args.get("user_id", type=int)
        preset = request.args.get("preset")
        start_date_str = request.args.get("start_date")
        end_date_str = request.args.get("end_date")

        conn = get_db()
        guild_id_param = scope_guild_id_for_viewer(conn, request.args.get("guild_id", type=int))
        start_dt, end_dt, _ = parse_date_filters(
            preset, start_date_str, end_date_str, get_guild_timezone(conn, guild_id_param)
        )
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
        preset = request.args.get("preset")
        start_date_str = request.args.get("start_date")
        end_date_str = request.args.get("end_date")

        conn = get_db()
        guild_id_param = scope_guild_id_for_viewer(
            conn, guild_id or request.args.get("guild_id", type=int)
        )
        start_dt, end_dt, _ = parse_date_filters(
            preset, start_date_str, end_date_str, get_guild_timezone(conn, guild_id_param)
        )
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
