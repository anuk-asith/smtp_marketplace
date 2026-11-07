"""
Application entry point — runs the Telegram bot only.
No web server needed (payments are confirmed manually by admin).
"""
import asyncio
import logging

from .bot import bot, dp
from .config import get_settings
from .db import init_db


async def main() -> None:
    cfg = get_settings()
    logging.basicConfig(
        level=cfg.log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    log = logging.getLogger("main")
    log.info("Starting %s …", cfg.shop_name)
    await init_db()
    log.info("Database ready. Bot is running.")
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    asyncio.run(main())
