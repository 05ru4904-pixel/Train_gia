"""Карточки для запоминания: колоды, подход, повторение и список выученных."""
import random
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.deps import CurrentUser, DbSession
from core import cards as cards_meta
from db import crud
from db.crud import CardState

router = APIRouter(tags=["cards"])

# Сколько карточек в одном подходе. Десять — столько, сколько успеваешь между
# делами и не устаёшь: подход должен заканчиваться раньше, чем надоест.
SESSION_SIZE = 10

# Из десяти карточек подхода четыре отданы повторению. Остальные шесть — новые
# слова: колода должна двигаться вперёд, а не превращаться в топтание на месте.
REPEAT_SLOTS = 4

# Одно место из каждых четырёх достаётся второй очереди — словам, которые ученик
# видел уже дважды. Отсюда и деление «три к одному» внутри повтора.
SECOND_QUEUE_EVERY = 4

MODE_NEW = "new"
MODE_REPEAT = "repeat"

# Метка для строк без времени ответа: такие уходят в начало очереди.
OLDEST = datetime(1970, 1, 1, tzinfo=timezone.utc)


class CardAnswer(BaseModel):
    card: str = Field(min_length=1, max_length=64)
    known: bool


def _deck_or_404(deck_id: str):
    deck = cards_meta.deck(deck_id)
    if deck is None:
        raise HTTPException(404, {"code": "no_such_deck", "message": "Такой колоды нет"})
    return deck


def _at(state: CardState) -> datetime:
    """Время последнего ответа — ключ очерёдности.

    SQLite отдаёт время без часового пояса, Postgres — с ним; сравнивать их
    напрямую нельзя, поэтому наивное считаем UTC.
    """
    moment = state.updated_at
    if moment is None:
        return OLDEST
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _counts(deck_id: str, progress: dict[str, CardState]) -> dict:
    """Сколько в колоде выучено, сколько в работе, сколько ещё не видели."""
    all_cards = cards_meta.cards(deck_id)
    learned = 0
    repeat = 0
    for card in all_cards:
        state = progress.get(card.key)
        if state is None:
            continue
        if state.learned:
            learned += 1
        else:
            repeat += 1
    return {
        "total": len(all_cards),
        "learned": learned,
        "repeat": repeat,
        "fresh": len(all_cards) - learned - repeat,
    }


def _queues(deck_id: str, progress: dict[str, CardState]) -> tuple[list, list]:
    """Две очереди на повтор.

    Первая — слова, которые ученик видел один раз: отложил и с тех пор не
    встречал. Вторая — те, кого он видел уже дважды и больше.

    Внутри очереди порядок по времени последнего ответа: кто ответил раньше,
    тот раньше и вернётся. Случайности здесь нет намеренно — ученик должен
    закрывать хвост, а не встречать одни и те же лёгкие слова.
    """
    first: list[tuple] = []
    second: list[tuple] = []
    for card in cards_meta.cards(deck_id):
        state = progress.get(card.key)
        if state is None or state.learned:
            continue
        (first if state.shows <= 1 else second).append((card, state))

    first.sort(key=lambda pair: _at(pair[1]))
    second.sort(key=lambda pair: _at(pair[1]))
    return [card for card, _ in first], [card for card, _ in second]


def _take_repeat(first: list, second: list, slots: int) -> list:
    """Набирает слова на повтор в пропорции «три из первой очереди, одно из второй».

    Пустая очередь подход не укорачивает: чего не хватило в одной, добираем из
    другой. Иначе ученик, у которого все отложенные слова уже второго круга,
    получал бы вместо десяти карточек одну.
    """
    want_second = slots // SECOND_QUEUE_EVERY
    want_first = slots - want_second

    picked_first = first[:want_first]
    picked_second = second[:want_second]
    if len(picked_first) < want_first:
        picked_second = second[:want_second + want_first - len(picked_first)]
    if len(picked_second) < want_second:
        picked_first = first[:want_first + want_second - len(picked_second)]
    return picked_first + picked_second


def _deck_payload(deck, progress: dict[str, CardState]) -> dict:
    return {
        "id": deck.id,
        "title": deck.title,
        "subtitle": deck.subtitle,
        "task_number": deck.task_number,
        "size": SESSION_SIZE,
        **_counts(deck.id, progress),
    }


@router.get("/cards")
async def decks(user: CurrentUser, db: DbSession) -> dict:
    """Список колод с прогрессом ученика."""
    items = []
    for deck in cards_meta.DECKS.values():
        progress = await crud.card_progress(db, user.id, deck.id)
        items.append(_deck_payload(deck, progress))
    return {"decks": items}


@router.get("/cards/{deck_id}")
async def deck_state(deck_id: str, user: CurrentUser, db: DbSession) -> dict:
    """Состояние одной колоды — экран перед подходом."""
    deck = _deck_or_404(deck_id)
    progress = await crud.card_progress(db, user.id, deck.id)
    return _deck_payload(deck, progress)


@router.get("/cards/{deck_id}/learned")
async def learned(deck_id: str, user: CurrentUser, db: DbSession) -> dict:
    """Выученные слова — список, который ученик открывает посмотреть.

    Свежие сверху: заходят обычно за тем, что закрыли только что.
    """
    deck = _deck_or_404(deck_id)
    progress = await crud.card_progress(db, user.id, deck.id)

    rows = []
    for card in cards_meta.cards(deck.id):
        state = progress.get(card.key)
        if state is not None and state.learned:
            rows.append((card, state))
    rows.sort(key=lambda pair: _at(pair[1]), reverse=True)

    return {
        "deck": deck.id,
        "title": deck.title,
        "cards": [card.payload() for card, _ in rows],
        **_counts(deck.id, progress),
    }


@router.get("/cards/{deck_id}/session")
async def session(deck_id: str, user: CurrentUser, db: DbSession, mode: str = MODE_NEW) -> dict:
    """Набирает подход из десяти карточек.

    Два режима, и они про разное:
      * `new` — учим дальше: шесть новых слов и четыре на повтор;
      * `repeat` — только повтор, десять слов, чтобы закрыть хвост.

    Пропорция повтора в обоих режимах одна: три слова из первой очереди на одно
    из второй. Порядок показа внутри подхода перемешан — иначе ученик считает
    карточки и знает заранее, что четвёртая будет трудной.
    """
    deck = _deck_or_404(deck_id)
    if mode not in (MODE_NEW, MODE_REPEAT):
        raise HTTPException(400, {"code": "bad_mode", "message": "Неизвестный режим"})

    progress = await crud.card_progress(db, user.id, deck.id)
    first, second = _queues(deck.id, progress)

    if mode == MODE_REPEAT:
        chosen = _take_repeat(first, second, SESSION_SIZE)
    else:
        chosen = _take_repeat(first, second, REPEAT_SLOTS)

        fresh = [c for c in cards_meta.cards(deck.id) if c.key not in progress]
        random.shuffle(fresh)
        chosen += fresh[:SESSION_SIZE - len(chosen)]

        # Новые слова кончились — добираем повтором. Колода из 187 слов рано или
        # поздно упирается в это, и подход не должен вдруг стать втрое короче.
        if len(chosen) < SESSION_SIZE:
            taken = {card.key for card in chosen}
            rest = [card for card in first + second if card.key not in taken]
            chosen += rest[:SESSION_SIZE - len(chosen)]

    random.shuffle(chosen)
    return {
        "deck": deck.id,
        "mode": mode,
        "cards": [card.payload() for card in chosen],
        **_counts(deck.id, progress),
    }


@router.post("/cards/{deck_id}/answer")
async def answer(deck_id: str, payload: CardAnswer, user: CurrentUser, db: DbSession) -> dict:
    """Отмечает карточку. Пишется сразу — подход часто бросают на середине."""
    deck = _deck_or_404(deck_id)
    if cards_meta.by_key(deck.id, payload.card) is None:
        raise HTTPException(404, {"code": "no_such_card", "message": "Такого слова в колоде нет"})

    await crud.mark_card(db, user.id, deck.id, payload.card, payload.known)
    progress = await crud.card_progress(db, user.id, deck.id)
    state = progress.get(payload.card)
    return {
        "ok": True,
        # Закрылось ли этим ответом само слово. Ключ не `learned`: в счётчиках
        # ниже это число выученных в колоде, и одно затёрло бы другое.
        "card_learned": bool(state and state.learned),
        **_counts(deck.id, progress),
    }


@router.post("/cards/{deck_id}/reset")
async def reset(deck_id: str, user: CurrentUser, db: DbSession) -> dict:
    """Забывает прогресс по колоде целиком — и выученные, и отложенные."""
    deck = _deck_or_404(deck_id)
    forgotten = await crud.reset_cards(db, user.id, deck.id)
    return {"ok": True, "forgotten": forgotten, **_counts(deck.id, {})}
