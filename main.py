"""Точка входа: база, HTTP-сервер и оба бота в одном процессе.

Один сервис вместо трёх: общий пул соединений к базе и один деплой (playbook 0).
Админ-бота можно вынести в отдельный сервис — снимите RUN_ADMIN_BOT_INLINE и
запускайте admin.py.
"""
import asyncio
import logging

import uvicorn

from api.main import app
from bots import admin_bot, user_bot
from config import settings
from db.database import dispose_db, init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("main")


async def serve_http() -> None:
    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=settings.port,
        log_level="info",
        access_log=False,
    )
    await uvicorn.Server(config).serve()


async def main() -> None:
    await init_db()

    tasks = [asyncio.create_task(serve_http(), name="http")]
    if settings.bot_token:
        tasks.append(asyncio.create_task(user_bot.run(), name="user-bot"))
    else:
        log.warning("BOT_TOKEN не задан — Mini App будет отдаваться, но бот не запустится")

    if settings.run_admin_bot_inline:
        if settings.admin_bot_token:
            tasks.append(asyncio.create_task(admin_bot.run(), name="admin-bot"))
        else:
            log.warning("ADMIN_BOT_TOKEN не задан — админ-бот не запущен")
    else:
        log.info("Админ-бот вынесен в отдельный сервис (RUN_ADMIN_BOT_INLINE=false)")

    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await dispose_db()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Остановлено")
