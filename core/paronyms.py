"""Паронимы: колода к заданию №5. Разбор файла `content/cards/paronyms.txt`.

Отдельно от `core/cards.py` намеренно. Карточка ударения — одно слово с позицией
ударения, карточка паронимов — группа слов со значениями. Общего кода у них нет,
и связывать их не нужно: правка здесь не должна доставать до задания №4.

Формат строки — группа паронимов. Слова разделены `|`, слово и его значение `=`:

    ## В
    вдох = вбирание воздуха: глубокий вдох | вздох = выдох как знак чувства: тяжёлый вздох

Слов в группе два или больше — в словнике есть и тройки. Ученик видит только сами
слова, вспоминает разницу и проверяет себя по значениям. Единица прогресса — вся
группа целиком, ключом служит первое слово.

Файл, а не база: словник правится редко, git даёт версии и откат бесплатно.

Правило то же, что у остальных парсеров проекта: строка, в которой что-то
определяется неоднозначно, в колоду не попадает, а уходит в `problems`.
"""
import logging
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

CONTENT_PATH = Path(__file__).resolve().parent.parent / "content" / "cards" / "paronyms.txt"

# Слово словника: русские буквы и дефис, ничего больше.
RE_WORD = re.compile(r"^[А-Яа-яЁё-]+$")

# Разделители. Внутри значения их быть не должно — на этом держится разбор.
GROUP_SEP = "|"
MEANING_SEP = "="

DECK_ID = "paronyms"
DECK_TITLE = "Паронимы"
DECK_SUBTITLE = "Словник паронимов, задание №5"
TASK_NUMBER = 5

# Подсказка на лице карточки: что ученику делать, пока ответ закрыт.
PROMPT = "Вспомни, что значит каждое"


@dataclass(frozen=True)
class Card:
    key: str              # первое слово группы — постоянный идентификатор карточки
    words: list[str]      # вопрос: сами слова, больше на лице ничего нет
    meanings: list[str]   # ответ: значение каждого, порядок тот же
    section: str          # раздел словника, показывается в углу карточки

    def payload(self) -> dict:
        return {
            "key": self.key,
            "words": list(self.words),
            "meanings": list(self.meanings),
            # Одной строкой — для списков слабых и выученных, там место в строку.
            "title": " / ".join(self.words),
            "section": self.section,
        }


_cards: list[Card] | None = None
_problems: list[str] = []


def _parse_line(line: str, section: str) -> tuple[Card | None, str | None]:
    """Строка словника -> карточка либо причина отказа."""
    chunks = [chunk.strip() for chunk in line.split(GROUP_SEP)]
    if len(chunks) < 2:
        return None, f"{line!r}: в группе меньше двух слов"

    words: list[str] = []
    meanings: list[str] = []
    for chunk in chunks:
        if chunk.count(MEANING_SEP) != 1:
            return None, f"{chunk!r}: у слова должно быть ровно одно значение через «=»"
        word, meaning = (part.strip() for part in chunk.split(MEANING_SEP))
        if not RE_WORD.match(word):
            return None, f"{word!r}: в слове есть посторонние символы"
        if not meaning:
            return None, f"{word!r}: значение пустое"
        words.append(word.lower())
        meanings.append(meaning)

    if len(set(words)) != len(words):
        return None, f"{line!r}: слово в группе повторяется"

    return Card(key=words[0], words=words, meanings=meanings, section=section), None


def _load() -> None:
    global _cards, _problems
    if _cards is not None:
        return

    cards: list[Card] = []
    problems: list[str] = []
    seen: set[str] = set()

    if CONTENT_PATH.is_file():
        section = ""
        for raw in CONTENT_PATH.read_text(encoding="utf-8").split("\n"):
            line = raw.strip()
            if not line:
                continue
            if line.startswith("##"):
                section = line.lstrip("#").strip()
                continue
            if line.startswith("#"):
                continue

            card, problem = _parse_line(line, section)
            if problem:
                problems.append(problem)
                continue
            if card.key in seen:
                problems.append(f"{card.key!r}: группа с этим словом уже была")
                continue
            seen.add(card.key)
            cards.append(card)

    if problems:
        # Не падаем: колода без пары строк лучше, чем раздел, который не открылся.
        # Но молчать нельзя — иначе группа потеряется незаметно.
        log.warning("Словник паронимов: строк не разобрано %s", len(problems))
        for problem in problems[:20]:
            log.warning("  %s", problem)

    _cards = cards
    _problems = problems


def reset_cache() -> None:
    global _cards, _problems
    _cards = None
    _problems = []


def cards() -> list[Card]:
    _load()
    return _cards


def problems() -> list[str]:
    _load()
    return _problems


def by_key(key: str) -> Card | None:
    for card in cards():
        if card.key == key:
            return card
    return None


def deck_payload() -> dict:
    """Шапка колоды: то, что не зависит от ученика."""
    return {
        "id": DECK_ID,
        "title": DECK_TITLE,
        "subtitle": DECK_SUBTITLE,
        "task_number": TASK_NUMBER,
        "prompt": PROMPT,
    }
