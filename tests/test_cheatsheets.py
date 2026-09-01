"""Раздел «Шпаргалки»: список заданий и чтение файлов. Запуск: python tests/test_cheatsheets.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import cheatsheets  # noqa: E402
from core.tasks_meta import TASK_NUMBERS, title  # noqa: E402


def test_index_lists_every_task():
    """В списке все 26 номеров, а не только написанные: ученик видит карту раздела."""
    items = cheatsheets.index()
    assert [item["number"] for item in items] == list(TASK_NUMBERS)
    assert items[3]["title"] == title(4)
    assert all("ready" in item for item in items)


def test_written_sheets_are_found():
    ready = [item["number"] for item in cheatsheets.index() if item["ready"]]
    assert ready, "должна быть хотя бы одна написанная шпаргалка"
    for number in ready:
        body = cheatsheets.body(number)
        assert body and len(body) > 200, f"№{number}: шпаргалка подозрительно короткая"
        assert "## Как решать" in body, f"№{number}: нет раздела «Как решать»"
        assert cheatsheets.has(number) is True


def test_missing_sheet_is_not_an_error():
    empty = [item["number"] for item in cheatsheets.index() if not item["ready"]]
    if empty:
        assert cheatsheets.body(empty[0]) is None
        assert cheatsheets.has(empty[0]) is False


def test_ready_count_matches_index():
    items = cheatsheets.index()
    assert cheatsheets.ready_count() == len([i for i in items if i["ready"]])


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok  {name}")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"FAIL  {name}: {exc}")
    print("--- провалено:", failed)
    sys.exit(1 if failed else 0)
