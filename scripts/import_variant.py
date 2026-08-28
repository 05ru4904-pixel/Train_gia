"""Импорт варианта из JSON в базу.

    python scripts/import_variant.py Задания/2.json --dry-run   # только проверить
    python scripts/import_variant.py Задания/2.json             # проверить и залить
    python scripts/import_variant.py Задания/                   # всю папку
    python scripts/import_variant.py Задания/2.json --variant 2 # собрать ещё и вариант

JSON готовит `scripts/parse_raw.py` из сырого текста с сайта.

Проверка и заливка живут в `core/variant_import.py` — там же, откуда их берёт
админ-бот. Здесь только разбор аргументов и печать.

Повторный запуск на том же файле ничего не задвоит: задание опознаётся по своему
содержимому, потому что ID источника мы намеренно не храним.
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.variant_import import ImportTask, import_tasks, load_file  # noqa: E402
from db.database import dispose_db, init_db  # noqa: E402


def collect_files(target: Path) -> list[Path]:
    if target.is_dir():
        # В папке лежат и сырые исходники, и результаты разбора — берём вторые.
        return sorted(p for p in target.iterdir() if p.is_file() and p.suffix == ".json")
    return [target]


async def main() -> None:
    parser = argparse.ArgumentParser(description="Импорт варианта ЕГЭ из JSON")
    parser.add_argument("path", help="файл или папка с JSON-вариантами")
    parser.add_argument("--dry-run", action="store_true", help="только проверить")
    parser.add_argument(
        "--variant", type=int, help="собрать из залитых заданий вариант с этим номером"
    )
    args = parser.parse_args()

    target = Path(args.path)
    if not target.exists():
        raise SystemExit(f"Не найдено: {target}")

    files = collect_files(target)
    if not files:
        raise SystemExit(f"В {target} нет ни одного .json — сначала прогоните parse_raw.py")

    all_tasks: list[ImportTask] = []
    all_errors: list[str] = []
    for path in files:
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

    await init_db()
    try:
        report = await import_tasks(all_tasks, args.variant)
    finally:
        await dispose_db()

    for warning in report.warnings:
        print(f"Внимание: {warning}")
    if report.variant_status:
        print(f"Вариант №{report.variant_number} {report.variant_status}.")
    print(f"\nДобавлено заданий: {len(report.created)}")
    if report.duplicates:
        print(f"Пропущено как уже залитые: {report.duplicates}")
    print(f"Всего в базе: {report.total_in_db}")


if __name__ == "__main__":
    asyncio.run(main())
