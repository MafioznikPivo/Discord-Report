from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import disnake
from disnake.errors import HTTPException, InteractionTimedOut, NotFound
from disnake.ext import commands

from src.config import AppConfig
from src.services.help_service import HelpService
from src.ui.views import HelpEntryView, HelpModerationView
from src.utils.permissions import has_moderator_role


class HelpCreateModal(disnake.ui.Modal):
    def __init__(self, cog: "HelpCog") -> None:
        self.cog = cog
        components = [
            disnake.ui.TextInput(
                label="Текст вопроса",
                custom_id="question_text",
                style=disnake.TextInputStyle.paragraph,
                placeholder="Опишите ваш вопрос для модераторов",
                min_length=5,
                max_length=1000,
            )
        ]
        super().__init__(
            title="Новый вопрос",
            custom_id=f"help:create:modal:{uuid4()}",
            components=components,
        )

    async def callback(self, interaction: disnake.ModalInteraction) -> None:
        await self.cog.handle_help_create_modal(interaction)


class HelpReplyModal(disnake.ui.Modal):
    def __init__(self, cog: "HelpCog", ticket_id: int) -> None:
        self.cog = cog
        self.ticket_id = ticket_id
        components = [
            disnake.ui.TextInput(
                label="Ответ модератора",
                custom_id="reply_text",
                style=disnake.TextInputStyle.paragraph,
                min_length=1,
                max_length=1500,
            )
        ]
        super().__init__(
            title=f"Ответ по вопросу #{ticket_id}",
            custom_id=f"help:reply:modal:{ticket_id}:{uuid4()}",
            components=components,
        )

    async def callback(self, interaction: disnake.ModalInteraction) -> None:
        reply_text = interaction.text_values["reply_text"].strip()
        await self.cog.handle_help_reply_modal(interaction, self.ticket_id, reply_text)


class HelpCloseModal(disnake.ui.Modal):
    def __init__(self, cog: "HelpCog", ticket_id: int) -> None:
        self.cog = cog
        self.ticket_id = ticket_id
        components = [
            disnake.ui.TextInput(
                label="Причина закрытия",
                custom_id="close_reason",
                style=disnake.TextInputStyle.paragraph,
                min_length=3,
                max_length=1000,
            )
        ]
        super().__init__(
            title=f"Закрытие вопроса #{ticket_id}",
            custom_id=f"help:close:modal:{ticket_id}:{uuid4()}",
            components=components,
        )

    async def callback(self, interaction: disnake.ModalInteraction) -> None:
        reason = interaction.text_values["close_reason"].strip()
        await self.cog.handle_help_close_modal(interaction, self.ticket_id, reason)


class HelpCog(commands.Cog):
    def __init__(self, bot: commands.InteractionBot, config: AppConfig, service: HelpService) -> None:
        self.bot = bot
        self.config = config
        self.service = service

    def build_entry_view(self) -> HelpEntryView:
        return HelpEntryView(self.handle_help_create_button)

    def build_moderation_view(self, ticket_id: int) -> HelpModerationView:
        return HelpModerationView(ticket_id, self.handle_help_reply_button, self.handle_help_close_button)

    @commands.slash_command(
        name="\u043f\u043e\u043c\u043e\u0449\u044c",
        description="Задать вопрос модераторам",
    )
    async def help_slash(self, interaction: disnake.ApplicationCommandInteraction) -> None:
        if not await self._validate_command_context(interaction):
            return

        embed = disnake.Embed(
            title="Система вопросов",
            description="Нажмите кнопку ниже, чтобы задать вопрос модераторам.",
            color=disnake.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
        )
        await interaction.response.send_message(
            embed=embed,
            view=self.build_entry_view(),
            ephemeral=interaction.guild is not None,
        )

    async def handle_help_create_button(self, interaction: disnake.MessageInteraction) -> None:
        if not await self._validate_command_context(interaction):
            return
        await interaction.response.send_modal(HelpCreateModal(self))

    async def handle_help_create_modal(self, interaction: disnake.ModalInteraction) -> None:
        await self._defer_if_needed(interaction, ephemeral=True)

        intake_channel = await self._resolve_text_channel(self.config.help_intake_channel_id)
        if intake_channel is None:
            await self._respond(
                interaction,
                "Канал для вопросов не найден. Обратитесь к администрации.",
                ephemeral=True,
            )
            return

        guild = await self.service.get_target_guild()
        if guild is None:
            await self._respond(interaction, "Целевой сервер недоступен.", ephemeral=True)
            return

        member = await self.service.fetch_target_member(interaction.author.id)
        if member is None:
            await self._respond(
                interaction,
                "Вы должны состоять на целевом сервере, чтобы использовать /помощь.",
                ephemeral=True,
            )
            return

        if await self.service.has_open_ticket(interaction.author.id):
            await self._respond(
                interaction,
                "У вас уже есть открытый вопрос. Дождитесь ответа модератора.",
                ephemeral=True,
            )
            return

        question_text = interaction.text_values["question_text"].strip()

        ticket = await self.service.create_open_ticket(
            guild_id=self.config.target_guild_id,
            user_id=interaction.author.id,
            question_text=question_text,
        )

        embed = self._build_ticket_embed(ticket, member)
        view = self.build_moderation_view(ticket["id"])

        try:
            intake_message = await intake_channel.send(embed=embed, view=view)
        except disnake.HTTPException:
            await self._respond(
                interaction,
                "Не удалось отправить вопрос модераторам. Попробуйте позже.",
                ephemeral=True,
            )
            return

        await self.service.set_intake_message(ticket["id"], intake_message.id)
        await self.service.add_ticket_message(
            ticket["id"],
            direction="user_to_mod",
            author_id=interaction.author.id,
            content=question_text,
        )

        await self._respond(
            interaction,
            f"Ваш вопрос #{ticket['id']} отправлен модераторам.",
            ephemeral=True,
        )

    async def handle_help_reply_button(self, interaction: disnake.MessageInteraction) -> None:
        ticket_id = self._extract_entity_id(interaction.component.custom_id)
        if ticket_id is None:
            await self._respond(interaction, "Некорректный ID вопроса.", ephemeral=True)
            return

        if not self._is_moderator(interaction.author):
            await self._respond(interaction, "У вас нет прав для этого действия.", ephemeral=True)
            return

        ticket = await self.service.get_ticket(ticket_id)
        if ticket is None or ticket["status"] != "open":
            await self._respond(interaction, "Вопрос не найден или уже закрыт.", ephemeral=True)
            return

        await interaction.response.send_modal(HelpReplyModal(self, ticket_id))

    async def handle_help_reply_modal(
        self,
        interaction: disnake.ModalInteraction,
        ticket_id: int,
        reply_text: str,
    ) -> None:
        if not self._is_moderator(interaction.author):
            await self._respond(interaction, "У вас нет прав для этого действия.", ephemeral=True)
            return

        await self._defer_if_needed(interaction, ephemeral=True)

        ticket = await self.service.get_ticket(ticket_id)
        if ticket is None or ticket["status"] != "open":
            await self._respond(interaction, "Вопрос не найден или уже закрыт.", ephemeral=True)
            return

        await self.service.notify_ticket_reply(ticket, interaction.author, reply_text)
        await self.service.add_ticket_message(
            ticket_id,
            direction="mod_to_user",
            author_id=interaction.author.id,
            content=reply_text,
        )

        await self._respond(interaction, f"Ответ отправлен пользователю по вопросу #{ticket_id}.", ephemeral=True)

    async def handle_help_close_button(self, interaction: disnake.MessageInteraction) -> None:
        ticket_id = self._extract_entity_id(interaction.component.custom_id)
        if ticket_id is None:
            await self._respond(interaction, "Некорректный ID вопроса.", ephemeral=True)
            return

        if not self._is_moderator(interaction.author):
            await self._respond(interaction, "У вас нет прав для этого действия.", ephemeral=True)
            return

        ticket = await self.service.get_ticket(ticket_id)
        if ticket is None or ticket["status"] != "open":
            await self._respond(interaction, "Вопрос не найден или уже закрыт.", ephemeral=True)
            return

        await interaction.response.send_modal(HelpCloseModal(self, ticket_id))

    async def handle_help_close_modal(
        self,
        interaction: disnake.ModalInteraction,
        ticket_id: int,
        reason: str,
    ) -> None:
        if not self._is_moderator(interaction.author):
            await self._respond(interaction, "У вас нет прав для этого действия.", ephemeral=True)
            return

        await self._defer_if_needed(interaction, ephemeral=True)

        closed = await self.service.close_ticket(ticket_id, interaction.author.id, reason)
        if not closed:
            await self._respond(interaction, "Вопрос не найден или уже закрыт.", ephemeral=True)
            return

        ticket = await self.service.get_ticket(ticket_id)
        if ticket is not None:
            await self.service.mark_ticket_status(
                ticket,
                f"Статус: закрыт модератором {interaction.author.mention}. Причина: {reason}",
            )
            await self.service.notify_ticket_closed(ticket, interaction.author, reason)

        await self._respond(interaction, f"Вопрос #{ticket_id} закрыт.", ephemeral=True)

    @commands.Cog.listener("on_message")
    async def help_dm_router(self, message: disnake.Message) -> None:
        if message.author.bot:
            return
        if message.guild is not None:
            return

        ticket = await self.service.get_open_ticket_for_user(message.author.id)
        if ticket is None:
            return

        text = message.content.strip()
        attachments = [att.url for att in message.attachments]

        stored_text = text if text else "[без текста]"
        if attachments:
            stored_text = f"{stored_text}\n\nВложения:\n" + "\n".join(attachments)

        await self.service.add_ticket_message(
            ticket_id=ticket["id"],
            direction="user_to_mod",
            author_id=message.author.id,
            content=stored_text,
        )
        await self.service.forward_user_dm_to_intake(ticket, message.author, text, attachments)

    async def _validate_command_context(
        self,
        interaction: disnake.ApplicationCommandInteraction | disnake.MessageInteraction,
    ) -> bool:
        if interaction.guild is not None and interaction.guild.id != self.config.target_guild_id:
            await self._respond(
                interaction,
                "Эта команда доступна только на целевом сервере.",
                ephemeral=True,
            )
            return False
        return True

    def _is_moderator(self, actor: disnake.abc.User) -> bool:
        return has_moderator_role(actor, self.config.moderator_role_ids)

    async def _defer_if_needed(
        self,
        interaction: disnake.MessageInteraction | disnake.ModalInteraction,
        *,
        ephemeral: bool,
    ) -> None:
        if interaction.response.is_done():
            return

        use_ephemeral = ephemeral and interaction.guild is not None
        try:
            await interaction.response.defer(with_message=True, ephemeral=use_ephemeral)
        except TypeError:
            await interaction.response.defer(ephemeral=use_ephemeral)
        except (InteractionTimedOut, NotFound, HTTPException):
            return

    async def _resolve_text_channel(self, channel_id: int) -> disnake.TextChannel | None:
        channel = self.bot.get_channel(channel_id)
        if isinstance(channel, disnake.TextChannel):
            return channel

        try:
            fetched = await self.bot.fetch_channel(channel_id)
        except disnake.HTTPException:
            return None
        return fetched if isinstance(fetched, disnake.TextChannel) else None

    @staticmethod
    def _build_ticket_embed(ticket: dict, member: disnake.Member) -> disnake.Embed:
        embed = disnake.Embed(
            title=f"Новый вопрос #{ticket['id']}",
            color=disnake.Color.green(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(
            name="Пользователь",
            value=f"{member.mention} ({member})\nID: `{member.id}`",
            inline=False,
        )
        embed.add_field(name="Текст вопроса", value=ticket["question_text"][:1024], inline=False)
        return embed

    @staticmethod
    def _extract_entity_id(custom_id: str) -> int | None:
        parts = custom_id.split(":")
        if not parts:
            return None
        last = parts[-1]
        return int(last) if last.isdigit() else None

    async def _respond(
        self,
        interaction: disnake.ApplicationCommandInteraction | disnake.MessageInteraction | disnake.ModalInteraction,
        content: str,
        *,
        ephemeral: bool,
    ) -> None:
        use_ephemeral = ephemeral and interaction.guild is not None
        try:
            if interaction.response.is_done():
                await interaction.followup.send(content, ephemeral=use_ephemeral)
            else:
                await interaction.response.send_message(content, ephemeral=use_ephemeral)
        except (InteractionTimedOut, NotFound, HTTPException):
            return
