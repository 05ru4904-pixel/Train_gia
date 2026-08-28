"""Сырой вариант с РЕШУ ЕГЭ -> JSON для import_variant.py.

Пользователь копирует вариант со страницы «как есть» и кладёт файл в `Задания/`.

    python scripts/parse_raw.py Задания/2                  # -> Задания/2.json
    python scripts/parse_raw.py Задания/2 -o готово.json
    python scripts/parse_raw.py Задания/2 --report         # подробности по каждому

Разбор живёт в `core/raw_variant.py` — там же, откуда его берёт админ-бот. Здесь
только чтение файла, печать отчёта и запись результата.

При любой проблеме JSON не пишется вовсе: лучше остановиться, чем тихо испортить
данные (см. историю с №13 в CLAUDE.md).
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.raw_variant import parse  # noqa: E402
from core.tasks_meta import KIND_CHOICE, KIND_MATCH, LAST_TASK  # noqa: E402


def print_report(tasks: list[dict]) -> None:
    for task in tasks:
        if task["kind"] == KIND_CHOICE:
            shape = f"вариантов {len(task['options'])}, ответ {task['correct']}"
        elif task["kind"] == KIND_MATCH:
            shape = f"{len(task['match_left'])}x{len(task['options'])}, ответ {task['correct']}"
        else:
            shape = f"ответ {task['answers']}"
        text_ref = f"текст {task['text_ref']}" if task["text_ref"] else ""
        print(f"  №{task['number']:<3} {task['kind']:<7} {text_ref:<9} {shape}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Сырой вариант с РЕШУ ЕГЭ -> JSON для import_variant.py"
    )
    parser.add_argument("path", help="файл с сырым текстом варианта")
    parser.add_argument("-o", "--out", help="куда записать JSON (по умолчанию рядом, с .json)")
    parser.add_argument("--report", action="store_true", help="показать разбор по каждому заданию")
    args = parser.parse_args()

    source = Path(args.path)
    if not source.exists():
        raise SystemExit(f"Не найдено: {source}")

    try:
        raw = source.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise SystemExit(
            f"{source.name}: файл не в кодировке UTF-8. "
            "Пересохраните его как UTF-8 и повторите."
        )

    result = parse(raw)

    print(f"{source.name}: разобрано заданий {len(result.tasks)} из {LAST_TASK}")
    if args.report and result.tasks:
        print_report(result.tasks)
    if result.texts:
        sizes = ", ".join(f"{k} — {len(v)} симв" for k, v in result.texts.items())
        print(f"Тексты: {sizes}")
    for note in result.notes:
        print(f"  ! {note}")

    if result.problems:
        print(f"\nПроблем: {len(result.problems)}")
        for problem in result.problems:
            print(f"  {problem}")
        print("\nJSON не записан — исправьте исходник и запустите снова.")
        raise SystemExit(1)

    target = Path(args.out) if args.out else source.with_suffix(".json")
    target.write_text(
        json.dumps(result.payload(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\nЗаписано: {target}")
    print(f"Дальше:   python scripts/import_variant.py {target} --dry-run")


if __name__ == "__main__":
    main()
