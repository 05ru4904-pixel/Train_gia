"""Тесты разбора шаблонов админ-бота. Запуск: python tests/test_parser.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.parser import (  # noqa: E402
    ID_LENGTH,
    ParseError,
    generate_task_id,
    normalize_task_id,
    parse_task,
    parse_task_batch,
    parse_variant,
)

SIMPLE = """Задание №4
В каком слове правильно поставлено ударение?
А) звонИт
Б) звОнит
В) позвонИт
Г) позвОнит
Ответ: А"""


def test_simple_task():
    task = parse_task(SIMPLE)
    assert task.number == 4
    assert task.text == "В каком слове правильно поставлено ударение?"
    assert task.options == ["звонИт", "звОнит", "позвонИт", "позвОнит"]
    assert task.correct == [0]
    assert task.correct_letters == "А"


def test_multiple_correct_answers():
    task = parse_task(
        "Задание №2\n"
        "Укажите варианты ответов.\n"
        "А) первый\nБ) второй\nВ) третий\nГ) четвёртый\nД) пятый\n"
        "Ответ: А, В, Д"
    )
    assert task.correct == [0, 2, 4]
    assert task.correct_letters == "А, В, Д"


def test_six_options_supported():
    task = parse_task(
        "Задание №26\nТермины и примеры.\n"
        "А) 1\nБ) 2\nВ) 3\nГ) 4\nД) 5\nЕ) 6\nОтвет: Б, Е"
    )
    assert len(task.options) == 6
    assert task.correct == [1, 5]


def test_latin_homoglyphs_are_fixed():
    """Админ набрал латинские A, B, C вместо кириллических."""
    task = parse_task(
        "Задание №5\nПаронимы.\nA) один\nB) два\nC) три\nОтвет: C"
    )
    assert task.options == ["один", "два", "три"]
    assert task.correct == [2]


def test_numeric_answer_accepted():
    task = parse_task("Задание №7\nФормы слова.\nА) а\nБ) б\nВ) в\nОтвет: 1, 3")
    assert task.correct == [0, 2]


def test_multiline_question_text():
    task = parse_task(
        "Задание №1\nПрочитайте текст.\nОпределите стиль.\nА) научный\nБ) разговорный\nОтвет: А"
    )
    assert task.text == "Прочитайте текст.\nОпределите стиль."


def test_header_variations():
    for header in ("Задание №4", "задание 4", "№4", "Задание No 4", "4."):
        task = parse_task(f"{header}\nВопрос?\nА) а\nБ) б\nОтвет: А")
        assert task.number == 4, header


def test_initial_in_question_is_not_an_option():
    """«А. Пушкин» в условии не должно стать вариантом ответа."""
    task = parse_task(
        "Задание №24\nКого имел в виду А. Пушкин?\nА) первого\nБ) второго\nОтвет: Б"
    )
    assert task.text == "Кого имел в виду А. Пушкин?"
    assert len(task.options) == 2


def test_skipped_letter_raises():
    """Пропущенная «В» — ошибка, а не тихая склейка строк."""
    try:
        parse_task("Задание №4\nВопрос?\nА) а\nБ) б\nГ) г\nОтвет: А")
    except ParseError as exc:
        assert "не подряд" in str(exc)
    else:
        raise AssertionError("ожидалась ParseError")


def _expect_error(raw: str, fragment: str):
    try:
        parse_task(raw)
    except ParseError as exc:
        assert fragment in str(exc), f"ожидал «{fragment}», получил «{exc}»"
    else:
        raise AssertionError(f"ожидалась ParseError для: {raw!r}")


def test_errors_are_readable():
    _expect_error("привет\nА) а\nБ) б\nОтвет: А", "номер задания")
    _expect_error("Задание №99\nВопрос?\nА) а\nБ) б\nОтвет: А", "от 1 до 26")
    _expect_error("Задание №4\nВопрос?\nА) а\nОтвет: А", "минимум 2 варианта")
    _expect_error("Задание №4\nВопрос?\nА) а\nБ) б", "строку с ответом")
    _expect_error("Задание №4\nВопрос?\nА) а\nБ) б\nОтвет: Ж", "такого варианта нет")
    _expect_error("Задание №4\nВопрос?\nА) а\nБ) б\nОтвет: А, А", "дважды")
    _expect_error("Задание №4\nА) а\nБ) б\nОтвет: А", "текст задания")


def test_generated_id_format():
    ids = {generate_task_id() for _ in range(200)}
    assert len(ids) > 190, "ID слишком часто повторяются"
    # Проверяем принадлежность алфавиту, а не isupper(): ID вида «359582» состоит
    # из одних цифр, и isupper() для него False — букв в строке просто нет.
    allowed = set("ABCDEFGHJKLMNPQRSTUVWXYZ23456789")
    for task_id in ids:
        assert len(task_id) == ID_LENGTH, task_id
        assert set(task_id) <= allowed, f"{task_id}: недопустимые символы"


def test_normalize_task_id():
    assert normalize_task_id(" k7f29a ") == "K7F29A"
    assert normalize_task_id("А72К91") == "A72K91"  # кириллица -> латиница


def test_parse_variant():
    variant = parse_variant("Вариант №10\n1: A72K91\n2: B82L43\n26: M72P81")
    assert variant.number == 10
    assert variant.task_ids == {1: "A72K91", 2: "B82L43", 26: "M72P81"}


def test_variant_errors():
    for raw, fragment in (
        ("1: A72K91", "Вариант №10"),
        ("Вариант №1\nмусор", "Не понял строку"),
        ("Вариант №1\n27: A72K91", "от 1 до 26"),
        ("Вариант №1\n1: A72K91\n1: B82L43", "дважды"),
        ("Вариант №1", "ни одного задания"),
    ):
        try:
            parse_variant(raw)
        except ParseError as exc:
            assert fragment in str(exc), f"ожидал «{fragment}», получил «{exc}»"
        else:
            raise AssertionError(f"ожидалась ParseError для: {raw!r}")


def test_parse_batch():
    tasks = parse_task_batch(
        "Задание №1\nВопрос 1?\nА) а\nБ) б\nОтвет: А\n\n"
        "Задание №2\nВопрос 2?\nА) а\nБ) б\nВ) в\nОтвет: В"
    )
    assert [t.number for t in tasks] == [1, 2]
    assert tasks[1].correct == [2]


def test_batch_error_points_to_block():
    try:
        parse_task_batch("Задание №1\nВопрос?\nА) а\nБ) б\nОтвет: А\n\nЗадание №2\nВопрос?\nА) а\nОтвет: А")
    except ParseError as exc:
        assert "Блок №2" in str(exc)
    else:
        raise AssertionError("ожидалась ParseError")


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
