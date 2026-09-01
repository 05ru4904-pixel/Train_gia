"""Чек-листы и шпаргалки по заданиям №1-26.

Тексты лежат файлами в `content/cheatsheets/NN.md` — по одному на номер задания.
Файлы, а не база: шпаргалок ровно 26, меняются они редко, а git даёт версии и
откат бесплатно. Понадобится правка из админ-бота — переедут в таблицу, API
менять не придётся.

Формат — подмножество markdown, которое умеет рисовать клиент:
    ## Заголовок раздела
    - пункт списка
    1. шаг по порядку
    > заметка
    **важное**

Заголовок и тема шпаргалки не пишутся в файле: они берутся из `tasks_meta`, чтобы
не разойтись с тем, что ученик видит над заданием.
"""
from pathlib import Path

from core.tasks_meta import TASK_NUMBERS, subtitle, title

CONTENT_DIR = Path(__file__).resolve().parent.parent / "content" / "cheatsheets"

# Прочитанные файлы держим в памяти: они мелкие, а процесс перезапускается на
# каждом деплое — значит кеш не может устареть незаметно.
_cache: dict[int, str] = {}
_scanned = False


def _path(number: int) -> Path:
    return CONTENT_DIR / f"{number:02d}.md"


def _load() -> None:
    global _scanned
    if _scanned:
        return
    for number in TASK_NUMBERS:
        path = _path(number)
        if path.is_file():
            text = path.read_text(encoding="utf-8").strip()
            if text:
                _cache[number] = text
    _scanned = True


def reset_cache() -> None:
    """Сбрасывает кеш — нужно тестам, которые кладут файлы на лету."""
    global _scanned
    _cache.clear()
    _scanned = False


def body(number: int) -> str | None:
    """Текст шпаргалки или None, если её ещё не написали."""
    _load()
    return _cache.get(number)


def has(number: int) -> bool:
    return body(number) is not None


def index() -> list[dict]:
    """Список всех 26 заданий с пометкой, готова ли шпаргалка.

    Отдаются все номера, а не только готовые: ученик должен видеть полную карту
    раздела и понимать, что появится дальше.
    """
    _load()
    return [
        {
            "number": number,
            "title": title(number),
            "subtitle": subtitle(number),
            "ready": number in _cache,
        }
        for number in TASK_NUMBERS
    ]


def ready_count() -> int:
    _load()
    return len(_cache)
