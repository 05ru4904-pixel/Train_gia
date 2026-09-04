"""Средства выразительности: тренажёр к заданию №22.

Это не карточки. Механика другая: выражение и четыре кнопки с названиями
приёмов, программа сама говорит «верно» или «неверно». Самопроверки
«Знаю / Не знаю» здесь нет, а значит нет ни таймеров, ни слабых, ни выученных.

Отдельно от `core/cards.py` и `core/paronyms.py` намеренно — разное задание,
разный код, правка здесь не должна доставать до №4 и №5.

**Вопросы берутся из базы, а не из файла.** У задания №22 пять позиций
«выражение -> приём», уже официальных и уже вычитанных источником. Восемь
залитых вариантов дают 40 вопросов, и каждый новый вариант добавляет ещё пять
сам, без правки контента.

Три обманки тянутся случайно из перечня ниже. Гнёзда «похожих» приёмов не
заводим: тогда набор кнопок сам выдавал бы ответ тому, кто его запомнил.

Перечень — `reference/task22_terms.md`, официальный и закрытый: составители
берут термины только оттуда. Здесь он лежит так же, как таблица баллов из
`state.md` лежит в `core/tasks_meta.py`. Термин задания, которого в перечне нет,
вопросом не станет — правило то же, что у парсера вариантов: при любой
неоднозначности ничего не собирается.
"""
import random
import re
from dataclasses import dataclass

# --- группы приёмов -----------------------------------------------------------
PHONETIC = "phonetic"
LEXICAL = "lexical"
SYNTACTIC = "syntactic"

GROUP_TITLES = {
    LEXICAL: "Лексические",
    SYNTACTIC: "Синтаксические",
    PHONETIC: "Фонетические",
}

# Порядок на экране точности: от самой населённой группы к самой маленькой.
GROUP_ORDER = (LEXICAL, SYNTACTIC, PHONETIC)

# --- приём -> группа ----------------------------------------------------------
# 23 термина: 2 фонетических, 7 лексических, 14 синтаксических. Поменяет ФИПИ
# перечень — правится только этот блок.
TERMS: dict[str, str] = {
    "ассонанс": PHONETIC,
    "аллитерация": PHONETIC,

    "эпитет": LEXICAL,
    "метафора": LEXICAL,
    "метонимия": LEXICAL,
    "олицетворение": LEXICAL,
    "гипербола": LEXICAL,
    "литота": LEXICAL,
    "сравнение": LEXICAL,

    "синтаксический параллелизм": SYNTACTIC,
    "парцелляция": SYNTACTIC,
    "вопросно-ответная форма изложения": SYNTACTIC,
    "градация": SYNTACTIC,
    "инверсия": SYNTACTIC,
    "лексический повтор": SYNTACTIC,
    "анафора": SYNTACTIC,
    "эпифора": SYNTACTIC,
    "антитеза": SYNTACTIC,
    "риторический вопрос": SYNTACTIC,
    "риторическое восклицание": SYNTACTIC,
    "риторическое обращение": SYNTACTIC,
    "многосоюзие": SYNTACTIC,
    "бессоюзие": SYNTACTIC,
}

assert set(GROUP_TITLES) == set(GROUP_ORDER) == set(TERMS.values()), "группы разошлись"

# Номер задания и подписи раздела.
TASK_NUMBER = 22
DECK_ID = "means"
DECK_TITLE = "Средства выразительности"
DECK_SUBTITLE = "Изобразительные средства, задание №22"

# Кнопок под выражением. Четыре везде, включая фонетические: обманки берутся со
# всего перечня, а не из своей группы, поэтому ассонансу есть с чем соседствовать.
OPTIONS_COUNT = 4

# Вопросов в одном подходе — столько же, сколько карточек в соседних разделах.
SESSION_SIZE = 10


def normalize(value: str) -> str:
    """Термин из задания -> ключ перечня. Регистр, лишние пробелы и «ё» не в счёт."""
    value = (value or "").strip().lower().replace("ё", "е")
    return re.sub(r"\s+", " ", value)


def group_of(term: str) -> str | None:
    return TERMS.get(normalize(term))


@dataclass(frozen=True)
class Pair:
    """Одна позиция задания №22: выражение и приём, который в нём использован."""

    task_id: str
    position: int      # номер позиции в match_left, он же адрес вопроса
    text: str
    term: str

    @property
    def group(self) -> str:
        return TERMS[self.term]


def pairs_from_task(task) -> list[Pair]:
    """Задание №22 -> вопросы. Позиция, в которой что-то не сходится, пропускается.

    `correct` у соответствия хранится с единицы — это позиции правого столбца в
    порядке букв А-Д (соглашение проекта, см. CLAUDE.md).
    """
    left = list(task.match_left or [])
    correct = list(task.correct or [])
    options = list(task.options or [])

    pairs: list[Pair] = []
    for position, (text, index) in enumerate(zip(left, correct)):
        if not isinstance(index, int) or not 1 <= index <= len(options):
            continue
        term = normalize(options[index - 1])
        if term not in TERMS:
            # Термина нет в закрытом перечне — вопрос не собираем и не гадаем.
            continue
        text = (text or "").strip()
        if not text:
            continue
        pairs.append(Pair(task_id=task.id, position=position, text=text, term=term))
    return pairs


def pairs_from_tasks(tasks) -> list[Pair]:
    pairs: list[Pair] = []
    for task in tasks:
        pairs.extend(pairs_from_task(task))
    return pairs


def term_at(task, position: int) -> str | None:
    """Правильный приём для позиции. Им проверяется ответ — клиенту он не уходит."""
    for pair in pairs_from_task(task):
        if pair.position == position:
            return pair.term
    return None


def build_options(term: str) -> list[str]:
    """Правильный приём и три случайные обманки из перечня, вперемешку."""
    others = [name for name in TERMS if name != term]
    random.shuffle(others)
    options = others[: OPTIONS_COUNT - 1] + [term]
    random.shuffle(options)
    return options


def question_payload(pair: Pair) -> dict:
    """Вопрос для клиента. Правильного ответа здесь нет — он проверяется на сервере."""
    return {
        "task_id": pair.task_id,
        "position": pair.position,
        "text": pair.text,
        "options": build_options(pair.term),
    }


def deck_payload() -> dict:
    """Шапка раздела: то, что не зависит от ученика."""
    return {
        "id": DECK_ID,
        "title": DECK_TITLE,
        "subtitle": DECK_SUBTITLE,
        "task_number": TASK_NUMBER,
        "size": SESSION_SIZE,
    }
