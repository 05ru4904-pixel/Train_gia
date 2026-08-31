"""Разбор одного задания, присланного в админ-бота текстом (`/add`).

Логика общая с разбором целого варианта: вид берётся из номера задания, ответ — из
пояснения, при любой неоднозначности задание не собирается. Здесь проверяется, что
общий код одинаково берёт и копию с сайта, и набранное руками.

Запуск: python tests/test_raw_variant.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.raw_variant import parse_one  # noqa: E402
from core.tasks_meta import (  # noqa: E402
    KIND_CHOICE,
    KIND_DIGITS,
    KIND_MATCH,
    KIND_OPEN,
)

FROM_SITE = """↑ **Задание 5 № 4548 тип 5 (ре­ше­но не­вер­но или не ре­ше­но)**

В одном из приведённых ниже предложений НЕВЕРНО употреблено выделенное слово.
Запишите это слово.

**Пояснение.**

Ответ: заклятым ИЛИ заклятый"""

MANUAL_CHOICE = """Задание 4
В каком слове правильно поставлено ударение? Выпишите это слово.
1) звонИт
2) звОнит
3) позвОнит
Ответ: 1"""

MANUAL_DIGITS = """Задание 15
Укажите цифру(-ы), на месте которой(-ых) пишется НН.
Ответ: 134
Текст: Дли(1)ая мощё(2)ая дорога вела к стари(3)ому дому."""

MANUAL_MATCH = """Задание 8
Установите соответствие между ошибками и предложениями.
А) деепричастный оборот
Б) падежная форма
В) подлежащее и сказуемое
Г) косвенная речь
Д) причастный оборот
1) Приехав в город, мне понравились улицы.
2) Согласно расписания поезд уходит в семь.
3) Все, кто читал повесть, помнят финал.
4) Он сказал, что я приду завтра.
5) Книга, лежащая на столе, моя.
Ответ: 12345"""


def only_task(raw: str) -> dict:
    result = parse_one(raw)
    assert not result.problems, [str(p) for p in result.problems]
    assert len(result.tasks) == 1
    return result.tasks[0]


def test_copied_from_site_keeps_source_id():
    task = only_task(FROM_SITE)
    assert task["number"] == 5
    assert task["kind"] == KIND_OPEN
    assert task["source_id"] == 4548, "ID источника обязан доехать — по нему ищутся дубли"
    assert task["answers"] == ["заклятым", "заклятый"]


def test_manual_choice_without_source_id():
    task = only_task(MANUAL_CHOICE)
    assert task["kind"] == KIND_CHOICE
    assert task["source_id"] is None, "у набранного руками ID источника нет"
    assert task["options"] == ["звонИт", "звОнит", "позвОнит"]
    assert task["correct"] == [1]


def test_kind_comes_from_number_not_from_shape():
    """№15 — цифры, хотя по форме сообщения он неотличим от «вписать слово»."""
    assert only_task(MANUAL_DIGITS)["kind"] == KIND_DIGITS
    assert only_task(MANUAL_CHOICE)["kind"] == KIND_CHOICE
    assert only_task(MANUAL_MATCH)["kind"] == KIND_MATCH


def test_manual_material_goes_to_passage():
    """«Текст:» пишут последним, после ответа, — материал не должен уехать в пояснение."""
    result = parse_one(MANUAL_DIGITS)
    task = result.tasks[0]
    assert task["answers"] == ["134"]
    assert task["text_ref"] == "t1"
    assert "стари(3)ому дому" in result.texts["t1"]
    assert "Ответ" not in result.texts["t1"]


def test_manual_match_columns():
    task = only_task(MANUAL_MATCH)
    assert len(task["match_left"]) == 5
    assert len(task["options"]) == 5
    assert task["correct"] == [1, 2, 3, 4, 5]


def problems_of(raw: str) -> str:
    result = parse_one(raw)
    assert result.problems, "ожидался отказ разбора"
    assert not result.tasks, "при проблемах задание не собирается"
    return " | ".join(str(p) for p in result.problems)


def test_no_header_is_refused():
    assert "шапкой" in problems_of("В каком слове ударение?\n1) а\n2) б\nОтвет: 1")


def test_no_answer_is_refused():
    assert "Ответ" in problems_of("Задание 4\nУкажите слово.\n1) а\n2) б")


def test_two_tasks_in_one_message_are_refused():
    raw = MANUAL_CHOICE + "\n\nЗадание 5\nЗапишите слово.\nОтвет: дом"
    assert "больше одного задания" in problems_of(raw)


def test_number_out_of_range():
    assert "вне диапазона" in problems_of("Задание 99\nУкажите слово.\nОтвет: дом")


def test_declared_type_must_match():
    """Сайт печатает номер дважды — расхождение значит, что шапка разобрана неверно."""
    raw = FROM_SITE.replace("тип 5", "тип 6")
    assert "не совпал" in problems_of(raw)


def test_choice_answer_outside_options():
    raw = "Задание 4\nУкажите слово.\n1) а\n2) б\nОтвет: 5"
    assert "а вариантов всего 2" in problems_of(raw)

# ---------------------------------------------------------------------------
# Ответ задания с цифрами: берём из строки только цифры
# ---------------------------------------------------------------------------
def digits_answer(answer: str) -> list[str]:
    raw = f"Задание 15\nУкажите цифры, на месте которых пишется НН.\nОтвет: {answer}"
    return only_task(raw)["answers"]


def test_plain_sequence():
    assert digits_answer("32465") == ["32465"]


def test_source_notes_are_dropped():
    """Приписка источника без цифр — не форма ответа, и вариант из-за неё не падает."""
    assert digits_answer("25; порядок не важен") == ["25"]
    assert digits_answer("34 ИЛИ 43 в любом порядке") == ["34"]
    assert digits_answer("1234 (цифры в любой последовательности)") == ["1234"]
    assert digits_answer("2 и 4") == ["24"]


def test_permutations_collapse():
    """Перестановки одного набора — одна форма: порядок всё равно не проверяется."""
    assert digits_answer("145, 541") == ["145"]
    assert digits_answer("35 или 53") == ["35"]
    assert digits_answer("34|43") == ["34"]


def test_comma_lists_digits_of_one_answer():
    """«3, 5» — это один ответ «35», а не «верно 3 или верно 5»."""
    assert digits_answer("3, 5") == ["35"]


def test_real_alternatives_are_kept():
    """Разные наборы через ИЛИ — действительно разные допустимые ответы."""
    assert digits_answer("12 ИЛИ 34") == ["12", "34"]


def test_answer_without_digits_is_refused():
    assert "цифр в ответе нет" in problems_of(
        "Задание 15\nУкажите цифры, на месте которых пишется НН.\nОтвет: порядок любой"
    )


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok  {name}")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"  FAIL {name}: {exc}")
    print("провалено:", failed)
    sys.exit(1 if failed else 0)
