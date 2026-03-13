from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import disnake

from src.services.help_service import HelpService
from src.services.report_service import ReportService

if TYPE_CHECKING:
    from src.cogs.help_cog import HelpCog
    from src.cogs.report_cog import ReportCog


logger = logging.getLogger(__name__)


class RecoveryService:
    def __init__(
        self,
        bot: disnake.Client,
        report_service: ReportService,
        help_service: HelpService,
        report_cog: "ReportCog",
        help_cog: "HelpCog",
    ) -> None:
        self.bot = bot
        self.report_service = report_service
        self.help_service = help_service
        self.report_cog = report_cog
        self.help_cog = help_cog

    async def recover(self) -> None:
        self.bot.add_view(self.report_cog.build_entry_view())
        self.bot.add_view(self.help_cog.build_entry_view())

        pending_reports = await self.report_service.list_pending_reports_for_recovery()
        for report in pending_reports:
            intake_message_id = report.get("intake_message_id")
            if intake_message_id is None:
                continue
            self.bot.add_view(
                self.report_cog.build_moderation_view(report["id"]),
                message_id=int(intake_message_id),
            )

        accepted_reports = await self.report_service.list_accepted_reports_for_recovery()
        for report in accepted_reports:
            control_message_id = report.get("control_message_id")
            if control_message_id is None:
                continue
            self.bot.add_view(
                self.report_cog.build_case_view(report["id"]),
                message_id=int(control_message_id),
            )

        open_tickets = await self.help_service.list_open_tickets_for_recovery()
        for ticket in open_tickets:
            intake_message_id = ticket.get("intake_message_id")
            if intake_message_id is None:
                continue
            self.bot.add_view(
                self.help_cog.build_moderation_view(ticket["id"]),
                message_id=int(intake_message_id),
            )

        logger.info(
            "Восстановление завершено: pending_reports=%s accepted_reports=%s open_tickets=%s",
            len(pending_reports),
            len(accepted_reports),
            len(open_tickets),
        )
