"""Отдельная точка входа для админ-бота.

Нужна, только если админ-бот вынесен в свой сервис на Railway. По умолчанию он
поднимается внутри main.py — тогда этот файл не используется.
"""
import asyncio
import logging

from bots import admin_bot
from db.database import dispose_db, init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("admin")


async def main() -> None:
    await init_db()
    try:
        await admin_bot.run()
    finally:
        await dispose_db()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Остановлено")
