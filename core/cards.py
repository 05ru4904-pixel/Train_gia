"""Карточки для запоминания: колоды слов и их разбор.

Первая колода — ударения (задание №4). Формат файла `content/cards/accents.txt`:

    ## Существительные
    свЁкла — не «свеклА»
    тОрты — ударение неподвижно

Ударная гласная пишется заглавной — из неё получаются сразу три вещи: вопрос
(слово без подсказки), ответ (то же слово с выделенной буквой) и проверка, что
строка вообще корректна.

Файлы, а не база: колоды правятся редко, а git даёт версии и откат бесплатно —
тот же довод, что и у шпаргалок в `core/cheatsheets.py`.

Главное правило то же, что у парсера вариантов: строка, в которой ударение не
определяется однозначно, в колоду не попадает, а уходит в `problems`.
"""
import logging
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

CONTENT_DIR = Path(__file__).resolve().parent.parent / "content" / "cards"

VOWELS = "АЕЁИОУЫЭЮЯ"

# Слово карточки: русские буквы и дефис, ничего больше.
RE_WORD = re.compile(r"^[А-Яа-яЁё-]+$")

# Подсказка отделяется тире — длинным или обычным, с пробелами вокруг.
RE_SPLIT = re.compile(r"\s+[—–-]\s+")


@dataclass(frozen=True)
class Card:
    key: str        # слово в нижнем регистре — постоянный идентификатор карточки
    word: str       # вопрос: слово без подсказок, «ё» показана как «е»
    answer: str     # ответ: слово целиком, ударная буква — по индексу stress
    stress: int     # позиция ударной гласной в answer
    hint: str       # необязательное пояснение
    group: str      # раздел колоды: существительные, глаголы, наречия…

    def payload(self) -> dict:
        return {
            "key": self.key,
            "word": self.word,
            "answer": self.answer,
            "stress": self.stress,
            "hint": self.hint,
            "group": self.group,
        }


@dataclass(frozen=True)
class Deck:
    id: str
    title: str
    subtitle: str
    task_number: int | None   # к какому заданию ЕГЭ относится колода


DECKS: dict[str, Deck] = {
    "accents": Deck(
        id="accents",
        title="Ударения",
        subtitle="Орфоэпический минимум, задание №4",
        task_number=4,
    ),
}

# Разобранные колоды держим в памяти: файлы мелкие, а процесс перезапускается на
# каждом деплое — устареть кеш незаметно не может.
_cards: dict[str, list[Card]] = {}
_problems: dict[str, list[str]] = {}


def _parse_line(line: str, group: str) -> tuple[Card | None, str | None]:
    """Строка файла -> карточка либо причина отказа."""
    parts = RE_SPLIT.split(line, maxsplit=1)
    display = parts[0].strip()
    hint = parts[1].strip() if len(parts) > 1 else ""

    if not RE_WORD.match(display):
        return None, f"{line!r}: в слове есть посторонние символы"

    marked = [index for index, char in enumerate(display) if char.isupper()]
    if len(marked) != 1:
        return None, f"{display!r}: ударная гласная должна быть ровно одна заглавная"
    at = marked[0]
    if display[at] not in VOWELS:
        return None, f"{display!r}: заглавная буква «{display[at]}» не гласная"

    answer = display.lower()
    return Card(
        key=answer,
        # «Ё» в вопросе прячем: догадаться, что там не «е», — часть задания.
        word=answer.replace("ё", "е"),
        answer=answer,
        stress=at,
        hint=hint,
        group=group,
    ), None


def _load(deck_id: str) -> None:
    if deck_id in _cards:
        return
    cards: list[Card] = []
    problems: list[str] = []
    seen: set[str] = set()

    path = CONTENT_DIR / f"{deck_id}.txt"
    if path.is_file():
        group = ""
        for raw in path.read_text(encoding="utf-8").split("\n"):
            line = raw.strip()
            if not line:
                continue
            if line.startswith("##"):
                group = line.lstrip("#").strip()
                continue
            if line.startswith("#"):
                continue

            card, problem = _parse_line(line, group)
            if problem:
                problems.append(problem)
                continue
            if card.key in seen:
                problems.append(f"{card.key!r}: слово встречается второй раз")
                continue
            seen.add(card.key)
            cards.append(card)

    if problems:
        # Не падаем: колода без пары строк лучше, чем раздел, который не открылся.
        # Но молчать нельзя — иначе слово потеряется незаметно.
        log.warning("Колода %s: строк не разобрано %s", deck_id, len(problems))
        for problem in problems[:20]:
            log.warning("  %s", problem)

    _cards[deck_id] = cards
    _problems[deck_id] = problems


def reset_cache() -> None:
    _cards.clear()
    _problems.clear()


def deck(deck_id: str) -> Deck | None:
    return DECKS.get(deck_id)


def cards(deck_id: str) -> list[Card]:
    if deck_id not in DECKS:
        return []
    _load(deck_id)
    return _cards[deck_id]


def problems(deck_id: str) -> list[str]:
    if deck_id not in DECKS:
        return []
    _load(deck_id)
    return _problems[deck_id]


def by_key(deck_id: str, key: str) -> Card | None:
    for card in cards(deck_id):
        if card.key == key:
            return card
    return None


def known_keys(deck_id: str) -> set[str]:
    return {card.key for card in cards(deck_id)}
