import asyncio
import logging

import disnake
from disnake.ext import commands

from config.settings import settings
from database import init_db
from utils.logging_setup import setup_logging
from views.persistent_panels import PERSISTENT_VIEWS

setup_logging()
logger = logging.getLogger("bot")

COGS = (
    "cogs.applications",
    "cogs.audit",
)


class GibddBot(commands.Bot):
    def __init__(self) -> None:
        intents = disnake.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        self._panels_initialized = False

    def setup_bot(self) -> None:
        init_db()

        for cog in COGS:
            try:
                self.load_extension(cog)
                logger.info("Ког загружен: %s", cog)
            except Exception as exc:
                logger.exception("Ошибка загрузки %s: %s", cog, exc)

        for view_cls in PERSISTENT_VIEWS:
            self.add_view(view_cls())

        from cogs.applications import ApplicationActionView, ResignationActionView
        self.add_view(ApplicationActionView())
        self.add_view(ResignationActionView())

        from cogs.audit import (
            AuditActionView,
            AuditDemoteUserSelectView,
            AuditPromoteUserSelectView,
            AuditAcceptUserSelectView,
            AuditDismissUserSelectView,
        )
        self.add_view(AuditActionView())
        self.add_view(AuditDemoteUserSelectView())
        self.add_view(AuditPromoteUserSelectView())
        self.add_view(AuditAcceptUserSelectView())
        self.add_view(AuditDismissUserSelectView())

    async def on_ready(self) -> None:
        logger.info("%s запущен (ID: %s), серверов: %d", self.user, self.user.id, len(self.guilds))

        for warning in settings.validate():
            logger.warning("Конфиг: %s", warning)

        if self._panels_initialized:
            return
        self._panels_initialized = True

        await asyncio.sleep(2)

        panel_jobs = [
            ("ApplicationsCog", "init_panel"),
            ("AuditCog", "init_panel"),
        ]
        for cog_name, method_name in panel_jobs:
            cog = self.get_cog(cog_name)
            if not cog:
                logger.error("Ког %s не найден для %s", cog_name, method_name)
                continue
            try:
                await getattr(cog, method_name)()
            except Exception as exc:
                logger.exception("Ошибка %s.%s: %s", cog_name, method_name, exc)

    async def on_error(self, event: str, *args, **kwargs) -> None:
        logger.exception("Ошибка в событии %s: args=%s kwargs=%s", event, args, kwargs)


bot = GibddBot()


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
    if isinstance(error, commands.CommandNotFound):
        return
    logger.error("Ошибка команды %s: %s", ctx.command, error)


async def main():
    bot.setup_bot()
    if not settings.discord_token or settings.discord_token == "YOUR_BOT_TOKEN_HERE":
        logger.error("Токен не настроен. Укажите DISCORD_BOT_TOKEN в .env")
    else:
        try:
            await bot.start(settings.discord_token)
        except KeyboardInterrupt:
            await bot.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
