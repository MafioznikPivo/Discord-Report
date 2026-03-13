from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import disnake
from disnake.errors import Forbidden, HTTPException, NotFound

from src.config import AppConfig
from src.db.database import Database


class HelpService:
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

    async def has_open_ticket(self, user_id: int) -> bool:
        row = await self.db.fetchone(
            """
            SELECT id
            FROM help_tickets
            WHERE user_id = ? AND status = 'open'
            LIMIT 1
            """,
            (user_id,),
        )
        return row is not None

    async def create_open_ticket(
        self,
        guild_id: int,
        user_id: int,
        question_text: str,
    ) -> dict[str, Any]:
        ticket_id = await self.db.execute_insert(
            """
            INSERT INTO help_tickets (
                guild_id,
                user_id,
                question_text,
                status,
                created_at
            )
            VALUES (?, ?, ?, 'open', ?)
            """,
            (
                guild_id,
                user_id,
                question_text,
                self._now_iso(),
            ),
        )
        ticket = await self.get_ticket(ticket_id)
        if ticket is None:
            raise RuntimeError("Не удалось загрузить help-тикет после создания")
        return ticket

    async def get_ticket(self, ticket_id: int) -> dict[str, Any] | None:
        return await self.db.fetchone(
            "SELECT * FROM help_tickets WHERE id = ? LIMIT 1",
            (ticket_id,),
        )

    async def get_open_ticket_for_user(self, user_id: int) -> dict[str, Any] | None:
        return await self.db.fetchone(
            """
            SELECT *
            FROM help_tickets
            WHERE user_id = ? AND status = 'open'
            ORDER BY id DESC
            LIMIT 1
            """,
            (user_id,),
        )

    async def set_intake_message(self, ticket_id: int, message_id: int) -> None:
        await self.db.execute(
            "UPDATE help_tickets SET intake_message_id = ? WHERE id = ?",
            (message_id, ticket_id),
        )

    async def add_ticket_message(
        self,
        ticket_id: int,
        direction: str,
        author_id: int,
        content: str,
    ) -> None:
        await self.db.execute(
            """
            INSERT INTO help_messages (ticket_id, direction, author_id, content, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (ticket_id, direction, author_id, content, self._now_iso()),
        )

    async def close_ticket(self, ticket_id: int, moderator_id: int, reason: str) -> bool:
        rowcount = await self.db.execute(
            """
            UPDATE help_tickets
            SET status = 'closed',
                closed_by_mod_id = ?,
                close_reason = ?,
                closed_at = ?
            WHERE id = ? AND status = 'open'
            """,
            (moderator_id, reason, self._now_iso(), ticket_id),
        )
        return rowcount > 0

    async def list_open_tickets_for_recovery(self) -> list[dict[str, Any]]:
        return await self.db.fetchall(
            """
            SELECT *
            FROM help_tickets
            WHERE status = 'open' AND intake_message_id IS NOT NULL
            """
        )

    async def mark_ticket_status(self, ticket: dict[str, Any], status_line: str) -> None:
        intake_message_id = ticket.get("intake_message_id")
        if intake_message_id is None:
            return
        await self._append_status_and_remove_view(
            self.config.help_intake_channel_id,
            intake_message_id,
            status_line,
        )

    async def notify_ticket_reply(
        self,
        ticket: dict[str, Any],
        moderator: disnake.Member,
        reply_text: str,
    ) -> None:
        content = (
            f"Ответ модератора {moderator.mention} ({moderator}) по вашему вопросу #{ticket['id']}:\n"
            f"{reply_text}"
        )
        await self._send_dm_with_fallback(ticket["user_id"], content)

    async def notify_ticket_closed(
        self,
        ticket: dict[str, Any],
        moderator: disnake.Member,
        reason: str,
    ) -> None:
        content = (
            f"Ваш вопрос #{ticket['id']} закрыт модератором {moderator.mention} ({moderator}).\n"
            f"Причина закрытия: {reason}"
        )
        await self._send_dm_with_fallback(ticket["user_id"], content)

    async def forward_user_dm_to_intake(
        self,
        ticket: dict[str, Any],
        author: disnake.User,
        message_text: str,
        attachment_urls: list[str],
    ) -> None:
        channel = await self._resolve_text_channel(self.config.help_intake_channel_id)
        if channel is None:
            return

        body = message_text.strip() if message_text.strip() else "[без текста]"
        if attachment_urls:
            body = f"{body}\n\nВложения:\n" + "\n".join(attachment_urls)

        embed = disnake.Embed(
            title=f"Продолжение вопроса #{ticket['id']}",
            description=body[:4096],
            color=disnake.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Пользователь", value=f"{author.mention} ({author})", inline=False)

        try:
            await channel.send(embed=embed)
        except HTTPException:
            return

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
            await self.log_to_help_intake(
                f"Не удалось отправить ЛС пользователю `{user_id}`: личные сообщения закрыты."
            )
        except HTTPException:
            await self.log_to_help_intake(
                f"Не удалось отправить ЛС пользователю `{user_id}`: ошибка API Discord."
            )

    async def log_to_help_intake(self, content: str) -> None:
        channel = await self._resolve_text_channel(self.config.help_intake_channel_id)
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

    async def _resolve_text_channel(self, channel_id: int) -> disnake.TextChannel | None:
        channel = self.bot.get_channel(channel_id)
        if isinstance(channel, disnake.TextChannel):
            return channel

        try:
            fetched = await self.bot.fetch_channel(channel_id)
        except HTTPException:
            return None
        return fetched if isinstance(fetched, disnake.TextChannel) else None

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()
