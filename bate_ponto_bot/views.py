from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import discord

from bate_ponto_bot.models import WeeklyHours
from bate_ponto_bot.utils import (
    DEFAULT_TIMEZONE,
    format_dt,
    format_seconds,
    utcnow,
    worked_seconds,
)

if TYPE_CHECKING:
    from bate_ponto_bot.bot import TimeClockBot


async def require_member(interaction: discord.Interaction) -> discord.Member | None:
    if not isinstance(interaction.user, discord.Member) or interaction.guild is None:
        await interaction.response.send_message(
            "Esse comando só funciona em um servidor.", ephemeral=True
        )
        return None
    return interaction.user


async def can_use_voice(bot: TimeClockBot, member: discord.Member) -> tuple[bool, str]:
    allowed_channels = await bot.db.list_allowed_voice_channels(member.guild.id)
    if not allowed_channels:
        return False, "Nenhum canal de voz permitido foi configurado."

    current_channel = member.voice.channel if member.voice else None
    if current_channel is None or current_channel.id not in allowed_channels:
        return (
            False,
            "Você precisa estar em um canal de voz permitido para iniciar ou retomar o ponto.",
        )

    return True, ""


class TimeClockView(discord.ui.View):
    def __init__(self, bot: TimeClockBot) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Iniciar ponto",
        style=discord.ButtonStyle.success,
        custom_id="timeclock:start",
    )
    async def start_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        member = await require_member(interaction)
        if member is None:
            return
        tz = self.bot.timezone_for(member.guild.id)

        allowed, reason = await can_use_voice(self.bot, member)
        if not allowed:
            self.bot.log_interaction("button_start", "denied_voice", interaction=interaction)
            await self.bot.respond_control_interaction(interaction, reason)
            return

        existing = await self.bot.db.get_open_entry(member.guild.id, member.id)
        if existing:
            self.bot.log_interaction(
                "button_start",
                "denied_already_open",
                interaction=interaction,
                entry_id=existing.id,
            )
            await self.bot.respond_control_interaction(
                interaction,
                "Você já tem um ponto em aberto.",
            )
            return

        entry = await self.bot.db.start_entry(member.guild.id, member.id)
        self.bot.log_interaction(
            "button_start",
            "success",
            interaction=interaction,
            channel_id=member.voice.channel.id if member.voice and member.voice.channel else None,
            entry_id=entry.id,
        )
        await self.bot.respond_control_embed(
            interaction,
            "Ponto ativo",
            f"{member.mention}, seu ponto foi iniciado.",
            color=discord.Color.green(),
            fields=[
                ("Status", "Ativo", True),
                ("Início", format_dt(entry.started_at, tz), True),
                (
                    "Canal de voz",
                    member.voice.channel.mention if member.voice else "desconhecido",
                    True,
                ),
            ],
        )
        await self.bot.audit(
            member.guild,
            "Ponto iniciado",
            f"{member.mention} iniciou um novo ponto.",
            color=discord.Color.green(),
            fields=[
                ("Início", format_dt(entry.started_at, tz), True),
                (
                    "Canal de voz",
                    member.voice.channel.mention if member.voice else "desconhecido",
                    True,
                ),
            ],
            user=member,
        )

    @discord.ui.button(
        label="Finalizar",
        style=discord.ButtonStyle.danger,
        custom_id="timeclock:finish",
    )
    async def finish_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        member = await require_member(interaction)
        if member is None:
            return
        tz = self.bot.timezone_for(member.guild.id)

        entry = await self.bot.db.get_open_entry(member.guild.id, member.id)
        if not entry:
            self.bot.log_interaction(
                "button_finish", "denied_no_open_entry", interaction=interaction
            )
            await self.bot.respond_control_interaction(
                interaction,
                "Você não tem ponto em aberto.",
            )
            return

        finished = await self.bot.db.finish_entry(entry)
        weekly_hours = await self.bot.db.list_weekly_hours(member.guild.id, member.id)
        current_week_total = current_week_worked_seconds(weekly_hours, week_start=local_week_start(tz))
        self.bot._cancel_absence_check(entry.id)
        self.bot.log_interaction(
            "button_finish",
            "success",
            interaction=interaction,
            entry_id=finished.id,
            worked_seconds=worked_seconds(finished),
        )
        await self.bot.delete_point_status_message(member.guild, entry.id)
        await self.bot.respond_control_embed(
            interaction,
            "Ponto finalizado",
            f"{member.mention}, seu ponto foi encerrado.",
            color=discord.Color.red(),
            fields=[
                ("Início", format_dt(finished.started_at, tz), True),
                ("Fim", format_dt(finished.ended_at, tz), True),
                ("Tempo trabalhado", format_seconds(worked_seconds(finished)), True),
                ("Tempo pausado", format_seconds(finished.pause_total_seconds), True),
                ("Total na semana vigente", format_seconds(current_week_total), False),
            ],
        )
        await self.bot.audit(
            member.guild,
            "Ponto finalizado",
            f"{member.mention} finalizou o ponto.",
            color=discord.Color.red(),
            fields=[
                ("Início", format_dt(finished.started_at, tz), True),
                ("Fim", format_dt(finished.ended_at, tz), True),
                ("Tempo trabalhado", format_seconds(worked_seconds(finished)), True),
                ("Tempo pausado", format_seconds(finished.pause_total_seconds), True),
                ("Total na semana vigente", format_seconds(current_week_total), False),
            ],
            user=member,
        )

    @discord.ui.button(
        label="Pausar/Retomar",
        style=discord.ButtonStyle.secondary,
        custom_id="timeclock:pause_resume",
    )
    async def pause_resume_button(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        member = await require_member(interaction)
        if member is None:
            return
        tz = self.bot.timezone_for(member.guild.id)

        entry = await self.bot.db.get_open_entry(member.guild.id, member.id)
        if not entry:
            self.bot.log_interaction(
                "button_pause_resume",
                "denied_no_open_entry",
                interaction=interaction,
            )
            await self.bot.respond_control_interaction(
                interaction,
                "Você não tem ponto em aberto.",
            )
            return

        if entry.status == "active":
            paused = await self.bot.db.pause_entry(entry, reason="manual")
            self.bot._cancel_absence_check(entry.id)
            self.bot.log_interaction(
                "button_pause_resume",
                "paused",
                interaction=interaction,
                entry_id=paused.id,
                worked_seconds=worked_seconds(paused),
            )
            await self.bot.delete_point_status_message(member.guild, entry.id)
            await self.bot.respond_control_embed(
                interaction,
                "Ponto pausado",
                f"{member.mention}, seu ponto foi pausado.",
                color=discord.Color.orange(),
                fields=[
                    ("Status", "Pausado", True),
                    ("Pausado em", format_dt(paused.pause_started_at, tz), True),
                    ("Tempo trabalhado", format_seconds(worked_seconds(paused)), True),
                ],
            )
            await self.bot.audit(
                member.guild,
                "Ponto pausado",
                f"{member.mention} pausou o ponto manualmente.",
                color=discord.Color.orange(),
                fields=[
                    ("Pausado em", format_dt(paused.pause_started_at, tz), True),
                    ("Tempo trabalhado", format_seconds(worked_seconds(paused)), True),
                ],
                user=member,
            )
            return

        allowed, reason = await can_use_voice(self.bot, member)
        if not allowed:
            self.bot.log_interaction(
                "button_pause_resume",
                "denied_voice",
                interaction=interaction,
                entry_id=entry.id,
            )
            await self.bot.respond_control_interaction(interaction, reason)
            return

        resumed = await self.bot.db.resume_entry(entry)
        self.bot.log_interaction(
            "button_pause_resume",
            "resumed",
            interaction=interaction,
            channel_id=member.voice.channel.id if member.voice and member.voice.channel else None,
            entry_id=resumed.id,
            worked_seconds=worked_seconds(resumed),
        )
        await self.bot.delete_point_status_message(member.guild, entry.id)
        await self.bot.respond_control_embed(
            interaction,
            "Ponto ativo",
            f"{member.mention}, seu ponto foi retomado.",
            color=discord.Color.green(),
            fields=[
                ("Status", "Ativo", True),
                ("Retomado em", format_dt(utcnow(), tz), True),
                ("Tempo trabalhado", format_seconds(worked_seconds(resumed)), True),
                (
                    "Canal de voz",
                    member.voice.channel.mention if member.voice else "desconhecido",
                    True,
                ),
            ],
        )
        await self.bot.audit(
            member.guild,
            "Ponto retomado",
            f"{member.mention} retomou o ponto.",
            color=discord.Color.green(),
            fields=[
                ("Retomado em", format_dt(utcnow(), tz), True),
                ("Tempo trabalhado", format_seconds(worked_seconds(resumed)), True),
            ],
            user=member,
        )

    @discord.ui.button(
        label="Status",
        style=discord.ButtonStyle.primary,
        custom_id="timeclock:status",
    )
    async def status_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        member = await require_member(interaction)
        if member is None:
            return
        tz = self.bot.timezone_for(member.guild.id)

        entry = await self.bot.db.get_open_entry(member.guild.id, member.id)
        if not entry:
            self.bot.log_interaction(
                "button_status", "denied_no_open_entry", interaction=interaction
            )
            await self.bot.respond_control_interaction(
                interaction,
                "Você não tem ponto em aberto.",
            )
            return

        self.bot.log_interaction(
            "button_status",
            "success",
            interaction=interaction,
            entry_id=entry.id,
            status=entry.status,
        )
        status = "ativo" if entry.status == "active" else "pausado"
        status_title = "Ponto ativo" if entry.status == "active" else "Ponto pausado"
        status_color = discord.Color.green() if entry.status == "active" else discord.Color.orange()
        await self.bot.delete_point_status_message(member.guild, entry.id)
        fields = [
            ("Status", status.title(), True),
            ("Início", format_dt(entry.started_at, tz), True),
            ("Tempo trabalhado", format_seconds(worked_seconds(entry)), True),
        ]
        if entry.status == "paused":
            fields.append(("Pausado em", format_dt(entry.pause_started_at, tz), True))

        await self.bot.respond_control_embed(
            interaction,
            status_title,
            f"{member.mention}, este é o status atual do seu ponto.",
            color=status_color,
            fields=fields,
        )

    @discord.ui.button(
        label="Histórico",
        style=discord.ButtonStyle.secondary,
        custom_id="timeclock:history",
    )
    async def history_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        member = await require_member(interaction)
        if member is None:
            return
        tz = self.bot.timezone_for(member.guild.id)

        history = await self.bot.db.list_history(member.guild.id, member.id)
        if not history:
            self.bot.log_interaction("button_history", "empty", interaction=interaction)
            await self.bot.respond_control_interaction(
                interaction,
                "Você ainda não tem histórico.",
            )
            return

        self.bot.log_interaction(
            "button_history",
            "success",
            interaction=interaction,
            count=len(history),
        )
        fields = [
            (
                format_dt(entry.started_at, tz),
                f"Fim: {format_dt(entry.ended_at, tz)}\n"
                f"Tempo trabalhado: {format_seconds(worked_seconds(entry))}",
                False,
            )
            for entry in history
        ]
        total_seconds = sum(worked_seconds(entry) for entry in history)
        await self.bot.respond_control_embed(
            interaction,
            "Histórico recente",
            f"Total listado: {format_seconds(total_seconds)}",
            color=discord.Color.blurple(),
            fields=fields,
        )

    @discord.ui.button(
        label="Ver informações",
        style=discord.ButtonStyle.secondary,
        custom_id="timeclock:weekly_info",
        row=1,
    )
    async def weekly_info_button(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        member = await require_member(interaction)
        if member is None:
            return
        tz = self.bot.timezone_for(member.guild.id)

        weekly_hours = await self.bot.db.list_weekly_hours(member.guild.id, member.id)
        time_info = await self.bot.db.get_user_time_info(member.guild.id, member.id)
        if not weekly_hours or time_info is None:
            self.bot.log_interaction("button_weekly_info", "empty", interaction=interaction)
            await self.bot.respond_control_interaction(
                interaction,
                "Você ainda não tem horas registradas.",
            )
            return

        self.bot.log_interaction(
            "button_weekly_info",
            "success",
            interaction=interaction,
            count=len(weekly_hours),
        )
        current_week_total = current_week_worked_seconds(weekly_hours, week_start=local_week_start(tz))
        summary_fields = [
            ("Primeiro bate-ponto", format_dt(time_info.first_started_at, tz), True),
            ("Último bate-ponto", format_dt(time_info.last_activity_at, tz), True),
            ("Tempo total", format_seconds(time_info.worked_seconds), True),
            ("Pontos registrados", str(time_info.shifts_count), True),
            ("Semana vigente", f"**{format_seconds(current_week_total)}**", False),
        ]
        await self.bot.respond_control_embed(
            interaction,
            "Informações do usuário",
            f"Resumo de {member.mention} e horas registradas por semana.",
            color=discord.Color.gold(),
            fields=summary_fields + weekly_hours_fields(weekly_hours, current_week_start=local_week_start(tz)),
        )


def local_week_start(tz: ZoneInfo = DEFAULT_TIMEZONE) -> date:
    today = utcnow().astimezone(tz).date()
    return today - timedelta(days=today.weekday())


def current_week_worked_seconds(
    items: list[WeeklyHours], week_start: date | None = None
) -> int:
    expected_week_start = week_start or local_week_start()
    return next(
        (item.worked_seconds for item in items if item.week_start == expected_week_start),
        0,
    )


def weekly_hours_fields(
    items: list[WeeklyHours], current_week_start: date | None = None
) -> list[tuple[str, str, bool]]:
    highlighted_week = current_week_start or local_week_start()
    return [
        (
            ("Semana vigente • " if item.week_start == highlighted_week else "Semana • ")
            + f"{item.week_start:%d/%m/%Y} a "
            f"{item.week_start + timedelta(days=6):%d/%m/%Y}",
            (
                f"**{format_seconds(item.worked_seconds)}**"
                if item.week_start == highlighted_week
                else format_seconds(item.worked_seconds)
            ),
            False,
        )
        for item in items
    ]
