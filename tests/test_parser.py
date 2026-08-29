"""Тесты разбора шаблонов админ-бота. Запуск: python tests/test_parser.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.parser import (  # noqa: E402
    ID_LENGTH,
    ParsedTask,
    ParseError,
    generate_task_id,
    normalize_task_id,
    parse_task,
    parse_task_batch,
    parse_variant,
    to_template,
)
from core.tasks_meta import (  # noqa: E402
    KIND_CHOICE,
    KIND_DIGITS,
    KIND_MATCH,
    KIND_OPEN,
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


# --------------------------------------------------------------------------- #
# Задания с вписыванием ответа
# --------------------------------------------------------------------------- #
def test_open_task():
    task = parse_task(
        "Задание №5\n"
        "Отредактируйте предложение: исправьте лексическую ошибку.\n"
        "Ответ: заклятым, заклятый"
    )
    assert task.kind == KIND_OPEN
    assert task.answers == ["заклятым", "заклятый"]
    assert task.options == [] and task.correct == []


def test_open_answer_with_spaces_is_not_split():
    """«не столько планы, сколько нас» — одна форма, а не две."""
    task = parse_task("Задание №25\nНайдите предложение.\nОтвет: не столько планы, сколько нас")
    assert task.answers == ["не столько планы, сколько нас"]


def test_digits_task():
    task = parse_task(
        "Задание №15\nУкажите все цифры, на месте которых пишется НН.\nОтвет: 134"
    )
    assert task.kind == KIND_DIGITS
    assert task.answers == ["134"]


def test_kind_comes_from_number_not_from_answer():
    """У №25 ответ — номер предложения, но сверяется он как слово (так же, как при
    заливке варианта). Иначе «34» и «43» стали бы одинаковыми."""
    task = parse_task("Задание №25\nНайдите предложение.\nОтвет: 34")
    assert task.kind == KIND_OPEN


def test_passage_tail():
    task = parse_task(
        "Задание №15\nУкажите все цифры.\nОтвет: 12\n"
        "Текст: Дли(1)ая дорога.\nВторая строка текста."
    )
    assert task.passage == "Дли(1)ая дорога.\nВторая строка текста."
    assert task.text == "Укажите все цифры."


def test_single_letter_line_stays_in_open_task():
    """«А. Пушкин» в условии не должно превратить задание с вписыванием в выбор."""
    task = parse_task("Задание №5\nКого имел в виду А. Пушкин?\nОтвет: заклятым")
    assert task.kind == KIND_OPEN
    assert task.text == "Кого имел в виду А. Пушкин?"


# --------------------------------------------------------------------------- #
# Задания на соответствие
# --------------------------------------------------------------------------- #
MATCH = """Задание №8
Установите соответствие между ошибками и предложениями.
А) нарушение с деепричастным оборотом
Б) ошибка в падежной форме
1) Приехав в город, мне понравились улицы.
2) Все, кто читал, помнят финал.
3) Согласно расписания поезд уходит в семь.
Ответ: А-1, Б-3"""


def test_match_task():
    task = parse_task(MATCH)
    assert task.kind == KIND_MATCH
    assert task.match_left == [
        "нарушение с деепричастным оборотом",
        "ошибка в падежной форме",
    ]
    assert len(task.options) == 3
    assert task.correct == [1, 3]


def test_match_answer_order_does_not_matter():
    """Порядок пар в ответе — дело админа, порядок в базе задаётся буквами."""
    assert parse_task(MATCH.replace("Ответ: А-1, Б-3", "Ответ: Б-3, А-1")).correct == [1, 3]


def test_match_accepts_plain_digits():
    """На бланке ЕГЭ ответ пишут одной строкой цифр — принимаем и так."""
    assert parse_task(MATCH.replace("Ответ: А-1, Б-3", "Ответ: 13")).correct == [1, 3]


def test_match_errors():
    _expect_error(MATCH.replace("Ответ: А-1, Б-3", "Ответ: А-1"), "не хватает позиций")
    _expect_error(MATCH.replace("Ответ: А-1, Б-3", "Ответ: А-1, Б-9"), "справа всего 3")
    _expect_error(MATCH.replace("Ответ: А-1, Б-3", "Ответ: А-1, Д-2"), "слева всего 2")
    _expect_error(MATCH.replace("Ответ: А-1, Б-3", "Ответ: А-1, А-3"), "дважды")


# --------------------------------------------------------------------------- #
# Обратная сборка: задание -> шаблон -> задание
# --------------------------------------------------------------------------- #
ROUND_TRIP = [
    ParsedTask(
        number=4, text="В каком слове ударение?",
        options=["звонИт", "звОнит"], correct=[0], kind=KIND_CHOICE,
    ),
    ParsedTask(
        number=5, text="Исправьте лексическую ошибку.", options=[], correct=[],
        kind=KIND_OPEN, answers=["заклятым", "заклятый"],
        passage="Он всегда был моим заклятым врагом.",
    ),
    ParsedTask(
        number=25, text="Найдите предложение.", options=[], correct=[],
        kind=KIND_OPEN, answers=["не столько планы, сколько нас"],
    ),
    ParsedTask(
        number=15, text="Укажите все цифры.", options=[], correct=[],
        kind=KIND_DIGITS, answers=["134"], passage="Дли(1)ая мощё(2)ая дорога.",
    ),
    ParsedTask(
        number=8, text="Установите соответствие.",
        options=["первое", "второе", "третье"], correct=[3, 1],
        kind=KIND_MATCH, match_left=["нарушение", "ошибка"],
    ),
]


def test_round_trip_keeps_every_field():
    """Правка через админ-бота — это шаблон -> разбор. Потеря любого поля здесь
    означает, что после правки задание в базе станет неполным."""
    for original in ROUND_TRIP:
        again = parse_task(to_template(original))
        assert again == original, f"№{original.number} ({original.kind}): {again} != {original}"


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
