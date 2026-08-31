"""Заливка варианта: дубли ищутся по ID источника.

Правило, которое проверяется здесь:
  * вариант опознаётся по одному заданию — №26. Совпал его ID источника с №26
    уже собранного варианта — вариант заливали, стоп;
  * совпадение заданий №1-25 заливке не мешает: одно и то же задание источник
    ставит в разные варианты;
  * каждое задание ищется по паре (номер, ID источника). Нашлось — берём его id,
    не нашлось — создаём.

Гоняется на SQLite, боевая база не трогается. Запуск: python tests/test_variant_import.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB_FILE = Path(tempfile.gettempdir()) / f"train_gia_test_{os.getpid()}.sqlite3"

# Окружение готовится до импорта config. setdefault, а не присваивание: если тест
# идёт вместе с test_api, база у них общая и переопределять её нельзя.
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{DB_FILE.as_posix()}")
os.environ.setdefault("BOT_TOKEN", "123456:TEST-TOKEN-abcdefghijklmnop")
os.environ.setdefault("ADMIN_BOT_TOKEN", "")
os.environ.setdefault("WEBAPP_URL", "https://example.test/app")

from sqlalchemy import select  # noqa: E402

from core.tasks_meta import (  # noqa: E402
    KIND_CHOICE,
    KIND_DIGITS,
    KIND_MATCH,
    LAST_TASK,
    TASK_NUMBERS,
    kind_of,
)
from core.variant_import import import_tasks, load_payload  # noqa: E402
from db import crud  # noqa: E402
from db.database import DATABASE_URL, SessionMaker, init_db  # noqa: E402
from db.models import Task, VariantItem  # noqa: E402

# Страховка: локальный .env смотрит в боевую базу, и запуск теста по ней стёр бы
# смысл проверок и добавил мусора ученикам.
assert DATABASE_URL.startswith("sqlite"), f"тест обязан идти на SQLite, а идёт на {DATABASE_URL}"


def make_task(number: int, source_id: int, tail: str = "") -> dict:
    """Задание в том виде, в каком его отдаёт разбор варианта."""
    kind = kind_of(number)
    task = {
        "number": number,
        "source_id": source_id,
        "kind": kind,
        "text": f"Условие задания {number}{tail}",
        "material": f"Материал задания {number}{tail}",
        "options": [],
        "match_left": [],
        "correct": [],
        "answers": [],
    }
    if kind == KIND_CHOICE:
        task["options"] = [f"вариант {i}" for i in range(1, 5)]
        task["correct"] = [2]
    elif kind == KIND_MATCH:
        task["match_left"] = [f"ошибка {i}" for i in range(1, 6)]
        task["options"] = [f"предложение {i}" for i in range(1, 10)]
        task["correct"] = [3, 1, 4, 2, 5]
    elif kind == KIND_DIGITS:
        task["answers"] = ["134"]
    else:
        task["answers"] = ["вследствие"]
    return task


def make_variant(base: int, tail: str = "", skip_source: tuple[int, ...] = ()) -> list:
    """26 заданий. ID источника = base + номер, чтобы варианты не пересекались."""
    tasks = []
    for number in TASK_NUMBERS:
        task = make_task(number, base + number, tail)
        if number in skip_source:
            task["source_id"] = None
        tasks.append(task)
    return tasks


async def upload(tasks: list, variant_number: int | None = None):
    """Полный путь заливки: проверка структуры, затем база."""
    loaded = load_payload({"texts": {}, "tasks": tasks})
    assert not loaded.errors, loaded.errors
    return await import_tasks(loaded.tasks, variant_number)


async def variant_task_ids(number: int) -> dict[int, str]:
    async with SessionMaker() as db:
        variant = await crud.get_variant_by_number(db, number)
        assert variant is not None, f"вариант №{number} не собран"
        rows = await db.execute(
            select(VariantItem.number, VariantItem.task_id)
            .where(VariantItem.variant_id == variant.id)
        )
        return dict(rows.all())


async def count_tasks(number: int) -> int:
    async with SessionMaker() as db:
        return len(await crud.list_tasks(db, number, limit=1000))


async def scenario() -> None:
    await init_db()

    # --- первый вариант: всё новое ---------------------------------------
    report = await upload(make_variant(1000), variant_number=901)
    assert not report.is_duplicate, "первый вариант не может быть дублем"
    assert len(report.created) == LAST_TASK, report.created
    assert report.duplicates == 0
    assert report.variant_status == "собран"
    first = await variant_task_ids(901)
    assert len(first) == LAST_TASK

    # ID источника доехал до базы
    async with SessionMaker() as db:
        task = await crud.get_task(db, first[8])
        assert task.source_id == 1008, task.source_id

    # --- второй вариант: те же №1-25, свой №26 ----------------------------
    # Совпадение 25 заданий заливке не мешает — так и бывает у источника.
    second_tasks = make_variant(1000)
    second_tasks[-1]["source_id"] = 2026
    second_tasks[-1]["text"] = "Условие задания 26, другое"
    report = await upload(second_tasks, variant_number=902)
    assert not report.is_duplicate, "вариант с новым №26 обязан залиться"
    assert len(report.created) == 1, f"создаться должно только №26, а создано {report.created}"
    assert report.duplicates == LAST_TASK - 1, report.duplicates
    second = await variant_task_ids(902)
    for number in range(1, LAST_TASK):
        assert second[number] == first[number], f"№{number} задвоился вместо переиспользования"
    assert second[LAST_TASK] != first[LAST_TASK], "№26 у второго варианта обязан быть свой"

    # --- тот же вариант второй раз ----------------------------------------
    before = await count_tasks(1)
    report = await upload(make_variant(1000), variant_number=903)
    assert report.is_duplicate, "повторная заливка обязана упереться в №26"
    assert report.duplicate_of == 901, report.duplicate_of
    assert not report.created, report.created
    assert await count_tasks(1) == before, "при отказе не должно создаваться ничего"
    async with SessionMaker() as db:
        assert await crud.get_variant_by_number(db, 903) is None

    # --- текст на сайте поправили, ID источника прежний --------------------
    # Раньше ловилось отпечатком содержимого и давало копию каждого задания.
    edited = make_variant(1000, tail=" (поправлено)")
    edited[-1]["source_id"] = 3026
    report = await upload(edited, variant_number=904)
    assert not report.is_duplicate
    assert len(report.created) == 1, f"правка текста не должна плодить копии: {report.created}"
    fourth = await variant_task_ids(904)
    assert fourth[1] == first[1]

    # --- задания без ID источника: запасной ключ — отпечаток ---------------
    manual = make_variant(5000, tail=" ручное", skip_source=(1, 2))
    report = await upload(manual, variant_number=905)
    assert len(report.created) == LAST_TASK, report.created
    report = await upload(make_variant(5000, tail=" ручное", skip_source=(1, 2)))
    assert report.duplicates == LAST_TASK, (
        f"без ID источника задание обязано опознаваться по отпечатку: {report.created}"
    )

    # --- «Только задания»: проверки варианта нет, дублей заданий тоже ------
    report = await upload(make_variant(1000))
    assert not report.is_duplicate, "без сборки варианта проверка по №26 не делается"
    assert not report.created, "все задания уже в базе"
    assert report.duplicates == LAST_TASK

    async with SessionMaker() as db:
        rows = await db.execute(select(Task).where(Task.number == LAST_TASK))
        sources = sorted(t.source_id for t in rows.scalars())
    assert sources == [1026, 2026, 3026, 5026], sources


def test_variant_import_by_source_id() -> None:
    asyncio.run(scenario())


if __name__ == "__main__":
    asyncio.run(scenario())
    print("ок")
