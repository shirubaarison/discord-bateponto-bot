from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands

from bate_ponto_bot.config import Settings
from bate_ponto_bot.db import Database, TimeEntry, utcnow, worked_seconds

LOGGER = logging.getLogger(__name__)
INTERACTION_LOGGER = logging.getLogger("bate_ponto_bot.interactions")
LOCAL_TIMEZONE = ZoneInfo("America/Fortaleza")


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

    async def setup_hook(self) -> None:
        await self.db.connect()
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
        await self.db.close()
        await super().close()

    async def on_ready(self) -> None:
        assert self.user is not None
        LOGGER.info("Bot conectado como %s (%s).", self.user, self.user.id)
        await self._restore_absence_checks()

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
                ("Início", format_dt(entry.started_at), True),
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
                ("Início", format_dt(entry.started_at), True),
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
                ("Início", format_dt(finished.started_at), True),
                ("Fim", format_dt(finished.ended_at), True),
                ("Tempo trabalhado", format_seconds(worked_seconds(finished)), True),
                ("Tempo pausado", format_seconds(finished.pause_total_seconds), True),
            ],
        )
        await self.bot.audit(
            member.guild,
            "Ponto finalizado",
            f"{member.mention} finalizou o ponto.",
            color=discord.Color.red(),
            fields=[
                ("Início", format_dt(finished.started_at), True),
                ("Fim", format_dt(finished.ended_at), True),
                ("Tempo trabalhado", format_seconds(worked_seconds(finished)), True),
                ("Tempo pausado", format_seconds(finished.pause_total_seconds), True),
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
                    ("Pausado em", format_dt(paused.pause_started_at), True),
                    ("Tempo trabalhado", format_seconds(worked_seconds(paused)), True),
                ],
            )
            await self.bot.audit(
                member.guild,
                "Ponto pausado",
                f"{member.mention} pausou o ponto manualmente.",
                color=discord.Color.orange(),
                fields=[
                    ("Pausado em", format_dt(paused.pause_started_at), True),
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
                ("Retomado em", format_dt(utcnow()), True),
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
                ("Retomado em", format_dt(utcnow()), True),
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
            ("Início", format_dt(entry.started_at), True),
            ("Tempo trabalhado", format_seconds(worked_seconds(entry)), True),
        ]
        if entry.status == "paused":
            fields.append(("Pausado em", format_dt(entry.pause_started_at), True))

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
                format_dt(entry.started_at),
                f"Fim: {format_dt(entry.ended_at)}\n"
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


class ConfigGroup(app_commands.Group):
    def __init__(self, bot: TimeClockBot) -> None:
        super().__init__(
            name="config",
            description="Configura o bot de bate-ponto.",
            default_permissions=discord.Permissions(manage_guild=True),
        )
        self.bot = bot

    @app_commands.command(name="painel", description="Define o canal do painel de ponto.")
    @app_commands.describe(canal="Canal onde o painel será publicado.")
    async def painel(
        self,
        interaction: discord.Interaction,
        canal: app_commands.AppCommandChannel,
    ) -> None:
        guild = require_guild(interaction)
        if guild is None:
            return

        if not is_text_app_channel(canal):
            self.bot.log_interaction(
                "config_painel",
                "denied_invalid_channel_type",
                interaction=interaction,
                channel_id=canal.id,
            )
            await interaction.response.send_message(
                "Selecione um canal de texto para publicar o painel.",
                ephemeral=True,
            )
            return

        channel = await resolve_app_channel(canal)
        if not isinstance(channel, discord.TextChannel):
            self.bot.log_interaction(
                "config_painel",
                "denied_channel_inaccessible",
                interaction=interaction,
                channel_id=canal.id,
            )
            await interaction.response.send_message(
                "Não consigo acessar esse canal. "
                "Dê ao bot permissão de ver e enviar mensagens nele.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        message = await self.bot.send_control_panel(channel)
        await self.bot.db.set_control_channel(guild.id, channel.id, message.id)
        self.bot.log_interaction(
            "config_painel",
            "success",
            interaction=interaction,
            channel_id=channel.id,
            message_id=message.id,
        )
        await interaction.followup.send(f"Painel publicado em {channel.mention}.", ephemeral=True)

    @app_commands.command(name="logs", description="Define o canal de auditoria.")
    @app_commands.describe(canal="Canal onde os logs serão enviados.")
    async def logs(
        self,
        interaction: discord.Interaction,
        canal: app_commands.AppCommandChannel,
    ) -> None:
        guild = require_guild(interaction)
        if guild is None:
            return

        if not is_text_app_channel(canal):
            self.bot.log_interaction(
                "config_logs",
                "denied_invalid_channel_type",
                interaction=interaction,
                channel_id=canal.id,
            )
            await interaction.response.send_message(
                "Selecione um canal de texto para os logs.",
                ephemeral=True,
            )
            return

        channel = await resolve_app_channel(canal)
        if not isinstance(channel, discord.TextChannel):
            self.bot.log_interaction(
                "config_logs",
                "denied_channel_inaccessible",
                interaction=interaction,
                channel_id=canal.id,
            )
            await interaction.response.send_message(
                "Não consigo acessar esse canal. "
                "Dê ao bot permissão de ver e enviar mensagens nele.",
                ephemeral=True,
            )
            return

        await self.bot.db.set_audit_channel(guild.id, channel.id)
        self.bot.log_interaction(
            "config_logs",
            "success",
            interaction=interaction,
            channel_id=channel.id,
        )
        await interaction.response.send_message(
            f"Logs configurados em {channel.mention}.",
            ephemeral=True,
        )

    @app_commands.command(
        name="voz-adicionar",
        description="Permite iniciar ponto neste canal de voz.",
    )
    @app_commands.describe(canal="Canal de voz permitido.")
    async def voz_adicionar(
        self,
        interaction: discord.Interaction,
        canal: app_commands.AppCommandChannel,
    ) -> None:
        guild = require_guild(interaction)
        if guild is None:
            return

        if not is_voice_app_channel(canal):
            self.bot.log_interaction(
                "config_voz_adicionar",
                "denied_invalid_channel_type",
                interaction=interaction,
                channel_id=canal.id,
            )
            await interaction.response.send_message(
                "Selecione um canal de voz para permitir o ponto.",
                ephemeral=True,
            )
            return

        channel = await resolve_app_channel(canal)
        if not isinstance(channel, discord.VoiceChannel | discord.StageChannel):
            self.bot.log_interaction(
                "config_voz_adicionar",
                "denied_channel_inaccessible",
                interaction=interaction,
                channel_id=canal.id,
            )
            await interaction.response.send_message(
                "Não consigo acessar esse canal de voz. Dê ao bot permissão de ver o canal.",
                ephemeral=True,
            )
            return

        await self.bot.db.add_allowed_voice_channel(guild.id, channel.id)
        self.bot.log_interaction(
            "config_voz_adicionar",
            "success",
            interaction=interaction,
            channel_id=channel.id,
        )
        await interaction.response.send_message(f"{channel.mention} permitido.", ephemeral=True)

    @app_commands.command(name="voz-remover", description="Remove um canal de voz permitido.")
    @app_commands.describe(canal="Canal de voz que será removido.")
    async def voz_remover(
        self,
        interaction: discord.Interaction,
        canal: app_commands.AppCommandChannel,
    ) -> None:
        guild = require_guild(interaction)
        if guild is None:
            return

        if not is_voice_app_channel(canal):
            self.bot.log_interaction(
                "config_voz_remover",
                "denied_invalid_channel_type",
                interaction=interaction,
                channel_id=canal.id,
            )
            await interaction.response.send_message(
                "Selecione um canal de voz para remover.",
                ephemeral=True,
            )
            return

        channel = await resolve_app_channel(canal)
        if not isinstance(channel, discord.VoiceChannel | discord.StageChannel):
            self.bot.log_interaction(
                "config_voz_remover",
                "denied_channel_inaccessible",
                interaction=interaction,
                channel_id=canal.id,
            )
            await interaction.response.send_message(
                "Não consigo acessar esse canal de voz. Dê ao bot permissão de ver o canal.",
                ephemeral=True,
            )
            return

        await self.bot.db.remove_allowed_voice_channel(guild.id, channel.id)
        self.bot.log_interaction(
            "config_voz_remover",
            "success",
            interaction=interaction,
            channel_id=channel.id,
        )
        await interaction.response.send_message(f"{channel.mention} removido.", ephemeral=True)

    @app_commands.command(name="tolerancia", description="Define a tolerância de ausência.")
    @app_commands.describe(minutos="Minutos de tolerância antes do encerramento automático.")
    async def tolerancia(
        self,
        interaction: discord.Interaction,
        minutos: app_commands.Range[int, 1, 120],
    ) -> None:
        guild = require_guild(interaction)
        if guild is None:
            return

        await self.bot.db.set_tolerance(guild.id, minutos * 60)
        self.bot.log_interaction(
            "config_tolerancia",
            "success",
            interaction=interaction,
            minutes=minutos,
        )
        await interaction.response.send_message(
            f"Tolerância configurada para {minutos} minuto(s).", ephemeral=True
        )

    @app_commands.command(
        name="limpeza",
        description="Define em quanto tempo mensagens comuns do canal do painel somem.",
    )
    @app_commands.describe(segundos="Segundos até apagar. Use 0 para desativar.")
    async def limpeza(
        self,
        interaction: discord.Interaction,
        segundos: app_commands.Range[int, 0, 86400],
    ) -> None:
        guild = require_guild(interaction)
        if guild is None:
            return

        await self.bot.db.set_control_message_ttl(guild.id, segundos)
        self.bot.log_interaction(
            "config_limpeza",
            "success",
            interaction=interaction,
            seconds=segundos,
        )
        if segundos == 0:
            await interaction.response.send_message(
                "Limpeza automática do canal do painel desativada.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"Mensagens comuns do canal do painel serão apagadas após {segundos} segundo(s).",
            ephemeral=True,
        )

    @app_commands.command(name="mostrar", description="Mostra a configuração atual.")
    async def mostrar(self, interaction: discord.Interaction) -> None:
        guild = require_guild(interaction)
        if guild is None:
            return

        settings = await self.bot.db.get_settings(guild.id)
        allowed = await self.bot.db.list_allowed_voice_channels(guild.id)
        self.bot.log_interaction(
            "config_mostrar",
            "success",
            interaction=interaction,
            allowed_voice_channels=len(allowed),
        )
        allowed_mentions = [
            channel.mention
            for channel_id in allowed
            if isinstance((channel := guild.get_channel(channel_id)), discord.VoiceChannel)
        ]
        control = channel_mention(guild, settings.control_channel_id)
        audit = channel_mention(guild, settings.audit_channel_id)
        cleanup = (
            f"{settings.control_message_ttl_seconds} segundo(s)"
            if settings.control_message_ttl_seconds > 0
            else "desativada"
        )
        await interaction.response.send_message(
            "Configuração atual:\n"
            f"Painel: {control}\n"
            f"Logs: {audit}\n"
            f"Tolerância: {settings.absence_tolerance_seconds // 60} minuto(s)\n"
            f"Limpeza do painel: {cleanup}\n"
            f"Voz permitida: {', '.join(allowed_mentions) if allowed_mentions else 'nenhum canal'}",
            ephemeral=True,
        )


async def require_member(interaction: discord.Interaction) -> discord.Member | None:
    if not isinstance(interaction.user, discord.Member) or interaction.guild is None:
        await interaction.response.send_message(
            "Esse comando só funciona em um servidor.", ephemeral=True
        )
        return None
    return interaction.user


def require_guild(interaction: discord.Interaction) -> discord.Guild | None:
    return interaction.guild


async def resolve_app_channel(
    channel: app_commands.AppCommandChannel,
) -> discord.abc.GuildChannel | None:
    resolved = channel.resolve()
    if resolved is not None:
        return resolved
    try:
        return await channel.fetch()
    except (discord.Forbidden, discord.NotFound):
        return None


def is_text_app_channel(channel: app_commands.AppCommandChannel) -> bool:
    return channel.type in {discord.ChannelType.text, discord.ChannelType.news}


def is_voice_app_channel(channel: app_commands.AppCommandChannel) -> bool:
    return channel.type in {discord.ChannelType.voice, discord.ChannelType.stage_voice}


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


def channel_mention(guild: discord.Guild, channel_id: int | None) -> str:
    if not channel_id:
        return "não configurado"
    channel = guild.get_channel(channel_id)
    return channel.mention if channel else f"`{channel_id}`"


def format_seconds(total_seconds: int) -> str:
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def format_dt(value) -> str:
    if value is None:
        return "desconhecido"
    local_value = value.astimezone(LOCAL_TIMEZONE)
    month_names = [
        "janeiro",
        "fevereiro",
        "março",
        "abril",
        "maio",
        "junho",
        "julho",
        "agosto",
        "setembro",
        "outubro",
        "novembro",
        "dezembro",
    ]
    month = month_names[local_value.month - 1]
    return (
        f"{local_value.day} de {month} de {local_value.year} "
        f"às {local_value.hour:02d}:{local_value.minute:02d}"
    )


def format_duration_words(total_seconds: int) -> str:
    minutes, seconds = divmod(total_seconds, 60)
    parts: list[str] = []
    if minutes:
        suffix = "s" if minutes != 1 else ""
        parts.append(f"{minutes} minuto{suffix}")
    if seconds:
        suffix = "s" if seconds != 1 else ""
        parts.append(f"{seconds} segundo{suffix}")
    return " e ".join(parts) if parts else "0 segundos"


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
