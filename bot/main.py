import asyncio
import contextlib
import logging
import signal
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode

from bot.config import ConfigLoader, Settings
from core.monitor import FeedMonitor
from core.state import StateManager
from handlers import commands
from processing import create_pipeline

logger = logging.getLogger(__name__)


LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def setup_logging(level: str = "INFO"):
    log_level = LOG_LEVELS.get(level.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%d-%m-%Y %H:%M:%S",
        stream=sys.stdout,
        force=True,
    )
    logging.getLogger("aiogram").setLevel(logging.INFO)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)


class Application:
    def __init__(self):
        self.settings = None
        self.config = None
        self.state_manager = None
        self.bot = None
        self.dp = None
        self.monitor = None
        self.pipeline = None
        self._shutdown_event = asyncio.Event()

    async def setup(self):
        try:
            self.settings = Settings()
        except Exception as e:
            logger.critical(f"Failed to load settings: {e}")
            sys.exit(1)

        setup_logging(self.settings.log_level)
        logger.info("Starting RSS to Telegram Bot")

        try:
            config_loader = ConfigLoader(self.settings.config_path)
            self.config = config_loader.load()
        except FileNotFoundError as e:
            logger.critical(str(e))
            sys.exit(1)
        except Exception as e:
            logger.critical(f"Failed to load config: {e}", exc_info=True)
            sys.exit(1)

        self.state_manager = StateManager(self.settings.state_path)
        await self.state_manager.load()

        self.pipeline = create_pipeline()

        if self.settings.telegram_api_server_url:
            session = AiohttpSession(api=TelegramAPIServer.from_base(self.settings.telegram_api_server_url, is_local=True), timeout=300)
        else:
            session = AiohttpSession(timeout=300)

        try:
            self.bot = Bot(token=self.settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML), session=session)
            bot_info = await self.bot.get_me()
            logger.info(f"Bot connected: @{bot_info.username} ({bot_info.id})")

        except Exception as e:
            logger.critical(f"Failed to initialize bot: {e}")
            sys.exit(1)

        self.dp = Dispatcher()

        self.monitor = FeedMonitor(self.config, self.state_manager, self.bot, self.pipeline)

        self.dp["settings"] = self.settings
        self.dp["config"] = self.config
        self.dp["state_manager"] = self.state_manager
        self.dp["pipeline"] = self.pipeline
        self.dp["monitor"] = self.monitor

        commands.setup_admin_filter(self.settings.admin_id_list)
        self.dp.include_router(commands.router)

        logger.info(
            f"Setup complete:\n"
            f"  - Admins: {', '.join(map(str, self.settings.admin_id_list))}\n"
            f"  - Channels: {len(self.config.channels)}\n"
            f"  - Feeds: {sum(len(c.feeds) for c in self.config.channels)}"
        )

    async def start(self):
        await self.monitor.start()
        logger.info("Bot is running. Press Ctrl+C to stop.")
        await self.dp.start_polling(self.bot)

    async def shutdown(self):
        logger.info("Shutting down...")
        if self.monitor:
            await self.monitor.stop()
        if self.state_manager:
            await self.state_manager.save()
        if self.bot:
            await self.bot.session.close()
        logger.info("Shutdown complete")


async def main():
    app = Application()

    def signal_handler(sig, frame):
        logger.info(f"Received signal {sig}")
        asyncio.create_task(app.shutdown())

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        await app.setup()
        await app.start()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        await app.shutdown()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
