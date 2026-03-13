from __future__ import annotations

from contextlib import suppress
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import disnake
from disnake.errors import HTTPException, InteractionTimedOut, NotFound
from disnake.ext import commands

from src.config import AppConfig
from src.services.report_service import ReportService
from src.ui.views import ReportCaseView, ReportEntryView, ReportModerationView
from src.utils.permissions import has_moderator_role


class ReportCreateModal(disnake.ui.Modal):
    def __init__(self, cog: "ReportCog") -> None:
        self.cog = cog
        components = [
            disnake.ui.TextInput(
                label="ID нарушителя",
                custom_id="offender_id",
                placeholder="Укажите Discord ID нарушителя",
                min_length=17,
                max_length=20,
            ),
            disnake.ui.TextInput(
                label="Причина жалобы",
                custom_id="reason",
                style=disnake.TextInputStyle.paragraph,
                placeholder="Опишите ситуацию подробно",
                min_length=5,
                max_length=1000,
            ),
        ]
        super().__init__(
            title="Новая жалоба",
            custom_id=f"report:create:modal:{uuid4()}",
            components=components,
        )

    async def callback(self, interaction: disnake.ModalInteraction) -> None:
        await self.cog.handle_report_create_modal(interaction)


class ReportRejectModal(disnake.ui.Modal):
    def __init__(self, cog: "ReportCog", report_id: int) -> None:
        self.cog = cog
        self.report_id = report_id
        components = [
            disnake.ui.TextInput(
                label="Причина отклонения",
                custom_id="reject_reason",
                style=disnake.TextInputStyle.paragraph,
                min_length=3,
                max_length=1000,
            )
        ]
        super().__init__(
            title=f"Отклонение жалобы #{report_id}",
            custom_id=f"report:reject:modal:{report_id}:{uuid4()}",
            components=components,
        )

    async def callback(self, interaction: disnake.ModalInteraction) -> None:
        reason = interaction.text_values["reject_reason"].strip()
        await self.cog.handle_report_reject_modal(interaction, self.report_id, reason)


class ReportCloseModal(disnake.ui.Modal):
    def __init__(self, cog: "ReportCog", report_id: int) -> None:
        self.cog = cog
        self.report_id = report_id
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
            title=f"Закрытие жалобы #{report_id}",
            custom_id=f"report:close:modal:{report_id}:{uuid4()}",
            components=components,
        )

    async def callback(self, interaction: disnake.ModalInteraction) -> None:
        reason = interaction.text_values["close_reason"].strip()
        await self.cog.handle_report_close_modal(interaction, self.report_id, reason)


class ReportCog(commands.Cog):
    def __init__(self, bot: commands.InteractionBot, config: AppConfig, service: ReportService) -> None:
        self.bot = bot
        self.config = config
        self.service = service

    def build_entry_view(self) -> ReportEntryView:
        return ReportEntryView(self.handle_report_create_button)

    def build_moderation_view(self, report_id: int) -> ReportModerationView:
        return ReportModerationView(
            report_id,
            self.handle_report_accept_button,
            self.handle_report_reject_button,
        )

    def build_case_view(self, report_id: int) -> ReportCaseView:
        return ReportCaseView(
            report_id,
            self.handle_move_reporter_button,
            self.handle_move_offender_button,
            self.handle_close_report_button,
        )

    @commands.slash_command(name="report", description="Подать жалобу на пользователя")
    async def report_slash(self, interaction: disnake.ApplicationCommandInteraction) -> None:
        if not await self._validate_command_context(interaction):
            return

        embed = disnake.Embed(
            title="Система жалоб",
            description="Нажмите кнопку ниже, чтобы подать жалобу.",
            color=disnake.Color.orange(),
            timestamp=datetime.now(timezone.utc),
        )
        await interaction.response.send_message(
            embed=embed,
            view=self.build_entry_view(),
            ephemeral=interaction.guild is not None,
        )

    async def handle_report_create_button(self, interaction: disnake.MessageInteraction) -> None:
        if not await self._validate_command_context(interaction):
            return
        await interaction.response.send_modal(ReportCreateModal(self))

    async def handle_report_create_modal(self, interaction: disnake.ModalInteraction) -> None:
        await self._defer_if_needed(interaction, ephemeral=True)

        intake_channel = await self._resolve_text_channel(self.config.report_intake_channel_id)
        if intake_channel is None:
            await self._respond(
                interaction,
                "Канал для жалоб не найден. Обратитесь к администрации.",
                ephemeral=True,
            )
            return

        guild = await self.service.get_target_guild()
        if guild is None:
            await self._respond(interaction, "Целевой сервер недоступен.", ephemeral=True)
            return

        reporter_member = await self.service.fetch_target_member(interaction.author.id)
        if reporter_member is None:
            await self._respond(
                interaction,
                "Вы должны состоять на целевом сервере, чтобы использовать систему жалоб.",
                ephemeral=True,
            )
            return

        offender_raw = interaction.text_values["offender_id"].strip()
        if not offender_raw.isdigit():
            await self._respond(interaction, "ID нарушителя должен состоять только из цифр.", ephemeral=True)
            return
        offender_id = int(offender_raw)

        offender_member = await self.service.fetch_target_member(offender_id)
        if offender_member is None:
            await self._respond(
                interaction,
                "Пользователь с указанным ID не найден на целевом сервере.",
                ephemeral=True,
            )
            return

        if offender_id == interaction.author.id:
            await self._respond(interaction, "Нельзя отправить жалобу на самого себя.", ephemeral=True)
            return

        if offender_member.bot:
            await self._respond(interaction, "Нельзя отправить жалобу на бота.", ephemeral=True)
            return

        if await self.service.has_active_report_for_reporter(interaction.author.id):
            await self._respond(
                interaction,
                "У вас уже есть активная жалоба. Дождитесь её обработки.",
                ephemeral=True,
            )
            return

        reason = interaction.text_values["reason"].strip()

        report = await self.service.create_pending_report(
            guild_id=self.config.target_guild_id,
            reporter_id=interaction.author.id,
            offender_id=offender_id,
            reason=reason,
        )

        embed = self._build_report_intake_embed(report, reporter_member, offender_member)
        view = self.build_moderation_view(report["id"])

        try:
            intake_message = await intake_channel.send(embed=embed, view=view)
        except disnake.HTTPException:
            await self.service.close_report(
                report_id=report["id"],
                reason="Техническая ошибка при отправке жалобы в канал модерации.",
                closed_by_mod_id=None,
                closed_by_display=None,
                auto_closed=True,
            )
            await self._respond(
                interaction,
                "Не удалось отправить жалобу модераторам. Попробуйте позже.",
                ephemeral=True,
            )
            return

        await self.service.set_intake_message(report["id"], intake_message.id)

        await self._respond(
            interaction,
            f"Жалоба #{report['id']} отправлена модераторам.",
            ephemeral=True,
        )

    async def handle_report_accept_button(self, interaction: disnake.MessageInteraction) -> None:
        report_id = self._extract_entity_id(interaction.component.custom_id)
        if report_id is None:
            await self._respond(interaction, "Некорректный ID жалобы.", ephemeral=True)
            return

        if not self._is_moderator(interaction.author):
            await self._respond(interaction, "У вас нет прав для этого действия.", ephemeral=True)
            return

        await self._defer_if_needed(interaction, ephemeral=True)

        report = await self.service.get_report(report_id)
        if report is None:
            await self._respond(interaction, "Жалоба не найдена.", ephemeral=True)
            return

        if report["status"] != "pending":
            await self._respond(interaction, "Жалоба уже обработана.", ephemeral=True)
            return

        try:
            case_text, case_voice = await self.service.create_case_channels(report_id)
        except RuntimeError as exc:
            await self._respond(interaction, str(exc), ephemeral=True)
            return
        except disnake.HTTPException:
            await self._respond(
                interaction,
                "Не удалось создать каналы для разбора. Попробуйте позже.",
                ephemeral=True,
            )
            return

        case_embed = self._build_case_embed(report)
        case_view = self.build_case_view(report_id)
        try:
            control_message = await case_text.send(embed=case_embed, view=case_view)
        except disnake.HTTPException:
            with suppress(disnake.HTTPException):
                await case_text.delete(reason="Ошибка при создании управляющего сообщения")
            with suppress(disnake.HTTPException):
                await case_voice.delete(reason="Ошибка при создании управляющего сообщения")
            await self._respond(
                interaction,
                "Не удалось отправить управляющее сообщение в report-канал.",
                ephemeral=True,
            )
            return

        accepted = await self.service.accept_report(
            report_id=report_id,
            moderator_id=interaction.author.id,
            report_text_channel_id=case_text.id,
            report_voice_channel_id=case_voice.id,
            control_message_id=control_message.id,
        )
        if not accepted:
            with suppress(disnake.HTTPException):
                await case_text.delete(reason="Откат: жалоба уже обработана")
            with suppress(disnake.HTTPException):
                await case_voice.delete(reason="Откат: жалоба уже обработана")
            await self._respond(interaction, "Жалоба уже обработана другим модератором.", ephemeral=True)
            return

        accepted_report = await self.service.get_report(report_id)
        if accepted_report is not None:
            await self.service.mark_intake_message_status(
                accepted_report,
                f"Статус: принята модератором {interaction.author.mention}.",
            )
            await self.service.notify_report_accepted(accepted_report, interaction.author)

        await self._respond(
            interaction,
            f"Жалоба #{report_id} принята. Каналы: {case_text.mention} и {case_voice.mention}",
            ephemeral=True,
        )

    async def handle_report_reject_button(self, interaction: disnake.MessageInteraction) -> None:
        report_id = self._extract_entity_id(interaction.component.custom_id)
        if report_id is None:
            await self._respond(interaction, "Некорректный ID жалобы.", ephemeral=True)
            return

        if not self._is_moderator(interaction.author):
            await self._respond(interaction, "У вас нет прав для этого действия.", ephemeral=True)
            return

        report = await self.service.get_report(report_id)
        if report is None or report["status"] != "pending":
            await self._respond(interaction, "Жалоба уже обработана.", ephemeral=True)
            return

        await interaction.response.send_modal(ReportRejectModal(self, report_id))

    async def handle_report_reject_modal(
        self,
        interaction: disnake.ModalInteraction,
        report_id: int,
        reason: str,
    ) -> None:
        if not self._is_moderator(interaction.author):
            await self._respond(interaction, "У вас нет прав для этого действия.", ephemeral=True)
            return

        await self._defer_if_needed(interaction, ephemeral=True)

        rejected = await self.service.reject_report(report_id, interaction.author.id, reason)
        if not rejected:
            await self._respond(interaction, "Жалоба уже обработана.", ephemeral=True)
            return

        report = await self.service.get_report(report_id)
        if report is not None:
            await self.service.mark_intake_message_status(
                report,
                f"Статус: отклонена модератором {interaction.author.mention}. Причина: {reason}",
            )
            await self.service.notify_report_rejected(report, interaction.author, reason)

        await self._respond(interaction, f"Жалоба #{report_id} отклонена.", ephemeral=True)

    async def handle_move_reporter_button(self, interaction: disnake.MessageInteraction) -> None:
        await self._handle_move_participant(interaction, move_reporter=True)

    async def handle_move_offender_button(self, interaction: disnake.MessageInteraction) -> None:
        await self._handle_move_participant(interaction, move_reporter=False)

    async def _handle_move_participant(
        self,
        interaction: disnake.MessageInteraction,
        *,
        move_reporter: bool,
    ) -> None:
        report_id = self._extract_entity_id(interaction.component.custom_id)
        if report_id is None:
            await self._respond(interaction, "Некорректный ID жалобы.", ephemeral=True)
            return

        if not self._is_moderator(interaction.author):
            await self._respond(interaction, "У вас нет прав для этого действия.", ephemeral=True)
            return

        await self._defer_if_needed(interaction, ephemeral=True)

        report = await self.service.get_report(report_id)
        if report is None or report["status"] != "accepted":
            await self._respond(interaction, "Жалоба не находится в стадии разбора.", ephemeral=True)
            return

        target_id = report["reporter_id"] if move_reporter else report["offender_id"]
        target_member = await self.service.fetch_target_member(target_id)
        if target_member is None:
            await self._respond(
                interaction,
                f"Не удалось найти пользователя <@{target_id}> на сервере.",
                ephemeral=True,
            )
            return

        try:
            moved = await self.service.move_member_to_case_voice(report, target_member)
        except disnake.HTTPException:
            await self._respond(
                interaction,
                "Не удалось выполнить перемещение. Попробуйте позже.",
                ephemeral=True,
            )
            return

        if moved:
            await self.service.clear_report_deadline(report_id, for_reporter=move_reporter)
            who = "Инициатор" if move_reporter else "Нарушитель"
            await self._respond(
                interaction,
                f"{who} успешно перемещён в голосовой канал разбора.",
                ephemeral=True,
            )
            return

        deadline_ts = int(datetime.now(timezone.utc).timestamp()) + self.config.report_missing_move_deadline_sec
        await self.service.set_report_deadline(
            report_id,
            for_reporter=move_reporter,
            deadline_ts=deadline_ts,
        )
        await self.service.notify_member_missing_for_move(target_member.id, report_id, interaction.author)

        await self._respond(
            interaction,
            "Пользователь не находится в голосовом канале. Ему отправлено ЛС с дедлайном 5 минут.",
            ephemeral=True,
        )

    async def handle_close_report_button(self, interaction: disnake.MessageInteraction) -> None:
        report_id = self._extract_entity_id(interaction.component.custom_id)
        if report_id is None:
            await self._respond(interaction, "Некорректный ID жалобы.", ephemeral=True)
            return

        if not self._is_moderator(interaction.author):
            await self._respond(interaction, "У вас нет прав для этого действия.", ephemeral=True)
            return

        report = await self.service.get_report(report_id)
        if report is None or report["status"] != "accepted":
            await self._respond(interaction, "Жалоба не находится в стадии разбора.", ephemeral=True)
            return

        await interaction.response.send_modal(ReportCloseModal(self, report_id))

    async def handle_report_close_modal(
        self,
        interaction: disnake.ModalInteraction,
        report_id: int,
        reason: str,
    ) -> None:
        if not self._is_moderator(interaction.author):
            await self._respond(interaction, "У вас нет прав для этого действия.", ephemeral=True)
            return

        await self._defer_if_needed(interaction, ephemeral=True)

        success, message = await self.service.close_report(
            report_id=report_id,
            reason=reason,
            closed_by_mod_id=interaction.author.id,
            closed_by_display=f"{interaction.author.mention} ({interaction.author})",
            auto_closed=False,
        )
        await self._respond(interaction, message, ephemeral=True)
        if success:
            await self.service.log_to_report_intake(
                f"Жалоба #{report_id} закрыта модератором {interaction.author}"
            )

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
    def _build_report_intake_embed(
        report: dict[str, Any],
        reporter_member: disnake.Member,
        offender_member: disnake.Member,
    ) -> disnake.Embed:
        embed = disnake.Embed(
            title=f"Новая жалоба #{report['id']}",
            color=disnake.Color.red(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(
            name="Инициатор",
            value=f"{reporter_member.mention} ({reporter_member})\nID: `{reporter_member.id}`",
            inline=False,
        )
        embed.add_field(
            name="Нарушитель",
            value=f"{offender_member.mention} ({offender_member})\nID: `{offender_member.id}`",
            inline=False,
        )
        embed.add_field(name="Причина", value=report["reason"][:1024], inline=False)
        return embed

    @staticmethod
    def _build_case_embed(report: dict[str, Any]) -> disnake.Embed:
        embed = disnake.Embed(
            title=f"Разбор жалобы #{report['id']}",
            description="Используйте кнопки ниже для управления разбором.",
            color=disnake.Color.orange(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Инициатор", value=f"<@{report['reporter_id']}> (`{report['reporter_id']}`)")
        embed.add_field(name="Нарушитель", value=f"<@{report['offender_id']}> (`{report['offender_id']}`)")
        embed.add_field(name="Причина", value=report["reason"][:1024], inline=False)
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
