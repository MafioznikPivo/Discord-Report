from __future__ import annotations

import asyncio
import logging

import disnake
from disnake.ext import commands

from src.cogs.help_cog import HelpCog
from src.cogs.report_cog import ReportCog
from src.config import AppConfig, ConfigError
from src.db.database import Database
from src.services.help_service import HelpService
from src.services.recovery_service import RecoveryService
from src.services.report_service import ReportService
from src.services.scheduler_service import SchedulerService


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


async def main() -> None:
    setup_logging()

    try:
        config = AppConfig.from_env()
    except ConfigError as exc:
        raise SystemExit(f"Ошибка конфигурации: {exc}") from exc

    intents = disnake.Intents.default()
    intents.guilds = True
    intents.members = True
    intents.messages = True
    intents.dm_messages = True
    intents.voice_states = True
    intents.message_content = True

    bot = commands.InteractionBot(intents=intents)

    db = Database(config.db_path)
    await db.connect()

    report_service = ReportService(bot, db, config)
    help_service = HelpService(bot, db, config)

    report_cog = ReportCog(bot, config, report_service)
    help_cog = HelpCog(bot, config, help_service)

    bot.add_cog(report_cog)
    bot.add_cog(help_cog)

    scheduler = SchedulerService(report_service, config.scheduler_poll_sec)
    recovery = RecoveryService(bot, report_service, help_service, report_cog, help_cog)

    startup_complete = False

    @bot.event
    async def on_ready() -> None:
        nonlocal startup_complete
        if startup_complete:
            return

        startup_complete = True
        await recovery.recover()
        await scheduler.start()
        await bot.change_presence(
            status=disnake.Status.online,
            activity=disnake.Game(
                name="/report | /\u043f\u043e\u043c\u043e\u0449\u044c"
            ),
        )
        logging.getLogger(__name__).info(
            "Бот подключён как %s (%s)",
            bot.user,
            bot.user.id if bot.user else "n/a",
        )

    try:
        await bot.start(config.bot_token)
    finally:
        await scheduler.stop()
        await db.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
