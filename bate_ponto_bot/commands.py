from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands

from bate_ponto_bot.utils import (
    canonical_timezone_name,
    channel_mention,
    format_dt,
    load_timezone,
    timezone_names,
    utcnow,
)

if TYPE_CHECKING:
    from bate_ponto_bot.bot import TimeClockBot


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


# Shown before the user types anything; Brazil first since most servers are there.
SUGGESTED_TIMEZONES = (
    "America/Sao_Paulo",
    "America/Fortaleza",
    "America/Recife",
    "America/Bahia",
    "America/Belem",
    "America/Manaus",
    "America/Cuiaba",
    "America/Porto_Velho",
    "America/Rio_Branco",
    "America/Noronha",
    "Europe/Lisbon",
    "UTC",
)


def timezone_choices(current: str) -> list[app_commands.Choice[str]]:
    query = current.strip().replace(" ", "_").casefold()
    names = (
        [name for name in timezone_names() if query in name.casefold()]
        if query
        else list(SUGGESTED_TIMEZONES)
    )
    return [app_commands.Choice(name=name, value=name) for name in names[:25]]


def is_text_app_channel(channel: app_commands.AppCommandChannel) -> bool:
    return channel.type in {discord.ChannelType.text, discord.ChannelType.news}


def is_voice_app_channel(channel: app_commands.AppCommandChannel) -> bool:
    return channel.type in {discord.ChannelType.voice, discord.ChannelType.stage_voice}


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

    @app_commands.command(name="fuso", description="Define o fuso horário do servidor.")
    @app_commands.describe(fuso="Fuso horário IANA, por exemplo America/Sao_Paulo.")
    async def fuso(self, interaction: discord.Interaction, fuso: str) -> None:
        guild = require_guild(interaction)
        if guild is None:
            return

        timezone_name = canonical_timezone_name(fuso)
        if timezone_name is None:
            self.bot.log_interaction(
                "config_fuso",
                "denied_unknown_timezone",
                interaction=interaction,
                timezone=fuso[:64],
            )
            await interaction.response.send_message(
                "Fuso horário desconhecido. Escolha uma das opções sugeridas, "
                "por exemplo `America/Sao_Paulo`.",
                ephemeral=True,
            )
            return

        await self.bot.db.set_timezone(guild.id, timezone_name)
        self.bot.set_guild_timezone(guild.id, timezone_name)
        self.bot.log_interaction(
            "config_fuso",
            "success",
            interaction=interaction,
            timezone=timezone_name,
        )
        local_now = format_dt(utcnow(), load_timezone(timezone_name))
        await interaction.response.send_message(
            f"Fuso horário definido para `{timezone_name}` (agora: {local_now}).",
            ephemeral=True,
        )

    @fuso.autocomplete("fuso")
    async def fuso_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return timezone_choices(current)

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
            f"Fuso horário: `{settings.timezone}`\n"
            f"Voz permitida: {', '.join(allowed_mentions) if allowed_mentions else 'nenhum canal'}",
            ephemeral=True,
        )
