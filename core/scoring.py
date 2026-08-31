"""Проверка ответов и подсчёт баллов. Чистые функции — без БД и сети.

Здесь собрано всё, что зависит от регламента ЕГЭ. Меняется регламент — правится
только этот файл (и тесты в tests/test_scoring.py).
"""
import re

from core.tasks_meta import KIND_DIGITS, KIND_MATCH, KIND_OPEN, LAST_TASK, TASK_NUMBERS

# ---------------------------------------------------------------------------
# Вес заданий в первичных баллах
# ---------------------------------------------------------------------------
# Официальная таблица заказчика (state.md): все задания по 1 баллу, кроме заданий
# на соответствие — №8 и №22, за них по 2. Максимум за тестовую часть — 28.
TASK_MAX_POINTS: dict[int, int] = {n: 1 for n in TASK_NUMBERS}
TASK_MAX_POINTS[8] = 2
TASK_MAX_POINTS[22] = 2

# Максимум первичных баллов за тестовую часть (№1-26, без сочинения №27).
MAX_RAW_SCORE = sum(TASK_MAX_POINTS.values())

# Задания на соответствие, где балл даётся и за частично верный ответ: ошибка на
# одной или двух позициях из пяти — 1 балл, больше двух — 0.
PARTIAL_TASKS = (8, 22)
MAX_PARTIAL_MISTAKES = 2

# ---------------------------------------------------------------------------
# Перевод первичного балла в тестовый (100-балльную шкалу)
# ---------------------------------------------------------------------------
# ВНИМАНИЕ: официальной таблицы здесь НЕТ. Пока словарь пуст, используется линейная
# шкала — это приближение, а не регламент. Чтобы включить настоящий перевод,
# впишите сюда пары {первичный_балл: тестовый_балл} из актуального документа
# Рособрнадзора; функция test_score() сразу начнёт брать значения отсюда.
#
# Отдельно учтите: без задания №27 потолок первичного балла ниже максимума за всю
# работу, поэтому тестовый балл по официальной таблице никогда не достигнет 100.
RAW_TO_TEST_SCORE: dict[int, int] = {}


def evaluate(selected: list[int], correct: list[int]) -> bool:
    """Ответ верен, только если выбран весь правильный набор и ничего лишнего (ТЗ п.5)."""
    return sorted(set(selected)) == sorted(set(correct))


def normalize_open(value: str) -> str:
    """Приводит текстовый ответ к сравнимому виду.

    Ученик пишет «Которой», «которой », «сделалаусилие» — всё это должно
    засчитываться. Убираем регистр, пробелы, дефисы и не различаем е/ё: на бланке
    ЕГЭ ответ пишется без пробелов, и источник сам теряет их в поле «Правильный
    ответ», так что различать их бессмысленно.
    """
    value = (value or "").strip().lower().replace("ё", "е")
    return re.sub(r"[\s\-—]+", "", value)


def evaluate_open(typed: str, answers: list[str]) -> bool:
    """Открытый ответ: годится любая из перечисленных источником форм."""
    if not typed or not answers:
        return False
    normalized = normalize_open(typed)
    return any(normalized == normalize_open(answer) for answer in answers)


def evaluate_digits(typed: str, answers: list[str]) -> bool:
    """Ввод цифр: порядок не важен, как и на настоящем экзамене.

    Источник иногда перечисляет перестановки («135|351|531»), а иногда даёт одну
    запись («1234»), хотя принимаются любые порядки. Поэтому сравниваем наборы
    цифр, а не строки.
    """
    if not typed or not answers:
        return False
    digits = set(re.findall(r"\d", typed))
    if not digits:
        return False
    return any(digits == set(re.findall(r"\d", answer)) for answer in answers)


def evaluate_match(selected: list[int], correct: list[int]) -> bool:
    """Соответствие: порядок обязателен — каждой позиции слева своя позиция справа."""
    if not correct or len(selected) != len(correct):
        return False
    return list(selected) == list(correct)


def check_answer(kind: str, response, task_correct: list[int], task_answers: list[str] | None) -> bool:
    """Единая точка проверки: выбирает способ сверки по виду задания."""
    if kind == KIND_OPEN:
        return evaluate_open(str(response or ""), task_answers or [])
    if kind == KIND_DIGITS:
        return evaluate_digits(str(response or ""), task_answers or [])
    if kind == KIND_MATCH:
        return evaluate_match(list(response or []), task_correct)
    return evaluate(list(response or []), task_correct)


def match_mistakes(selected: list[int], correct: list[int]) -> int:
    """Сколько позиций левого столбца заполнено неверно.

    Незаполненная позиция считается ошибкой наравне с неверной: пустое место в
    ответе — это не выполненная часть задания.
    """
    if not correct:
        return 0
    selected = list(selected or [])
    mistakes = sum(
        1 for index, value in enumerate(correct)
        if index >= len(selected) or selected[index] != value
    )
    # Лишние позиции сверх правого столбца — тоже ошибки.
    return mistakes + max(0, len(selected) - len(correct))


def award_points(number: int, is_correct: bool, mistakes: int | None = None) -> int:
    """Первичные баллы за одно задание.

    Обычные задания — «всё или ничего»: полный вес за верный ответ, иначе ноль.
    У заданий на соответствие (№8, №22) есть промежуточная ступень: ошибка на
    одной или двух позициях из пяти стоит 1 балла из 2, три и больше — ноль.
    `mistakes` считает `match_mistakes`; None значит «считать нечего».
    """
    maximum = TASK_MAX_POINTS.get(number, 1)
    if is_correct:
        return maximum
    if (
        number in PARTIAL_TASKS
        and mistakes is not None
        and 1 <= mistakes <= MAX_PARTIAL_MISTAKES
    ):
        return 1
    return 0


def score_answer(number: int, kind: str, response, task_correct, task_answers) -> tuple[bool, int]:
    """Ответ -> (верен ли целиком, первичные баллы).

    Единая точка для слоя БД: проверка и начисление всегда идут парой, и частичный
    балл за соответствие иначе легко потерять.

    Частично верный ответ остаётся НЕверным: в статистике «правильных» он не
    считается, иначе процент точности начнёт врать. Балл при этом начисляется.
    """
    is_correct = check_answer(kind, response, task_correct, task_answers)
    mistakes = (
        match_mistakes(list(response or []), list(task_correct or []))
        if kind == KIND_MATCH else None
    )
    return is_correct, award_points(number, is_correct, mistakes)


def max_points(number: int) -> int:
    return TASK_MAX_POINTS.get(number, 1)


def raw_score(results: dict[int, bool]) -> int:
    """Первичный балл за вариант. results: {номер задания: ответ верен}."""
    return sum(award_points(number, ok) for number, ok in results.items())


def test_score(raw: int) -> int:
    """Первичный балл -> тестовый (0-100)."""
    raw = max(0, min(raw, MAX_RAW_SCORE))
    if RAW_TO_TEST_SCORE:
        if raw in RAW_TO_TEST_SCORE:
            return RAW_TO_TEST_SCORE[raw]
        # балла нет в таблице — берём ближайший меньший
        lower = [k for k in RAW_TO_TEST_SCORE if k <= raw]
        return RAW_TO_TEST_SCORE[max(lower)] if lower else 0
    if MAX_RAW_SCORE == 0:
        return 0
    return round(raw * 100 / MAX_RAW_SCORE)


def is_official_table_configured() -> bool:
    """False — значит тестовый балл считается линейным приближением."""
    return bool(RAW_TO_TEST_SCORE)


def accuracy(correct: int, total: int) -> int:
    """Процент правильных, округлённый до целого. Ноль заданий — ноль процентов."""
    return round(correct * 100 / total) if total else 0


# ---------------------------------------------------------------------------
# Регламент полного варианта
# ---------------------------------------------------------------------------
# 210 минут — официальная продолжительность ЕГЭ по русскому языку целиком.
# Оставлено как есть по решению заказчика, хотя сочинение (№27) не входит в вариант.
VARIANT_TIME_LIMIT_SEC = 210 * 60

# Количество вопросов, доступное в обычной тренировке (ТЗ п.3.1).
TRAINING_COUNTS = (6, 9, 12, 15)

VARIANT_TASK_COUNT = LAST_TASK
