"""Запуск бота + Mini App API в одном процессе."""
from __future__ import annotations

import asyncio
import logging
import os

import uvicorn
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("run")


async def main() -> None:
    from bot import bot, dp, setup_webapp_button
    from api import app as api_app

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8080"))

    await setup_webapp_button()

    config = uvicorn.Config(api_app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)

    me = await bot.get_me()
    logger.info("Бот @%s | Mini App http://127.0.0.1:%s", me.username, port)

    await asyncio.gather(
        dp.start_polling(bot),
        server.serve(),
    )


if __name__ == "__main__":
    asyncio.run(main())
