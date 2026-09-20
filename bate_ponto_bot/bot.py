from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import discord
from discord.ext import commands

from bate_ponto_bot.commands import (
    ConfigGroup,
    is_text_app_channel,
    is_voice_app_channel,
    require_guild,
    resolve_app_channel,
)
from bate_ponto_bot.config import Settings
from bate_ponto_bot.db import Database, TimeEntry
from bate_ponto_bot.utils import (
    LOCAL_TIMEZONE,
    channel_mention,
    format_dt,
    format_duration_words,
    format_seconds,
    utcnow,
    worked_seconds,
)
from bate_ponto_bot.views import TimeClockView, can_use_voice, require_member

__all__ = [
    "LOCAL_TIMEZONE",
    "ConfigGroup",
    "TimeClockBot",
    "TimeClockView",
    "can_use_voice",
    "channel_mention",
    "format_dt",
    "format_duration_words",
    "format_seconds",
    "is_text_app_channel",
    "is_voice_app_channel",
    "main",
    "require_guild",
    "require_member",
    "resolve_app_channel",
]

LOGGER = logging.getLogger(__name__)
INTERACTION_LOGGER = logging.getLogger("bate_ponto_bot.interactions")


class TimeClockBot(commands.Bot):
    def __init__(self, database: Database, dev_guild_id: int | None = None) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.messages = True
        intents.voice_states = True

        super().__init__(command_prefix="!", intents=intents)
        self.db = database
        self.dev_guild_id = dev_guild_id
        self.absence_tasks: dict[int, asyncio.Task[None]] = {}
        self.cleanup_tasks: set[asyncio.Task[None]] = set()
        self.pause_expiration_task: asyncio.Task[None] | None = None

    async def setup_hook(self) -> None:
        await self.db.connect()
        self._start_pause_expiration_job()
        self.add_view(TimeClockView(self))
        self.tree.add_command(ConfigGroup(self))

        if self.dev_guild_id:
            guild = discord.Object(id=self.dev_guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            LOGGER.info("Comandos slash sincronizados no servidor %s.", self.dev_guild_id)
        else:
            await self.tree.sync()
            LOGGER.info("Comandos slash globais sincronizados.")

    async def close(self) -> None:
        for task in self.absence_tasks.values():
            task.cancel()
        for task in self.cleanup_tasks:
            task.cancel()
        if self.pause_expiration_task:
            self.pause_expiration_task.cancel()
        await self.db.close()
        await super().close()

    async def on_ready(self) -> None:
        assert self.user is not None
        LOGGER.info("Bot conectado como %s (%s).", self.user, self.user.id)
        await self._restore_absence_checks()
        await self._refresh_control_panels()

    async def _refresh_control_panels(self) -> None:
        """Refresh existing panel messages so newly deployed buttons become visible."""
        for guild in self.guilds:
            settings = await self.db.get_settings(guild.id)
            if not settings.control_channel_id or not settings.panel_message_id:
                continue

            channel = guild.get_channel(settings.control_channel_id)
            if not isinstance(channel, discord.TextChannel):
                continue

            try:
                message = await channel.fetch_message(settings.panel_message_id)
                embed = await self.build_control_panel_embed(guild)
                await message.edit(embed=embed, view=TimeClockView(self))
            except discord.NotFound:
                LOGGER.warning("Painel configurado não existe mais no servidor %s.", guild.id)
            except discord.Forbidden:
                LOGGER.warning("Sem permissão para atualizar o painel no servidor %s.", guild.id)
            except discord.HTTPException:
                LOGGER.exception("Falha ao atualizar o painel no servidor %s.", guild.id)

    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot or message.pinned:
            return

        settings = await self.db.get_settings_for_control_channel(
            message.guild.id,
            message.channel.id,
        )
        if settings is None:
            return
        if message.id == settings.panel_message_id:
            return
        if settings.control_message_ttl_seconds <= 0:
            return

        self.log_interaction(
            "control_message",
            "cleanup_scheduled",
            guild_id=message.guild.id,
            user_id=message.author.id,
            channel_id=message.channel.id,
            message_id=message.id,
            delay_seconds=settings.control_message_ttl_seconds,
        )
        self._schedule_control_message_delete(
            message,
            settings.control_message_ttl_seconds,
        )

    def _schedule_control_message_delete(self, message: discord.Message, delay: int) -> None:
        task = asyncio.create_task(self._delete_control_message_after(message, delay))
        self.cleanup_tasks.add(task)
        task.add_done_callback(self.cleanup_tasks.discard)

    def _start_pause_expiration_job(self) -> None:
        if self.pause_expiration_task and not self.pause_expiration_task.done():
            return
        self.pause_expiration_task = asyncio.create_task(self._pause_expiration_loop())

    async def _pause_expiration_loop(self) -> None:
        while True:
            try:
                cutoff = utcnow() - timedelta(hours=24)
                entries = await self.db.finish_expired_paused_entries(cutoff)
                for entry in entries:
                    self.log_interaction(
                        "pause_timeout",
                        "auto_closed",
                        guild_id=entry.guild_id,
                        user_id=entry.user_id,
                        entry_id=entry.id,
                    )
                    guild = self.get_guild(entry.guild_id)
                    member = guild.get_member(entry.user_id) if guild else None
                    if guild:
                        await self.delete_point_status_message(guild, entry.id)
                    if member:
                        try:
                            await self.notify_member(
                                member,
                                "Ponto encerrado automaticamente",
                                "Seu ponto foi encerrado após 24 horas em pausa.",
                                color=discord.Color.orange(),
                                fields=[
                                    ("Status", "Encerrado", True),
                                    ("Fim", format_dt(entry.ended_at), True),
                                    (
                                        "Tempo trabalhado",
                                        format_seconds(worked_seconds(entry)),
                                        True,
                                    ),
                                ],
                            )
                        except discord.HTTPException:
                            LOGGER.warning(
                                "Não foi possível notificar o usuário %s sobre o ponto %s.",
                                entry.user_id,
                                entry.id,
                            )
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("Falha no job de encerramento de pausas expiradas.")
            await asyncio.sleep(60)

    async def _delete_control_message_after(self, message: discord.Message, delay: int) -> None:
        await asyncio.sleep(delay)
        try:
            await message.delete()
        except discord.NotFound:
            return
        except discord.Forbidden:
            LOGGER.warning("Sem permissão para apagar mensagens no canal %s.", message.channel.id)
        except discord.HTTPException:
            LOGGER.exception(
                "Falha ao apagar mensagem %s no canal %s.",
                message.id,
                message.channel.id,
            )

    async def _delete_point_status_message_after(
        self,
        message: discord.Message,
        entry_id: int,
        delay: int,
    ) -> None:
        await asyncio.sleep(delay)
        try:
            await message.delete()
        except discord.NotFound:
            pass
        except discord.Forbidden:
            LOGGER.warning("Sem permissão para apagar status no canal %s.", message.channel.id)
            return
        except discord.HTTPException:
            LOGGER.exception(
                "Falha ao apagar status %s no canal %s.",
                message.id,
                message.channel.id,
            )
            return

        await self.db.set_status_message(entry_id, None)

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.bot or not member.guild:
            return

        allowed_channels = await self.db.list_allowed_voice_channels(member.guild.id)
        before_allowed = before.channel is not None and before.channel.id in allowed_channels
        after_allowed = after.channel is not None and after.channel.id in allowed_channels

        entry = await self.db.get_open_entry(member.guild.id, member.id)
        if not entry:
            if after_allowed:
                await self._notify_return_after_auto_closure(member, after)
            return

        if before_allowed and not after_allowed and entry.status == "active":
            settings = await self.db.get_settings(member.guild.id)
            started_at = utcnow()
            deadline_at = started_at + timedelta(seconds=settings.absence_tolerance_seconds)
            updated = await self.db.set_absence_deadline(entry, started_at, deadline_at)
            self.log_interaction(
                "voice_absence",
                "started",
                guild_id=member.guild.id,
                user_id=member.id,
                channel_id=before.channel.id if before.channel else None,
                entry_id=updated.id,
                deadline_at=deadline_at.isoformat(),
            )
            self._schedule_absence_check(member.guild.id, member.id, updated.id, deadline_at)
            await self.delete_point_status_message(member.guild, updated.id)
            await self.notify_member(
                member,
                "Você saiu da chamada permitida",
                "Seu ponto continua ativo durante a tolerância. "
                f"Volte em até {format_duration_words(settings.absence_tolerance_seconds)} "
                f"para evitar encerramento automático.\n\nLimite: {format_dt(deadline_at)}",
                color=discord.Color.orange(),
                fields=[
                    ("Status", "Em tolerância", True),
                    (
                        "Canal anterior",
                        before.channel.mention if before.channel else "desconhecido",
                        True,
                    ),
                ],
            )
            await self.audit(
                member.guild,
                "Ausência detectada",
                f"{member.mention} saiu de um canal de voz permitido.",
                color=discord.Color.orange(),
                fields=[
                    (
                        "Canal anterior",
                        before.channel.mention if before.channel else "desconhecido",
                        True,
                    ),
                    ("Limite de retorno", format_dt(deadline_at), True),
                ],
                user=member,
            )
            return

        if after_allowed:
            if entry.absence_deadline_at:
                await self.db.clear_absence(entry)
                self._cancel_absence_check(entry.id)
                self.log_interaction(
                    "voice_absence",
                    "cleared",
                    guild_id=member.guild.id,
                    user_id=member.id,
                    channel_id=after.channel.id if after.channel else None,
                    entry_id=entry.id,
                )
                await self.delete_point_status_message(member.guild, entry.id)
                await self.notify_member(
                    member,
                    "Ponto ativo",
                    "Você voltou antes do fim da tolerância. Seu ponto continua ativo.",
                    color=discord.Color.green(),
                    fields=[
                        ("Status", "Ativo", True),
                        (
                            "Canal atual",
                            after.channel.mention if after.channel else "desconhecido",
                            True,
                        ),
                    ],
                )
                await self.audit(
                    member.guild,
                    "Ausência encerrada",
                    f"{member.mention} voltou antes do fim da tolerância.",
                    color=discord.Color.green(),
                    fields=[
                        (
                            "Canal atual",
                            after.channel.mention if after.channel else "desconhecido",
                            True,
                        ),
                    ],
                    user=member,
                )
                return

            if entry.status == "paused" and entry.pause_reason == "auto_absence":
                finished = await self.db.finish_entry(entry, close_reason="auto_absence")
                self.log_interaction(
                    "voice_absence",
                    "closed_on_return",
                    guild_id=member.guild.id,
                    user_id=member.id,
                    channel_id=after.channel.id if after.channel else None,
                    entry_id=finished.id,
                )
                await self.delete_point_status_message(member.guild, entry.id)
                await self.notify_member(
                    member,
                    "Ponto encerrado automaticamente",
                    "Seu ponto já havia sido encerrado por ausência.",
                    color=discord.Color.red(),
                    fields=[
                        ("Status", "Encerrado", True),
                        ("Fim", format_dt(finished.ended_at), True),
                    ],
                )
                await self.audit(
                    member.guild,
                    "Ponto encerrado por ausência",
                    f"{member.mention} voltou, mas o ponto já havia excedido a tolerância.",
                    color=discord.Color.red(),
                    fields=[
                        (
                            "Canal atual",
                            after.channel.mention if after.channel else "desconhecido",
                            True,
                        ),
                        ("Fim", format_dt(finished.ended_at), True),
                        ("Tempo trabalhado", format_seconds(worked_seconds(finished)), True),
                    ],
                    user=member,
                )

    async def _notify_return_after_auto_closure(
        self,
        member: discord.Member,
        after: discord.VoiceState,
    ) -> None:
        entry = await self.db.claim_unnotified_auto_absence_closure(member.guild.id, member.id)
        if entry is None:
            return

        self.log_interaction(
            "voice_absence",
            "return_after_auto_closure",
            guild_id=member.guild.id,
            user_id=member.id,
            channel_id=after.channel.id if after.channel else None,
            entry_id=entry.id,
        )
        await self.delete_point_status_message(member.guild, entry.id)
        await self.notify_member(
            member,
            "Ponto já encerrado",
            "Seu ponto foi encerrado automaticamente por ausência. "
            "Para continuar, inicie um novo ponto.",
            color=discord.Color.red(),
            fields=[
                ("Status", "Encerrado", True),
                ("Fim", format_dt(entry.ended_at), True),
                ("Tempo trabalhado", format_seconds(worked_seconds(entry)), True),
            ],
        )
        await self.audit(
            member.guild,
            "Usuário voltou após encerramento",
            f"{member.mention} voltou para um canal permitido após o encerramento automático.",
            color=discord.Color.red(),
            fields=[
                ("Canal atual", after.channel.mention if after.channel else "desconhecido", True),
                ("Fim do ponto", format_dt(entry.ended_at), True),
                ("Tempo trabalhado", format_seconds(worked_seconds(entry)), True),
            ],
            user=member,
        )

    async def audit(
        self,
        guild: discord.Guild,
        title: str,
        description: str,
        *,
        color: discord.Color | None = None,
        fields: list[tuple[str, str, bool]] | None = None,
        user: discord.abc.User | None = None,
    ) -> None:
        settings = await self.db.get_settings(guild.id)
        if not settings.audit_channel_id:
            return

        channel = guild.get_channel(settings.audit_channel_id)
        if isinstance(channel, discord.TextChannel):
            embed = discord.Embed(
                title=title,
                description=description,
                color=color or discord.Color.blurple(),
                timestamp=utcnow(),
            )
            if user:
                embed.set_author(name=str(user), icon_url=user.display_avatar.url)
            for name, value, inline in fields or []:
                embed.add_field(name=name, value=value, inline=inline)
            embed.set_footer(text="Auditoria de bate-ponto")

            try:
                await channel.send(embed=embed)
            except discord.Forbidden:
                LOGGER.warning("Sem permissão para enviar logs no canal %s.", channel.id)
            except discord.HTTPException:
                LOGGER.exception("Falha ao enviar log de auditoria no canal %s.", channel.id)

    async def notify_member(
        self,
        member: discord.Member,
        title: str,
        description: str,
        *,
        color: discord.Color,
        fields: list[tuple[str, str, bool]] | None = None,
    ) -> None:
        embed = discord.Embed(
            title=title,
            description=description,
            color=color,
        )
        for name, value, inline in fields or []:
            embed.add_field(name=name, value=value, inline=inline)
        embed.set_footer(text=f"Bate-ponto - {member.guild.name}")

        try:
            await member.send(embed=embed)
        except discord.Forbidden:
            LOGGER.warning("Não foi possível enviar DM para o usuário %s.", member.id)
        except discord.HTTPException:
            LOGGER.exception("Falha ao enviar aviso privado para o usuário %s.", member.id)

    async def notify_control_channel(
        self,
        guild: discord.Guild,
        title: str,
        message: str,
        *,
        color: discord.Color | None = None,
    ) -> None:
        settings = await self.db.get_settings(guild.id)
        if not settings.control_channel_id:
            return

        channel = guild.get_channel(settings.control_channel_id)
        if not isinstance(channel, discord.TextChannel):
            return

        embed = discord.Embed(
            title=title,
            description=message,
            color=color or discord.Color.blurple(),
            timestamp=utcnow(),
        )
        embed.set_footer(text="Bate-ponto")
        try:
            sent = await channel.send(embed=embed)
        except discord.Forbidden:
            LOGGER.warning("Sem permissão para avisar no canal do painel %s.", channel.id)
        except discord.HTTPException:
            LOGGER.exception("Falha ao avisar no canal do painel %s.", channel.id)
            return

        if settings.control_message_ttl_seconds > 0:
            self._schedule_control_message_delete(
                sent,
                settings.control_message_ttl_seconds,
            )

    async def send_control_panel(self, channel: discord.TextChannel) -> discord.Message:
        embed = await self.build_control_panel_embed(channel.guild)
        return await channel.send(embed=embed, view=TimeClockView(self))

    async def build_control_panel_embed(self, guild: discord.Guild) -> discord.Embed:
        return discord.Embed(
            title="Bate-ponto",
            description="Use os botões abaixo para controlar seu ponto.",
            color=discord.Color.blurple(),
        )

    async def upsert_point_status_message(
        self,
        guild: discord.Guild,
        entry_id: int,
        title: str,
        description: str,
        *,
        color: discord.Color,
        fields: list[tuple[str, str, bool]] | None = None,
        delete_after: bool = False,
    ) -> None:
        settings = await self.db.get_settings(guild.id)
        if not settings.control_channel_id:
            return

        channel = guild.get_channel(settings.control_channel_id)
        if not isinstance(channel, discord.TextChannel):
            return

        embed = discord.Embed(
            title=title,
            description=description,
            color=color,
        )
        for name, value, inline in fields or []:
            embed.add_field(name=name, value=value, inline=inline)
        embed.set_footer(text="Bate-ponto")

        entry = await self.db.get_entry(entry_id)
        message = await self._get_point_status_message(channel, entry)
        try:
            if message:
                await message.edit(embed=embed)
            else:
                message = await channel.send(embed=embed)
                await self.db.set_status_message(entry_id, message.id)
        except discord.Forbidden:
            LOGGER.warning("Sem permissão para atualizar status no canal do painel %s.", channel.id)
            return
        except discord.HTTPException:
            LOGGER.exception("Falha ao atualizar status no canal do painel %s.", channel.id)
            return

        if delete_after and settings.control_message_ttl_seconds > 0:
            task = asyncio.create_task(
                self._delete_point_status_message_after(
                    message,
                    entry_id,
                    settings.control_message_ttl_seconds,
                )
            )
            self.cleanup_tasks.add(task)
            task.add_done_callback(self.cleanup_tasks.discard)

    async def delete_point_status_message(self, guild: discord.Guild, entry_id: int) -> None:
        settings = await self.db.get_settings(guild.id)
        if not settings.control_channel_id:
            return

        channel = guild.get_channel(settings.control_channel_id)
        if not isinstance(channel, discord.TextChannel):
            return

        entry = await self.db.get_entry(entry_id)
        message = await self._get_point_status_message(channel, entry)
        if message is None:
            await self.db.set_status_message(entry_id, None)
            return

        try:
            await message.delete()
        except discord.NotFound:
            pass
        except discord.Forbidden:
            LOGGER.warning("Sem permissão para apagar status no canal do painel %s.", channel.id)
            return
        except discord.HTTPException:
            LOGGER.exception("Falha ao apagar status no canal do painel %s.", channel.id)
            return

        await self.db.set_status_message(entry_id, None)

    async def _get_point_status_message(
        self,
        channel: discord.TextChannel,
        entry: TimeEntry | None,
    ) -> discord.Message | None:
        message_id = entry.status_message_id if entry else None
        if not message_id:
            return None
        try:
            return await channel.fetch_message(message_id)
        except discord.NotFound:
            return None
        except discord.Forbidden:
            LOGGER.warning("Sem permissão para buscar status no canal do painel %s.", channel.id)
            return None
        except discord.HTTPException:
            LOGGER.exception("Falha ao buscar status no canal do painel %s.", channel.id)
            return None

    async def respond_control_interaction(
        self,
        interaction: discord.Interaction,
        content: str,
    ) -> None:
        delete_after: int | None = None
        if interaction.guild:
            settings = await self.db.get_settings(interaction.guild.id)
            if settings.control_message_ttl_seconds > 0:
                delete_after = settings.control_message_ttl_seconds

        await interaction.response.send_message(
            content,
            ephemeral=True,
            delete_after=delete_after,
        )

    async def respond_control_embed(
        self,
        interaction: discord.Interaction,
        title: str,
        description: str,
        *,
        color: discord.Color,
        fields: list[tuple[str, str, bool]] | None = None,
    ) -> None:
        delete_after: int | None = None
        if interaction.guild:
            settings = await self.db.get_settings(interaction.guild.id)
            if settings.control_message_ttl_seconds > 0:
                delete_after = settings.control_message_ttl_seconds

        embed = discord.Embed(
            title=title,
            description=description,
            color=color,
        )
        for name, value, inline in fields or []:
            embed.add_field(name=name, value=value, inline=inline)
        embed.set_footer(text="Bate-ponto")

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
            delete_after=delete_after,
        )

    def _schedule_absence_check(
        self, guild_id: int, user_id: int, entry_id: int, deadline_at
    ) -> None:
        self._cancel_absence_check(entry_id)
        task = asyncio.create_task(self._absence_check(guild_id, user_id, entry_id, deadline_at))
        self.absence_tasks[entry_id] = task

    def _cancel_absence_check(self, entry_id: int) -> None:
        task = self.absence_tasks.pop(entry_id, None)
        if task:
            task.cancel()

    async def _absence_check(self, guild_id: int, user_id: int, entry_id: int, deadline_at) -> None:
        try:
            delay = max(0, (deadline_at - utcnow()).total_seconds())
            await asyncio.sleep(delay)

            guild = self.get_guild(guild_id)
            member = guild.get_member(user_id) if guild else None
            if guild is None or member is None:
                return

            entry = await self.db.get_open_entry(guild_id, user_id)
            if not entry or entry.id != entry_id or entry.status != "active":
                return
            if not entry.absence_deadline_at or entry.absence_deadline_at != deadline_at:
                return

            allowed_channels = await self.db.list_allowed_voice_channels(guild_id)
            current_channel = member.voice.channel if member.voice else None
            if current_channel and current_channel.id in allowed_channels:
                await self.db.clear_absence(entry)
                return

            finished = await self.db.finish_entry(
                entry,
                ended_at=deadline_at,
                close_reason="auto_absence",
            )
            self.log_interaction(
                "voice_absence",
                "auto_closed",
                guild_id=guild_id,
                user_id=user_id,
                entry_id=finished.id,
                deadline_at=deadline_at.isoformat(),
            )
            await self.delete_point_status_message(guild, entry.id)
            await self.notify_member(
                member,
                "Ponto encerrado automaticamente",
                "Seu ponto foi encerrado porque você excedeu a tolerância de ausência.",
                color=discord.Color.red(),
                fields=[
                    ("Status", "Encerrado", True),
                    ("Fim", format_dt(finished.ended_at), True),
                    ("Tempo trabalhado", format_seconds(worked_seconds(finished)), True),
                ],
            )
            await self.audit(
                guild,
                "Ponto encerrado por ausência",
                f"{member.mention} excedeu a tolerância configurada.",
                color=discord.Color.red(),
                fields=[
                    (
                        "Ausente desde",
                        format_dt(entry.absence_started_at or deadline_at),
                        True,
                    ),
                    ("Fim", format_dt(finished.ended_at), True),
                    ("Tempo trabalhado", format_seconds(worked_seconds(finished)), True),
                ],
                user=member,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Falha ao verificar ausência do ponto %s.", entry_id)
        finally:
            self.absence_tasks.pop(entry_id, None)

    async def _restore_absence_checks(self) -> None:
        pending_absences = await self.db.list_pending_absences()
        for entry in pending_absences:
            if entry.absence_deadline_at:
                self.log_interaction(
                    "voice_absence",
                    "restored_check",
                    guild_id=entry.guild_id,
                    user_id=entry.user_id,
                    entry_id=entry.id,
                    deadline_at=entry.absence_deadline_at.isoformat(),
                )
                self._schedule_absence_check(
                    entry.guild_id,
                    entry.user_id,
                    entry.id,
                    entry.absence_deadline_at,
                )

    def log_interaction(
        self,
        action: str,
        outcome: str,
        *,
        interaction: discord.Interaction | None = None,
        guild_id: int | None = None,
        user_id: int | None = None,
        channel_id: int | None = None,
        entry_id: int | None = None,
        **extra: object,
    ) -> None:
        if not INTERACTION_LOGGER.isEnabledFor(logging.INFO):
            return

        if interaction is not None:
            guild_id = guild_id or interaction.guild_id
            user_id = user_id or interaction.user.id
            channel_id = channel_id or interaction.channel_id

        values: dict[str, object] = {
            "action": action,
            "outcome": outcome,
            "guild_id": guild_id,
            "user_id": user_id,
            "channel_id": channel_id,
            "entry_id": entry_id,
            **extra,
        }
        message = " ".join(f"{key}={value}" for key, value in values.items() if value is not None)
        INTERACTION_LOGGER.info(message)


def main() -> None:
    settings = Settings()
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    bot = TimeClockBot(Database(settings.resolved_database_url), dev_guild_id=settings.dev_guild_id)
    bot.run(settings.discord_token)


if __name__ == "__main__":
    main()
