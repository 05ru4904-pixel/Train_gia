"""Карточка задания в админ-боте. Запуск: python tests/test_admin_cards.py

Карточка — единственный способ проверить, что залилось в базу. Пока она печатала
любое задание как выбор варианта, у заданий с вписыванием ответа она выходила
пустой, а у соответствия — уверенно неверной. Поэтому она под тестом.
"""
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Движок базы создаётся при импорте admin_bot; сама база тестам не нужна, но
# строка подключения должна быть валидной.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from bots.admin_bot import PASSAGE_PREVIEW, task_card  # noqa: E402
from core.tasks_meta import (  # noqa: E402
    KIND_CHOICE,
    KIND_DIGITS,
    KIND_MATCH,
    KIND_OPEN,
)


@dataclass
class FakeTask:
    """Задание как его отдаёт база — по именам полей модели Task."""

    id: str = "K7F29A"
    number: int = 4
    kind: str = KIND_CHOICE
    text: str = "Условие задания"
    passage: str | None = None
    options: list = field(default_factory=list)
    match_left: list | None = None
    correct: list = field(default_factory=list)
    answers: list | None = None


def test_choice_card():
    """Варианты подписаны цифрами — как в источнике и как их видит ученик."""
    card = task_card(FakeTask(options=["звонИт", "звОнит"], correct=[1]))
    assert "2) звОнит ✅" in card
    assert "1) звонИт ✅" not in card
    assert "Ответ: 2" in card


def test_open_card_shows_answers():
    card = task_card(FakeTask(
        number=5, kind=KIND_OPEN, answers=["заклятым", "заклятый"],
        passage="Он всегда был моим заклятым врагом.",
    ))
    assert "Ответ: заклятым, заклятый" in card
    assert "вписать слово" in card
    assert "заклятым врагом" in card, "материал задания должен быть виден"


def test_digits_card():
    card = task_card(FakeTask(number=15, kind=KIND_DIGITS, answers=["134"]))
    assert "Ответ: 134" in card
    assert "вписать цифры" in card


def test_empty_answer_is_flagged():
    """Задание без ответа ученику засчитать нельзя — админ обязан это увидеть."""
    assert "⚠️" in task_card(FakeTask(number=5, kind=KIND_OPEN, answers=[]))
    assert "⚠️" in task_card(FakeTask(options=["а", "б"], correct=[]))


MATCH = FakeTask(
    id="QN7TPL", number=8, kind=KIND_MATCH,
    text="Установите соответствие.",
    match_left=["нарушение с деепричастным оборотом", "ошибка в падежной форме"],
    options=["первое предложение", "второе предложение", "третье предложение"],
    correct=[3, 1],   # позиции правого столбца, с единицы
)


def test_match_card_shows_both_columns():
    card = task_card(MATCH)
    assert "А) нарушение с деепричастным оборотом → 3" in card
    assert "Б) ошибка в падежной форме → 1" in card
    # правый столбец нумеруется с единицы и не выдаётся за варианты ответа
    assert "3) третье предложение" in card
    assert "Ответ: А-3, Б-1" in card


def test_match_card_does_not_tick_wrong_option():
    """Старая карточка ставила галочку по индексу, а correct у соответствия
    считается с единицы — галочка попадала не на тот пункт."""
    card = task_card(MATCH)
    assert "✅" not in card
    assert "Ответ: Г" not in card, "буквы за пределами столбца — признак старой ошибки"


def test_match_card_flags_broken_task():
    broken = FakeTask(
        number=8, kind=KIND_MATCH, match_left=["а", "б", "в"],
        options=["1", "2"], correct=[1],
    )
    assert "⚠️" in task_card(broken)


def test_long_passage_is_trimmed():
    card = task_card(FakeTask(number=5, kind=KIND_OPEN, answers=["да"], passage="я" * 5000))
    assert len(card) < 4096, "карточка должна помещаться в сообщение Telegram"
    assert f"ещё {5000 - PASSAGE_PREVIEW} символов" in card


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
