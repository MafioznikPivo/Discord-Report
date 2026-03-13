from __future__ import annotations

import asyncio
import logging

from src.services.report_service import ReportService


logger = logging.getLogger(__name__)


class SchedulerService:
    def __init__(self, report_service: ReportService, poll_sec: int) -> None:
        self.report_service = report_service
        self.poll_sec = poll_sec
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._runner(), name="report-deadline-scheduler")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _runner(self) -> None:
        while True:
            try:
                await self.report_service.process_deadlines()
            except Exception:  # noqa: BLE001
                logger.exception("Ошибка при обработке дедлайнов жалоб")
            await asyncio.sleep(self.poll_sec)
