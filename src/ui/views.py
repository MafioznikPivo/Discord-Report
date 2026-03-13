from __future__ import annotations

from collections.abc import Awaitable, Callable

import disnake


ButtonHandler = Callable[[disnake.MessageInteraction], Awaitable[None]]


class ReportEntryView(disnake.ui.View):
    def __init__(self, on_create: ButtonHandler, *, disabled: bool = False) -> None:
        super().__init__(timeout=None)
        self._on_create = on_create

        button = disnake.ui.Button(
            label="Написать жалобу",
            style=disnake.ButtonStyle.primary,
            custom_id="report:create",
            disabled=disabled,
        )
        button.callback = self._handle_create
        self.add_item(button)

    async def _handle_create(self, interaction: disnake.MessageInteraction) -> None:
        await self._on_create(interaction)


class ReportModerationView(disnake.ui.View):
    def __init__(
        self,
        report_id: int,
        on_accept: ButtonHandler,
        on_reject: ButtonHandler,
        *,
        disabled: bool = False,
    ) -> None:
        super().__init__(timeout=None)
        self.report_id = report_id
        self._on_accept = on_accept
        self._on_reject = on_reject

        accept_button = disnake.ui.Button(
            label="Принять жалобу",
            style=disnake.ButtonStyle.success,
            custom_id=f"report:mod:accept:{report_id}",
            disabled=disabled,
        )
        reject_button = disnake.ui.Button(
            label="Отклонить",
            style=disnake.ButtonStyle.danger,
            custom_id=f"report:mod:reject:{report_id}",
            disabled=disabled,
        )
        accept_button.callback = self._handle_accept
        reject_button.callback = self._handle_reject
        self.add_item(accept_button)
        self.add_item(reject_button)

    async def _handle_accept(self, interaction: disnake.MessageInteraction) -> None:
        await self._on_accept(interaction)

    async def _handle_reject(self, interaction: disnake.MessageInteraction) -> None:
        await self._on_reject(interaction)


class ReportCaseView(disnake.ui.View):
    def __init__(
        self,
        report_id: int,
        on_move_reporter: ButtonHandler,
        on_move_offender: ButtonHandler,
        on_close: ButtonHandler,
        *,
        disabled: bool = False,
    ) -> None:
        super().__init__(timeout=None)
        self.report_id = report_id
        self._on_move_reporter = on_move_reporter
        self._on_move_offender = on_move_offender
        self._on_close = on_close

        move_reporter_button = disnake.ui.Button(
            label="Переместить инициатора жалобы",
            style=disnake.ButtonStyle.primary,
            custom_id=f"report:case:move_reporter:{report_id}",
            disabled=disabled,
        )
        move_offender_button = disnake.ui.Button(
            label="Переместить нарушителя",
            style=disnake.ButtonStyle.primary,
            custom_id=f"report:case:move_offender:{report_id}",
            disabled=disabled,
        )
        close_button = disnake.ui.Button(
            label="Закрыть жалобу",
            style=disnake.ButtonStyle.danger,
            custom_id=f"report:case:close:{report_id}",
            disabled=disabled,
        )
        move_reporter_button.callback = self._handle_move_reporter
        move_offender_button.callback = self._handle_move_offender
        close_button.callback = self._handle_close
        self.add_item(move_reporter_button)
        self.add_item(move_offender_button)
        self.add_item(close_button)

    async def _handle_move_reporter(self, interaction: disnake.MessageInteraction) -> None:
        await self._on_move_reporter(interaction)

    async def _handle_move_offender(self, interaction: disnake.MessageInteraction) -> None:
        await self._on_move_offender(interaction)

    async def _handle_close(self, interaction: disnake.MessageInteraction) -> None:
        await self._on_close(interaction)


class HelpEntryView(disnake.ui.View):
    def __init__(self, on_create: ButtonHandler, *, disabled: bool = False) -> None:
        super().__init__(timeout=None)
        self._on_create = on_create

        button = disnake.ui.Button(
            label="Задать вопрос",
            style=disnake.ButtonStyle.primary,
            custom_id="help:create",
            disabled=disabled,
        )
        button.callback = self._handle_create
        self.add_item(button)

    async def _handle_create(self, interaction: disnake.MessageInteraction) -> None:
        await self._on_create(interaction)


class HelpModerationView(disnake.ui.View):
    def __init__(
        self,
        ticket_id: int,
        on_reply: ButtonHandler,
        on_close: ButtonHandler,
        *,
        disabled: bool = False,
    ) -> None:
        super().__init__(timeout=None)
        self.ticket_id = ticket_id
        self._on_reply = on_reply
        self._on_close = on_close

        reply_button = disnake.ui.Button(
            label="Ответить",
            style=disnake.ButtonStyle.success,
            custom_id=f"help:reply:{ticket_id}",
            disabled=disabled,
        )
        close_button = disnake.ui.Button(
            label="Закрыть",
            style=disnake.ButtonStyle.danger,
            custom_id=f"help:close:{ticket_id}",
            disabled=disabled,
        )
        reply_button.callback = self._handle_reply
        close_button.callback = self._handle_close
        self.add_item(reply_button)
        self.add_item(close_button)

    async def _handle_reply(self, interaction: disnake.MessageInteraction) -> None:
        await self._on_reply(interaction)

    async def _handle_close(self, interaction: disnake.MessageInteraction) -> None:
        await self._on_close(interaction)
