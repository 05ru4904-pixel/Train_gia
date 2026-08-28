"""Импорт варианта из JSON в базу.

Исходники с сайта прогоняются через модель, которая приводит их к строгому JSON
(схема описана в README). Этот скрипт проверяет такой файл и заливает задания.

    python scripts/import_variant.py Задания/1 --dry-run   # только проверить
    python scripts/import_variant.py Задания/1             # проверить и залить
    python scripts/import_variant.py Задания/              # всю папку
    python scripts/import_variant.py Задания/1 --variant 1 # собрать ещё и вариант

Повторный запуск на том же файле ничего не задвоит: задание опознаётся по своему
содержимому, потому что ID источника мы намеренно не храним.
"""
import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.parser import ParsedTask  # noqa: E402
from core.tasks_meta import (  # noqa: E402
    KIND_CHOICE,
    KIND_DIGITS,
    KIND_MATCH,
    KIND_OPEN,
    LAST_TASK,
    TASK_KINDS,
)
from db import crud  # noqa: E402
from db.database import SessionMaker, dispose_db, init_db  # noqa: E402


@dataclass
class ImportTask:
    """Задание, готовое к заливке. Держит все четыре вида сразу."""

    number: int
    kind: str
    text: str
    passage: str | None
    options: list[str]
    match_left: list[str]
    correct: list[int]
    answers: list[str]


@dataclass
class Loaded:
    tasks: list[ImportTask] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _as_list(value) -> list:
    return list(value) if isinstance(value, list) else []


def _as_text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def load_file(path: Path) -> Loaded:
    """Читает и проверяет один JSON-файл варианта."""
    result = Loaded()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        result.errors.append(f"{path.name}: файл не разбирается как JSON — {exc}")
        return result
    except UnicodeDecodeError:
        result.errors.append(f"{path.name}: файл не в кодировке UTF-8")
        return result

    if not isinstance(data, dict) or "tasks" not in data:
        result.errors.append(f"{path.name}: нет ключа «tasks» на верхнем уровне")
        return result

    texts = data.get("texts") or {}
    if not isinstance(texts, dict):
        result.errors.append(f"{path.name}: «texts» должен быть объектом")
        texts = {}

    seen: set[int] = set()
    for position, raw in enumerate(data["tasks"], start=1):
        where = f"{path.name}, задание на позиции {position}"
        if not isinstance(raw, dict):
            result.errors.append(f"{where}: не объект")
            continue

        number = raw.get("number")
        if not isinstance(number, int) or not 1 <= number <= LAST_TASK:
            result.errors.append(f"{where}: номер {number!r} вне диапазона 1-{LAST_TASK}")
            continue
        where = f"{path.name}, №{number}"
        if number in seen:
            result.errors.append(f"{where}: номер встречается второй раз")
            continue
        seen.add(number)

        kind = raw.get("kind")
        if kind not in TASK_KINDS:
            result.errors.append(f"{where}: неизвестный вид {kind!r}, ожидался один из {TASK_KINDS}")
            continue

        text = _as_text(raw.get("text"))
        if not text:
            result.errors.append(f"{where}: пустое условие")
            continue

        options = [_as_text(o) for o in _as_list(raw.get("options"))]
        match_left = [_as_text(o) for o in _as_list(raw.get("match_left"))]
        correct = [c for c in _as_list(raw.get("correct")) if isinstance(c, int)]
        answers = [_as_text(a) for a in _as_list(raw.get("answers")) if _as_text(a)]

        # Исходный текст: либо общий по ссылке, либо свой у задания.
        ref = raw.get("text_ref")
        passage_parts = []
        if ref:
            if ref not in texts:
                result.errors.append(f"{where}: text_ref={ref!r}, но такого текста нет в «texts»")
                continue
            passage_parts.append(_as_text(texts[ref]))
        material = _as_text(raw.get("material"))
        if material:
            passage_parts.append(material)
        passage = "\n\n".join(p for p in passage_parts if p) or None

        problem = _validate(kind, options, match_left, correct, answers)
        if problem:
            result.errors.append(f"{where}: {problem}")
            continue

        result.tasks.append(
            ImportTask(
                number=number,
                kind=kind,
                text=text,
                passage=passage,
                options=options,
                match_left=match_left,
                correct=correct,
                answers=answers,
            )
        )
    return result


def _validate(kind, options, match_left, correct, answers) -> str | None:
    """Проверяет, что для своего вида задание заполнено полностью и непротиворечиво."""
    if kind == KIND_CHOICE:
        if len(options) < 2:
            return "меньше двух вариантов ответа"
        if not correct:
            return "не указан правильный ответ"
        bad = [c for c in correct if not 1 <= c <= len(options)]
        if bad:
            return f"в correct есть {bad}, а вариантов всего {len(options)}"
    elif kind == KIND_MATCH:
        if not match_left:
            return "пустой левый столбец (match_left)"
        if len(options) < 2:
            return "пустой правый столбец (options)"
        if len(correct) != len(match_left):
            return f"в correct {len(correct)} значений, а слева {len(match_left)} позиций"
        bad = [c for c in correct if not 1 <= c <= len(options)]
        if bad:
            return f"в correct есть {bad}, а справа всего {len(options)} позиций"
    elif kind in (KIND_OPEN, KIND_DIGITS):
        if not answers:
            return "не перечислены допустимые ответы (answers)"
        if kind == KIND_DIGITS and not all(any(ch.isdigit() for ch in a) for a in answers):
            return f"вид digits, но в answers нет цифр: {answers}"
    return None


def to_parsed(task: ImportTask) -> ParsedTask:
    """Приводит к структуре, которую понимает слой БД.

    В базе индексы вариантов нумеруются с нуля, а источник — с единицы.
    """
    return ParsedTask(
        number=task.number,
        text=task.text,
        options=task.options,
        correct=[c - 1 for c in task.correct] if task.kind == KIND_CHOICE else list(task.correct),
    )


def fingerprint(task: ImportTask) -> str:
    """Отпечаток по содержимому — защита от повторной заливки без ID источника."""
    parts = [str(task.number), task.kind, " ".join(task.text.lower().split())]
    parts += [" ".join(o.lower().split()) for o in task.options]
    parts += [" ".join(o.lower().split()) for o in task.match_left]
    return "|".join(parts)


async def do_import(tasks: list[ImportTask], variant_number: int | None) -> None:
    await init_db()
    created: list[tuple[int, str]] = []
    duplicates = 0

    async with SessionMaker() as db:
        # Собираем отпечатки уже залитых заданий тех же номеров.
        known: dict[str, str] = {}
        for number in sorted({t.number for t in tasks}):
            for existing in await crud.list_tasks(db, number, limit=100_000):
                key = "|".join([
                    str(existing.number),
                    existing.kind,
                    " ".join((existing.text or "").lower().split()),
                    *[" ".join(str(o).lower().split()) for o in (existing.options or [])],
                    *[" ".join(str(o).lower().split()) for o in (existing.match_left or [])],
                ])
                known[key] = existing.id

        for task in tasks:
            key = fingerprint(task)
            if key in known:
                duplicates += 1
                continue
            saved = await crud.create_task(db, to_parsed(task))
            # Поля, которых нет в шаблоне админ-бота, проставляем отдельно.
            saved.kind = task.kind
            saved.passage = task.passage
            saved.match_left = task.match_left or None
            saved.answers = task.answers or None
            await db.commit()
            known[key] = saved.id
            created.append((task.number, saved.id))

        if variant_number is not None:
            slots = {number: task_id for number, task_id in created}
            if len(slots) < len(created):
                print("Внимание: в файле несколько заданий с одним номером, вариант не собран.")
            elif len(slots) < LAST_TASK:
                print(
                    f"Внимание: залито {len(slots)} номеров из {LAST_TASK}, "
                    "вариант собирать не из чего."
                )
            else:
                existing = await crud.get_variant_by_number(db, variant_number)
                if existing:
                    await crud.replace_variant_items(db, existing, slots)
                    print(f"Вариант №{variant_number} обновлён.")
                else:
                    await crud.create_variant(db, variant_number, slots)
                    print(f"Вариант №{variant_number} собран.")

        total = await crud.total_tasks_count(db)

    print(f"\nДобавлено заданий: {len(created)}")
    if duplicates:
        print(f"Пропущено как уже залитые: {duplicates}")
    print(f"Всего в базе: {total}")


def collect_files(target: Path) -> list[Path]:
    if target.is_dir():
        return sorted(p for p in target.iterdir() if p.is_file())
    return [target]


async def main() -> None:
    parser = argparse.ArgumentParser(description="Импорт варианта ЕГЭ из JSON")
    parser.add_argument("path", help="файл или папка с JSON-вариантами")
    parser.add_argument("--dry-run", action="store_true", help="только проверить")
    parser.add_argument("--variant", type=int, help="собрать из залитых заданий вариант с этим номером")
    args = parser.parse_args()

    target = Path(args.path)
    if not target.exists():
        raise SystemExit(f"Не найдено: {target}")

    all_tasks: list[ImportTask] = []
    all_errors: list[str] = []
    for path in collect_files(target):
        loaded = load_file(path)
        all_tasks.extend(loaded.tasks)
        all_errors.extend(loaded.errors)
        print(f"{path.name}: разобрано {len(loaded.tasks)}, ошибок {len(loaded.errors)}")

    if all_errors:
        print(f"\nОшибок: {len(all_errors)}")
        for error in all_errors[:30]:
            print(f"  {error}")
        if len(all_errors) > 30:
            print(f"  ... и ещё {len(all_errors) - 30}")
        print("\nНичего не залито — исправьте файл и запустите снова.")
        return

    by_kind: dict[str, int] = {}
    for task in all_tasks:
        by_kind[task.kind] = by_kind.get(task.kind, 0) + 1
    print("\nПо видам: " + ", ".join(f"{k} — {v}" for k, v in sorted(by_kind.items())))
    print("Номера: " + ", ".join(str(t.number) for t in sorted(all_tasks, key=lambda x: x.number)))

    if args.dry_run:
        print("\nПроверка пройдена, база не тронута (--dry-run).")
        return

    try:
        await do_import(all_tasks, args.variant)
    finally:
        await dispose_db()


if __name__ == "__main__":
    asyncio.run(main())
