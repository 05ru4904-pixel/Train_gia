"""Заливка демонстрационных заданий.

Нужна, чтобы приложение можно было прощёлкать целиком до того, как в базе появится
настоящий контент. Все созданные здесь задания помечены DEMO_MARK и удаляются одной
командой — реальные задания, добавленные через админ-бота, скрипт не трогает.

    python scripts/seed.py            # залить демо-задания
    python scripts/seed.py --clear    # удалить только демо-задания
    python scripts/seed.py --per 15   # сколько заданий на каждый номер

ВАЖНО: содержимое ниже — заглушки для проверки интерфейса, а не материалы ЕГЭ.
Исключение — блок REAL_STRESS: это общеизвестные примеры на ударение, они верны.
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select  # noqa: E402

from core.parser import ParsedTask  # noqa: E402
from core.tasks_meta import TASK_NUMBERS, subtitle, title  # noqa: E402
from db import crud  # noqa: E402
from db.database import SessionMaker, dispose_db, init_db  # noqa: E402
from db.models import Task, VariantItem  # noqa: E402

DEMO_MARK = "[демо]"

# Настоящие примеры на постановку ударения — для задания №4.
REAL_STRESS = [
    (["звонИт", "звОнит", "позвОнит", "тОрты"], [0, 3]),
    (["тОрты", "тортЫ", "бантЫ", "шарфЫ"], [0]),
    (["каталОг", "катАлог", "договОр", "дОговор"], [0, 2]),
    (["красИвее", "красивЕе", "слИвовый", "сливОвый"], [0, 2]),
    (["квартАл", "квАртал", "жалюзИ", "жалЮзи"], [0, 2]),
    (["облегчИть", "облЕгчить", "щавЕль", "щАвель"], [0, 2]),
    (["создалА", "сОздала", "нАчал", "начАл"], [0, 2]),
    (["вклЮчит", "включИт", "черпАть", "чЕрпать"], [1, 3]),
]


def demo_task(number: int, index: int) -> ParsedTask:
    """Задание-заглушка с предсказуемым правильным ответом."""
    if number == 4 and index < len(REAL_STRESS):
        options, correct = REAL_STRESS[index]
        return ParsedTask(
            number=4,
            # Пометка нужна и здесь: без неё --clear не найдёт эти задания и они
            # останутся в базе вперемешку с настоящими.
            text=f"{DEMO_MARK} В каком слове (или словах) верно поставлено ударение?",
            options=list(options),
            correct=list(correct),
        )

    # Каждое третье задание — с несколькими правильными ответами (ТЗ п.5).
    multi = index % 3 == 2
    option_count = 4 + (index % 3)
    options = [
        f"{DEMO_MARK} вариант {chr(1040 + i)} для задания №{number}"
        for i in range(option_count)
    ]
    correct = [index % option_count]
    if multi:
        correct = sorted({index % option_count, (index + 2) % option_count})
    return ParsedTask(
        number=number,
        text=(
            f"{DEMO_MARK} Задание №{number} — {title(number)}. {subtitle(number)}.\n"
            + ("Укажите все верные варианты." if multi else "Укажите верный вариант.")
        ),
        options=options,
        correct=correct,
    )


def is_demo(task: Task) -> bool:
    if DEMO_MARK in (task.text or ""):
        return True
    return any(DEMO_MARK in str(option) for option in (task.options or []))


async def seed(per_number: int) -> None:
    await init_db()
    created = 0
    async with SessionMaker() as db:
        existing = await crud.task_counts(db)
        for number in TASK_NUMBERS:
            have = existing.get(number, 0)
            for index in range(max(0, per_number - have)):
                await crud.create_task(db, demo_task(number, have + index))
                created += 1
        total = await crud.total_tasks_count(db)

    print(f"Добавлено демо-заданий: {created}")
    print(f"Всего заданий в базе: {total}")
    print("Полный вариант соберётся автоматически из случайных заданий.")


async def clear() -> None:
    await init_db()
    async with SessionMaker() as db:
        rows = await db.execute(select(Task))
        demo_ids = [task.id for task in rows.scalars() if is_demo(task)]
        if not demo_ids:
            print("Демо-заданий в базе нет.")
            return
        # Сначала убираем их из собранных вариантов, иначе внешний ключ не пустит.
        await db.execute(delete(VariantItem).where(VariantItem.task_id.in_(demo_ids)))
        await db.execute(delete(Task).where(Task.id.in_(demo_ids)))
        await db.commit()
    print(f"Удалено демо-заданий: {len(demo_ids)}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Демо-задания для тренажёра ЕГЭ")
    parser.add_argument("--clear", action="store_true", help="удалить демо-задания")
    parser.add_argument("--per", type=int, default=15, help="заданий на каждый номер")
    args = parser.parse_args()
    try:
        await (clear() if args.clear else seed(args.per))
    finally:
        await dispose_db()


if __name__ == "__main__":
    asyncio.run(main())
