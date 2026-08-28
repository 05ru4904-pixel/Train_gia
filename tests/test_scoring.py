"""Тесты проверки ответов и подсчёта баллов. Запуск: python tests/test_scoring.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import scoring  # noqa: E402
from core.tasks_meta import TASK_NUMBERS  # noqa: E402


def test_single_answer():
    assert scoring.evaluate([0], [0]) is True
    assert scoring.evaluate([1], [0]) is False


def test_full_set_required():
    """Неполный набор и лишний вариант одинаково считаются ошибкой (ТЗ п.5)."""
    correct = [0, 2, 4]
    assert scoring.evaluate([0, 2, 4], correct) is True
    assert scoring.evaluate([4, 0, 2], correct) is True, "порядок не важен"
    assert scoring.evaluate([0, 2], correct) is False, "неполный набор"
    assert scoring.evaluate([0, 2, 4, 1], correct) is False, "лишний вариант"
    assert scoring.evaluate([], correct) is False


def test_duplicates_ignored():
    assert scoring.evaluate([0, 0, 2], [0, 2]) is True


def test_task_weights():
    assert scoring.max_points(1) == 1
    assert scoring.max_points(8) == 5
    assert scoring.max_points(16) == 2
    assert scoring.max_points(26) == 4
    assert scoring.MAX_RAW_SCORE == sum(scoring.max_points(n) for n in TASK_NUMBERS)


def test_award_is_all_or_nothing():
    assert scoring.award_points(8, True) == 5
    assert scoring.award_points(8, False) == 0, "частичное начисление пока не введено"
    assert scoring.award_points(3, True) == 1
    assert scoring.award_points(3, False) == 0


def test_raw_score():
    results = {1: True, 8: True, 16: False, 26: True}   # 1 + 5 + 0 + 4
    assert scoring.raw_score(results) == 10
    assert scoring.raw_score({}) == 0


def test_test_score_bounds_and_monotonicity():
    assert scoring.test_score(0) == 0
    assert scoring.test_score(-5) == 0, "отрицательный балл обрезается"
    assert scoring.test_score(scoring.MAX_RAW_SCORE + 100) == scoring.test_score(scoring.MAX_RAW_SCORE)
    previous = -1
    for raw in range(scoring.MAX_RAW_SCORE + 1):
        current = scoring.test_score(raw)
        assert current >= previous, f"шкала не монотонна на {raw}"
        assert 0 <= current <= 100
        previous = current


def test_official_table_is_used_when_filled():
    """Как только словарь заполнят, линейная заглушка выключается."""
    original = dict(scoring.RAW_TO_TEST_SCORE)
    try:
        scoring.RAW_TO_TEST_SCORE.update({0: 0, 10: 33, 20: 67, scoring.MAX_RAW_SCORE: 72})
        assert scoring.is_official_table_configured() is True
        assert scoring.test_score(10) == 33
        assert scoring.test_score(15) == 33, "промежуточный балл берёт ближайший меньший"
        assert scoring.test_score(20) == 67
    finally:
        scoring.RAW_TO_TEST_SCORE.clear()
        scoring.RAW_TO_TEST_SCORE.update(original)
    assert scoring.is_official_table_configured() is False


def test_accuracy():
    assert scoring.accuracy(9, 12) == 75
    assert scoring.accuracy(0, 12) == 0
    assert scoring.accuracy(12, 12) == 100
    assert scoring.accuracy(0, 0) == 0, "деления на ноль быть не должно"
    assert scoring.accuracy(1, 3) == 33


def test_open_answer_is_forgiving_about_form():
    """Ученик пишет ответ руками — регистр, пробелы и ё считать ошибкой нельзя."""
    answers = ["которой", "который"]
    for typed in ("которой", "Которой", "  которой  ", "КОТОРОЙ"):
        assert scoring.evaluate_open(typed, answers) is True, typed
    assert scoring.evaluate_open("который", answers) is True, "принимается любая форма из списка"
    assert scoring.evaluate_open("которая", answers) is False
    assert scoring.evaluate_open("", answers) is False
    assert scoring.evaluate_open("которой", []) is False


def test_open_answer_ignores_spaces_and_yo():
    assert scoring.evaluate_open("сделала усилие", ["сделалаусилие"]) is True
    assert scoring.evaluate_open("сделалаусилие", ["сделала усилие"]) is True
    assert scoring.evaluate_open("тюлем", ["тюлём"]) is True, "ё и е не различаем"


def test_digits_ignore_order():
    """На бланке ЕГЭ цифры можно записать в любом порядке."""
    assert scoring.evaluate_digits("1234", ["1234"]) is True
    assert scoring.evaluate_digits("4321", ["1234"]) is True
    assert scoring.evaluate_digits("2 4", ["24"]) is True, "пробелы не мешают"
    assert scoring.evaluate_digits("124", ["1234"]) is False, "цифры пропущены"
    assert scoring.evaluate_digits("12345", ["1234"]) is False, "лишняя цифра"
    assert scoring.evaluate_digits("", ["1234"]) is False
    assert scoring.evaluate_digits("абв", ["1234"]) is False


def test_match_requires_exact_order():
    """В соответствии позиция каждой буквы своя — порядок и есть ответ."""
    correct = [3, 6, 1, 9, 5]
    assert scoring.evaluate_match([3, 6, 1, 9, 5], correct) is True
    assert scoring.evaluate_match([6, 3, 1, 9, 5], correct) is False, "переставлено"
    assert scoring.evaluate_match([3, 6, 1, 9], correct) is False, "не заполнено до конца"
    assert scoring.evaluate_match([], correct) is False


def test_check_answer_dispatches_by_kind():
    assert scoring.check_answer("choice", [0, 2], [0, 2], None) is True
    assert scoring.check_answer("choice", [0], [0, 2], None) is False
    assert scoring.check_answer("open", "тюлем", [], ["тюлем"]) is True
    assert scoring.check_answer("digits", "21", [], ["12"]) is True
    assert scoring.check_answer("match", [1, 2], [1, 2], None) is True
    assert scoring.check_answer("match", [2, 1], [1, 2], None) is False
    # пустой ответ не должен случайно засчитаться ни в одном виде
    for kind in ("choice", "open", "digits", "match"):
        assert scoring.check_answer(kind, None, [1], ["1"]) is False, kind


def test_regulations():
    assert scoring.VARIANT_TIME_LIMIT_SEC == 210 * 60
    assert scoring.TRAINING_COUNTS == (6, 9, 12, 15)
    assert scoring.VARIANT_TASK_COUNT == 26


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
