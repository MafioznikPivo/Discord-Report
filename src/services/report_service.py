from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import disnake
from disnake.errors import Forbidden, HTTPException, NotFound

from src.config import AppConfig
from src.db.database import Database

ACTIVE_REPORT_STATUSES = ("pending", "accepted")


class ReportService:
    def __init__(self, bot: disnake.Client, db: Database, config: AppConfig) -> None:
        self.bot = bot
        self.db = db
        self.config = config

    async def get_target_guild(self) -> disnake.Guild | None:
        guild = self.bot.get_guild(self.config.target_guild_id)
        if guild is not None:
            return guild
        try:
            fetched = await self.bot.fetch_guild(self.config.target_guild_id)
        except HTTPException:
            return None
        return fetched

    async def fetch_target_member(self, user_id: int) -> disnake.Member | None:
        guild = await self.get_target_guild()
        if guild is None:
            return None

        member = guild.get_member(user_id)
        if member is not None:
            return member

        try:
            return await guild.fetch_member(user_id)
        except (NotFound, HTTPException):
            return None

    async def has_active_report_for_reporter(self, reporter_id: int) -> bool:
        row = await self.db.fetchone(
            """
            SELECT id
            FROM reports
            WHERE reporter_id = ? AND status IN ('pending', 'accepted')
            LIMIT 1
            """,
            (reporter_id,),
        )
        return row is not None

    async def create_pending_report(
        self,
        guild_id: int,
        reporter_id: int,
        offender_id: int,
        reason: str,
    ) -> dict[str, Any]:
        report_id = await self.db.execute_insert(
            """
            INSERT INTO reports (
                guild_id,
                reporter_id,
                offender_id,
                reason,
                status,
                created_at
            )
            VALUES (?, ?, ?, ?, 'pending', ?)
            """,
            (
                guild_id,
                reporter_id,
                offender_id,
                reason,
                self._now_iso(),
            ),
        )
        report = await self.get_report(report_id)
        if report is None:
            raise RuntimeError("Не удалось загрузить жалобу после создания")
        return report

    async def get_report(self, report_id: int) -> dict[str, Any] | None:
        return await self.db.fetchone(
            "SELECT * FROM reports WHERE id = ? LIMIT 1",
            (report_id,),
        )

    async def set_intake_message(self, report_id: int, message_id: int) -> None:
        await self.db.execute(
            "UPDATE reports SET intake_message_id = ? WHERE id = ?",
            (message_id, report_id),
        )

    async def accept_report(
        self,
        report_id: int,
        moderator_id: int,
        report_text_channel_id: int,
        report_voice_channel_id: int,
        control_message_id: int,
    ) -> bool:
        reporter_deadline_ts = self._now_ts() + self.config.report_initial_join_deadline_sec
        rowcount = await self.db.execute(
            """
            UPDATE reports
            SET status = 'accepted',
                accepted_at = ?,
                accepted_by_mod_id = ?,
                report_text_channel_id = ?,
                report_voice_channel_id = ?,
                control_message_id = ?,
                reporter_deadline_ts = ?,
                offender_deadline_ts = NULL
            WHERE id = ? AND status = 'pending'
            """,
            (
                self._now_iso(),
                moderator_id,
                report_text_channel_id,
                report_voice_channel_id,
                control_message_id,
                reporter_deadline_ts,
                report_id,
            ),
        )
        return rowcount > 0

    async def reject_report(self, report_id: int, moderator_id: int, reason: str) -> bool:
        rowcount = await self.db.execute(
            """
            UPDATE reports
            SET status = 'rejected',
                rejected_by_mod_id = ?,
                reject_reason = ?,
                closed_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (moderator_id, reason, self._now_iso(), report_id),
        )
        return rowcount > 0

    async def close_report(
        self,
        report_id: int,
        reason: str,
        closed_by_mod_id: int | None,
        closed_by_display: str | None,
        *,
        auto_closed: bool,
    ) -> tuple[bool, str]:
        report_before = await self.get_report(report_id)
        if report_before is None:
            return False, "Жалоба не найдена."

        if report_before["status"] not in ACTIVE_REPORT_STATUSES:
            return False, "Жалоба уже закрыта или недоступна для закрытия."

        rowcount = await self.db.execute(
            """
            UPDATE reports
            SET status = 'closed',
                close_reason = ?,
                closed_by_mod_id = ?,
                closed_at = ?,
                reporter_deadline_ts = NULL,
                offender_deadline_ts = NULL
            WHERE id = ? AND status IN ('pending', 'accepted')
            """,
            (reason, closed_by_mod_id, self._now_iso(), report_id),
        )
        if rowcount == 0:
            return False, "Жалоба уже закрыта другим модератором."

        report = await self.get_report(report_id)
        if report is None:
            return False, "Жалоба не найдена после обновления."

        status_line = (
            f"Статус: закрыта автоматически (неявка). Причина: {reason}"
            if auto_closed
            else f"Статус: закрыта модератором {closed_by_display or 'Неизвестно'}. Причина: {reason}"
        )
        await self.mark_intake_message_status(report, status_line)
        await self.mark_control_message_status(report, status_line)

        await self.delete_report_channels(report)
        await self.notify_report_closed(report, reason, closed_by_display, auto_closed=auto_closed)
        return True, "Жалоба закрыта."

    async def set_report_deadline(
        self,
        report_id: int,
        *,
        for_reporter: bool,
        deadline_ts: int | None,
    ) -> None:
        column = "reporter_deadline_ts" if for_reporter else "offender_deadline_ts"
        await self.db.execute(
            f"UPDATE reports SET {column} = ? WHERE id = ? AND status = 'accepted'",
            (deadline_ts, report_id),
        )

    async def clear_report_deadline(self, report_id: int, *, for_reporter: bool) -> None:
        await self.set_report_deadline(report_id, for_reporter=for_reporter, deadline_ts=None)

    async def list_pending_reports_for_recovery(self) -> list[dict[str, Any]]:
        return await self.db.fetchall(
            """
            SELECT *
            FROM reports
            WHERE status = 'pending' AND intake_message_id IS NOT NULL
            """
        )

    async def list_accepted_reports_for_recovery(self) -> list[dict[str, Any]]:
        return await self.db.fetchall(
            """
            SELECT *
            FROM reports
            WHERE status = 'accepted' AND control_message_id IS NOT NULL
            """
        )

    async def list_reports_with_deadlines(self) -> list[dict[str, Any]]:
        return await self.db.fetchall(
            """
            SELECT *
            FROM reports
            WHERE status = 'accepted'
              AND (reporter_deadline_ts IS NOT NULL OR offender_deadline_ts IS NOT NULL)
            """
        )

    async def process_deadlines(self) -> None:
        now_ts = self._now_ts()
        reports = await self.list_reports_with_deadlines()

        for report in reports:
            if report["status"] != "accepted":
                continue

            timeout_reason: str | None = None

            reporter_deadline = report.get("reporter_deadline_ts")
            if reporter_deadline is not None and now_ts >= reporter_deadline:
                if await self.member_in_any_voice(report["reporter_id"]):
                    await self.clear_report_deadline(report["id"], for_reporter=True)
                else:
                    timeout_reason = "Неявка инициатора жалобы в голосовой канал."

            offender_deadline = report.get("offender_deadline_ts")
            if (
                timeout_reason is None
                and offender_deadline is not None
                and now_ts >= offender_deadline
            ):
                if await self.member_in_any_voice(report["offender_id"]):
                    await self.clear_report_deadline(report["id"], for_reporter=False)
                else:
                    timeout_reason = "Неявка нарушителя в голосовой канал."

            if timeout_reason is not None:
                await self.close_report(
                    report["id"],
                    reason=timeout_reason,
                    closed_by_mod_id=None,
                    closed_by_display=None,
                    auto_closed=True,
                )

    async def create_case_channels(
        self,
        report_id: int,
    ) -> tuple[disnake.TextChannel, disnake.VoiceChannel]:
        guild = await self.get_target_guild()
        if guild is None:
            raise RuntimeError("Целевой сервер не найден")

        category_obj = guild.get_channel(self.config.report_category_id)
        if category_obj is None:
            try:
                category_obj = await guild.fetch_channel(self.config.report_category_id)
            except HTTPException as exc:
                raise RuntimeError("Не удалось получить категорию для репортов") from exc
        if not isinstance(category_obj, disnake.CategoryChannel):
            raise RuntimeError("REPORT_CATEGORY_ID должен указывать на категорию")

        bot_user = self.bot.user
        bot_member = guild.get_member(bot_user.id) if bot_user is not None else None

        text_overwrites: dict[disnake.abc.Snowflake, disnake.PermissionOverwrite] = {
            guild.default_role: disnake.PermissionOverwrite(view_channel=False)
        }
        voice_overwrites: dict[disnake.abc.Snowflake, disnake.PermissionOverwrite] = {
            guild.default_role: disnake.PermissionOverwrite(view_channel=False, connect=False)
        }

        if bot_member is not None:
            text_overwrites[bot_member] = disnake.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
                manage_messages=True,
            )
            voice_overwrites[bot_member] = disnake.PermissionOverwrite(
                view_channel=True,
                connect=True,
                speak=True,
                stream=True,
                move_members=True,
                manage_channels=True,
            )

        for role_id in self.config.moderator_role_ids:
            role = guild.get_role(role_id)
            if role is None:
                continue
            text_overwrites[role] = disnake.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_messages=True,
            )
            voice_overwrites[role] = disnake.PermissionOverwrite(
                view_channel=True,
                connect=True,
                speak=True,
                stream=True,
                move_members=True,
            )

        text_channel = await guild.create_text_channel(
            name=f"report-{report_id}",
            category=category_obj,
            overwrites=text_overwrites,
            reason=f"Открыт репорт #{report_id}",
        )
        voice_channel = await guild.create_voice_channel(
            name=f"report-{report_id}",
            category=category_obj,
            overwrites=voice_overwrites,
            reason=f"Открыт репорт #{report_id}",
        )
        return text_channel, voice_channel

    async def grant_case_access_to_member(
        self,
        report: dict[str, Any],
        member: disnake.Member,
    ) -> bool:
        text_channel = await self._resolve_text_channel(report.get("report_text_channel_id"))
        voice_channel = await self._resolve_voice_channel(report.get("report_voice_channel_id"))
        if text_channel is None or voice_channel is None:
            return False

        await text_channel.set_permissions(
            member,
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            reason=f"Доступ к репорту #{report['id']}",
        )
        await voice_channel.set_permissions(
            member,
            view_channel=True,
            connect=True,
            speak=True,
            stream=True,
            reason=f"Доступ к репорту #{report['id']}",
        )
        return True

    async def move_member_to_case_voice(
        self,
        report: dict[str, Any],
        member: disnake.Member,
    ) -> bool:
        if member.voice is None or member.voice.channel is None:
            return False

        voice_channel = await self._resolve_voice_channel(report.get("report_voice_channel_id"))
        if voice_channel is None:
            return False

        await self.grant_case_access_to_member(report, member)
        await member.move_to(voice_channel, reason=f"Перемещение в репорт #{report['id']}")
        return True

    async def member_in_any_voice(self, user_id: int) -> bool:
        member = await self.fetch_target_member(user_id)
        return bool(member and member.voice and member.voice.channel)

    async def mark_intake_message_status(self, report: dict[str, Any], status_line: str) -> None:
        intake_message_id = report.get("intake_message_id")
        if intake_message_id is None:
            return
        await self._append_status_and_remove_view(
            self.config.report_intake_channel_id,
            intake_message_id,
            status_line,
        )

    async def mark_control_message_status(self, report: dict[str, Any], status_line: str) -> None:
        control_message_id = report.get("control_message_id")
        text_channel_id = report.get("report_text_channel_id")
        if control_message_id is None or text_channel_id is None:
            return
        await self._append_status_and_remove_view(text_channel_id, control_message_id, status_line)

    async def notify_report_accepted(
        self,
        report: dict[str, Any],
        moderator: disnake.Member,
    ) -> None:
        content = (
            f"Ваша жалоба #{report['id']} принята модератором {moderator.mention} "
            f"({moderator}).\n"
            f"В течение {self.config.report_initial_join_deadline_sec // 60} минут "
            "зайдите в любой голосовой канал сервера."
        )
        await self._send_dm_with_fallback(report["reporter_id"], content)

    async def notify_report_rejected(
        self,
        report: dict[str, Any],
        moderator: disnake.Member,
        reason: str,
    ) -> None:
        content = (
            f"Ваша жалоба #{report['id']} была отклонена модератором {moderator.mention} "
            f"({moderator}).\n"
            f"Причина: {reason}"
        )
        await self._send_dm_with_fallback(report["reporter_id"], content)

    async def notify_report_closed(
        self,
        report: dict[str, Any],
        reason: str,
        moderator_display: str | None,
        *,
        auto_closed: bool,
    ) -> None:
        if auto_closed:
            reporter_content = (
                f"Жалоба #{report['id']} закрыта автоматически.\n"
                f"Причина: {reason}"
            )
            offender_content = (
                f"Разбирательство по жалобе #{report['id']} закрыто автоматически.\n"
                f"Причина: {reason}"
            )
        else:
            reporter_content = (
                f"Жалоба #{report['id']} была рассмотрена и закрыта модератором {moderator_display}.\n"
                f"Причина закрытия: {reason}"
            )
            offender_content = (
                f"Разбирательство по жалобе #{report['id']} завершено модератором {moderator_display}.\n"
                f"Причина: {reason}"
            )

        await self._send_dm_with_fallback(report["reporter_id"], reporter_content)

        offender_id = report.get("offender_id")
        if (
            report.get("accepted_at") is not None
            and offender_id is not None
            and offender_id != report["reporter_id"]
        ):
            await self._send_dm_with_fallback(offender_id, offender_content)

    async def notify_member_missing_for_move(
        self,
        target_user_id: int,
        report_id: int,
        moderator: disnake.Member,
    ) -> None:
        content = (
            f"Модератор {moderator.mention} ({moderator}) пытался переместить вас "
            f"в голосовой канал для разбора жалобы #{report_id}, но вы не были в voice.\n"
            f"У вас есть {self.config.report_missing_move_deadline_sec // 60} минут, "
            "чтобы зайти в любой голосовой канал сервера."
        )
        await self._send_dm_with_fallback(target_user_id, content)

    async def delete_report_channels(self, report: dict[str, Any]) -> None:
        text_channel_id = report.get("report_text_channel_id")
        voice_channel_id = report.get("report_voice_channel_id")

        if text_channel_id is not None:
            await self._delete_channel(text_channel_id)
        if voice_channel_id is not None:
            await self._delete_channel(voice_channel_id)

    async def _send_dm_with_fallback(self, user_id: int, content: str) -> None:
        user = self.bot.get_user(user_id)
        if user is None:
            try:
                user = await self.bot.fetch_user(user_id)
            except HTTPException:
                return

        try:
            await user.send(content)
        except Forbidden:
            await self.log_to_report_intake(
                f"Не удалось отправить ЛС пользователю `{user_id}`: личные сообщения закрыты."
            )
        except HTTPException:
            await self.log_to_report_intake(
                f"Не удалось отправить ЛС пользователю `{user_id}`: ошибка API Discord."
            )

    async def log_to_report_intake(self, content: str) -> None:
        channel = await self._resolve_text_channel(self.config.report_intake_channel_id)
        if channel is None:
            return
        try:
            await channel.send(f"[SYSTEM] {content}")
        except HTTPException:
            return

    async def _append_status_and_remove_view(
        self,
        channel_id: int,
        message_id: int,
        status_line: str,
    ) -> None:
        channel = await self._resolve_text_channel(channel_id)
        if channel is None:
            return

        try:
            message = await channel.fetch_message(message_id)
        except (NotFound, HTTPException):
            return

        content = message.content or ""
        if status_line not in content:
            if content:
                content = f"{content}\n{status_line}"[:2000]
            else:
                content = status_line[:2000]

        try:
            await message.edit(content=content, view=None)
        except HTTPException:
            return

    async def _resolve_text_channel(self, channel_id: int | None) -> disnake.TextChannel | None:
        if channel_id is None:
            return None

        channel = self.bot.get_channel(channel_id)
        if isinstance(channel, disnake.TextChannel):
            return channel

        try:
            fetched = await self.bot.fetch_channel(channel_id)
        except HTTPException:
            return None
        return fetched if isinstance(fetched, disnake.TextChannel) else None

    async def _resolve_voice_channel(self, channel_id: int | None) -> disnake.VoiceChannel | None:
        if channel_id is None:
            return None

        channel = self.bot.get_channel(channel_id)
        if isinstance(channel, disnake.VoiceChannel):
            return channel

        try:
            fetched = await self.bot.fetch_channel(channel_id)
        except HTTPException:
            return None
        return fetched if isinstance(fetched, disnake.VoiceChannel) else None

    async def _delete_channel(self, channel_id: int) -> None:
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except HTTPException:
                return

        try:
            await channel.delete(reason="Закрытие жалобы")
        except HTTPException:
            return

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _now_ts() -> int:
        return int(datetime.now(timezone.utc).timestamp())


