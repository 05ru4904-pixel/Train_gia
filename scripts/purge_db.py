"""Очистка базы от заданий, вариантов и прохождений.

    python scripts/purge_db.py                 # только показать, что будет удалено
    python scripts/purge_db.py --yes           # удалить

Пользователи не трогаются: у них дата регистрации, тариф и plan_until.

Порядок удаления обязателен именно такой. `session_items.task_id` и
`variant_items.task_id` стоят с ondelete=RESTRICT — база не даст удалить задание,
на которое ссылается чей-то ответ. Поэтому сначала уходят ответы и сессии, затем
связки вариантов, и только потом сами задания.

Скрипт смотрит в ту базу, что указана в DATABASE_URL. Локальный .env смотрит в
боевую — печатаем адрес перед работой, чтобы это было видно.
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, func, select  # noqa: E402

from db.database import DATABASE_URL, SessionMaker, dispose_db  # noqa: E402
from db.models import (  # noqa: E402
    Session,
    SessionItem,
    Task,
    User,
    Variant,
    VariantItem,
)

# Порядок важен: от ссылающихся к тем, на кого ссылаются.
ORDER = (
    (SessionItem, "ответов в сессиях"),
    (Session, "сессий"),
    (VariantItem, "заданий в вариантах"),
    (Variant, "вариантов"),
    (Task, "заданий"),
)


def where_am_i() -> str:
    kind = DATABASE_URL.split("://")[0]
    host = DATABASE_URL.split("@")[-1].split("/")[0]
    return f"{kind} @ {host}"


async def counts(db) -> list[tuple[str, int]]:
    rows = []
    for model, title in ORDER:
        total = (await db.execute(select(func.count()).select_from(model))).scalar()
        rows.append((title, total))
    users = (await db.execute(select(func.count()).select_from(User))).scalar()
    rows.append(("пользователей (не трогаем)", users))
    return rows


async def main() -> None:
    parser = argparse.ArgumentParser(description="Очистка базы заданий и прохождений")
    parser.add_argument("--yes", action="store_true", help="действительно удалить")
    args = parser.parse_args()

    print(f"База: {where_am_i()}\n")

    async with SessionMaker() as db:
        for title, total in await counts(db):
            print(f"  {title}: {total}")

        if not args.yes:
            print("\nНичего не удалено. Для удаления: python scripts/purge_db.py --yes")
            await dispose_db()
            return

        print("\nУдаляю…")
        for model, title in ORDER:
            result = await db.execute(delete(model))
            print(f"  {title}: удалено {result.rowcount}")
        await db.commit()

        print("\nОсталось:")
        for title, total in await counts(db):
            print(f"  {title}: {total}")

    await dispose_db()


if __name__ == "__main__":
    asyncio.run(main())
