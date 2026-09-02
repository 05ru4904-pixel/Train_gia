"""Карточки для запоминания: колоды, подход и отметки «знаю / не знаю»."""
import random

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.deps import CurrentUser, DbSession
from core import cards as cards_meta
from db import crud
from db.models import CARD_KNOWN

router = APIRouter(tags=["cards"])

# Сколько карточек в одном подходе. Десять — столько, сколько успеваешь между
# делами и не устаёшь: подход должен заканчиваться раньше, чем надоест.
SESSION_SIZE = 10

MODE_NEW = "new"
MODE_REPEAT = "repeat"


class CardAnswer(BaseModel):
    card: str = Field(min_length=1, max_length=64)
    known: bool


def _deck_or_404(deck_id: str):
    deck = cards_meta.deck(deck_id)
    if deck is None:
        raise HTTPException(404, {"code": "no_such_deck", "message": "Такой колоды нет"})
    return deck


def _counts(deck_id: str, progress: dict[str, str]) -> dict:
    """Сколько в колоде выучено, сколько на повторе, сколько ещё не видели."""
    all_cards = cards_meta.cards(deck_id)
    known = sum(1 for card in all_cards if progress.get(card.key) == CARD_KNOWN)
    repeat = sum(
        1 for card in all_cards
        if card.key in progress and progress[card.key] != CARD_KNOWN
    )
    return {
        "total": len(all_cards),
        "known": known,
        "repeat": repeat,
        "fresh": len(all_cards) - known - repeat,
    }


def _deck_payload(deck, progress: dict[str, str]) -> dict:
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


@router.get("/cards/{deck_id}/session")
async def session(deck_id: str, user: CurrentUser, db: DbSession, mode: str = MODE_NEW) -> dict:
    """Набирает подход из десяти карточек.

    Два режима, и они про разное:
      * `new` — учим дальше: сначала те, которых ученик ещё не видел, и только
        если новых не хватило, добираем отложенные на повтор;
      * `repeat` — только отложенные, чтобы закрыть хвост.

    Внутри режима порядок случайный: заученный порядок слов на экзамене не поможет.
    """
    deck = _deck_or_404(deck_id)
    if mode not in (MODE_NEW, MODE_REPEAT):
        raise HTTPException(400, {"code": "bad_mode", "message": "Неизвестный режим"})

    progress = await crud.card_progress(db, user.id, deck.id)
    all_cards = cards_meta.cards(deck.id)

    fresh = [c for c in all_cards if c.key not in progress]
    repeat = [c for c in all_cards if progress.get(c.key) not in (None, CARD_KNOWN)]

    # Порядок внутри группы случайный: выученная последовательность слов на
    # экзамене не пригодится, а вот узнавание слова «не по месту» — да.
    random.shuffle(fresh)
    random.shuffle(repeat)

    # Новые идут первыми, отложенные добираются следом: иначе колода упирается в
    # хвост из трудных слов и вперёд не двигается.
    pool = repeat if mode == MODE_REPEAT else fresh + repeat
    chosen = pool[:SESSION_SIZE]
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
    return {"ok": True, **_counts(deck.id, progress)}


@router.post("/cards/{deck_id}/reset")
async def reset(deck_id: str, user: CurrentUser, db: DbSession) -> dict:
    """Забывает прогресс по колоде — начать сначала."""
    deck = _deck_or_404(deck_id)
    forgotten = await crud.reset_cards(db, user.id, deck.id)
    return {"ok": True, "forgotten": forgotten, **_counts(deck.id, {})}
