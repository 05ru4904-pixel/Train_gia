"""Карточки: два тренажёра, таймеры повторения и список выученных.

Методика простая и вся держится на времени.

* **Основной тренажёр** — только новые слова, десять за подход, один проход.
  «Знаю» закрывает слово сразу, «Не знаю» кладёт его в список повтора и запускает
  восьмичасовой таймер.
* **Тренажёр «Повторить»** — только слова с истёкшим таймером. Открывается, когда
  созрело хотя бы пять. Ответ «Знаю» двигает слово дальше: 8 часов -> 24 часа ->
  выучено. Ответ «Не знаю» не меняет ничего, слово просто вернётся в этом же
  подходе — за это отвечает клиент, сервер только записывает ответы.

Расписание живёт в `card_progress.due_at`, переходы — в `crud.next_stage`.
"""
import random
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.deps import CurrentUser, DbSession
from core import cards as cards_meta
from db import crud
from db.crud import CardState, as_utc

router = APIRouter(tags=["cards"])

# Сколько карточек в одном подходе. Десять — столько, сколько успеваешь между
# делами и не устаёшь: подход должен заканчиваться раньше, чем надоест.
SESSION_SIZE = 10

# Пока столько слов не созреет, повторение не открывается. Смысл в том, чтобы
# ученик не бегал в приложение за одним словом: подход должен быть подходом.
REPEAT_MIN = 5

MODE_NEW = "new"
MODE_REPEAT = "repeat"

# Метка для строк без времени: они уходят в начало сортировки.
OLDEST = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _moment(value: datetime | None) -> datetime:
    """Время для сортировки: пустое считаем давно прошедшим."""
    return as_utc(value) if value else OLDEST


class CardAnswer(BaseModel):
    card: str = Field(min_length=1, max_length=64)
    known: bool


def _deck_or_404(deck_id: str):
    deck = cards_meta.deck(deck_id)
    if deck is None:
        raise HTTPException(404, {"code": "no_such_deck", "message": "Такой колоды нет"})
    return deck


def _waiting(deck_id: str, progress: dict[str, CardState]) -> list[tuple]:
    """Весь список повтора: слова, которые ученик отложил и ещё не закрыл.

    Порядок — по сроку, у кого раньше истекает, тот первый. Слово без срока
    считается созревшим и уходит в голову списка.
    """
    rows = []
    for card in cards_meta.cards(deck_id):
        state = progress.get(card.key)
        if state is not None and not state.learned:
            rows.append((card, state))
    rows.sort(key=lambda pair: _moment(pair[1].due_at))
    return rows


def _counts(deck_id: str, progress: dict[str, CardState], now: datetime) -> dict:
    """Цифры для экрана колоды: сколько выучено, ждёт, созрело и ещё не видели."""
    all_cards = cards_meta.cards(deck_id)
    waiting = _waiting(deck_id, progress)
    ready = [pair for pair in waiting if pair[1].ready(now)]
    learned = sum(1 for card in all_cards
                  if (state := progress.get(card.key)) is not None and state.learned)

    # Когда откроется повторение: срок пятого слова в очереди. Слов меньше пяти —
    # ждать нечего, сначала надо набрать их в основном тренажёре.
    ready_at = None
    if len(waiting) >= REPEAT_MIN and len(ready) < REPEAT_MIN:
        due = waiting[REPEAT_MIN - 1][1].due_at
        ready_at = as_utc(due).isoformat() if due else None

    return {
        "total": len(all_cards),
        "learned": learned,
        "repeat": len(waiting),
        "ready": len(ready),
        "fresh": len(all_cards) - learned - len(waiting),
        "can_repeat": len(ready) >= REPEAT_MIN,
        "repeat_min": REPEAT_MIN,
        "ready_at": ready_at,
    }


def _deck_payload(deck, progress: dict[str, CardState], now: datetime) -> dict:
    return {
        "id": deck.id,
        "title": deck.title,
        "subtitle": deck.subtitle,
        "task_number": deck.task_number,
        "size": SESSION_SIZE,
        **_counts(deck.id, progress, now),
    }


@router.get("/cards")
async def decks(user: CurrentUser, db: DbSession) -> dict:
    """Список колод с прогрессом ученика."""
    now = crud.utcnow()
    items = []
    for deck in cards_meta.DECKS.values():
        progress = await crud.card_progress(db, user.id, deck.id)
        items.append(_deck_payload(deck, progress, now))
    return {"decks": items}


@router.get("/cards/{deck_id}")
async def deck_state(deck_id: str, user: CurrentUser, db: DbSession) -> dict:
    """Состояние одной колоды — экран перед подходом."""
    deck = _deck_or_404(deck_id)
    progress = await crud.card_progress(db, user.id, deck.id)
    return _deck_payload(deck, progress, crud.utcnow())


@router.get("/cards/{deck_id}/weak")
async def weak(deck_id: str, user: CurrentUser, db: DbSession) -> dict:
    """Слабые слова — то, что сейчас на повторе, с этапом и таймером.

    Порядок — по мере попадания в список: строка заводится первым «Не знаю» и
    больше не пересоздаётся, так что её номер и есть очерёдность.
    """
    deck = _deck_or_404(deck_id)
    now = crud.utcnow()
    progress = await crud.card_progress(db, user.id, deck.id)

    rows = []
    for card in cards_meta.cards(deck.id):
        state = progress.get(card.key)
        if state is not None and not state.learned:
            rows.append((card, state))
    rows.sort(key=lambda pair: pair[1].row_id)

    return {
        "deck": deck.id,
        "title": deck.title,
        "cards": [
            dict(
                card.payload(),
                stage=state.status,
                ready=state.ready(now),
                due_at=as_utc(state.due_at).isoformat() if state.due_at else None,
            )
            for card, state in rows
        ],
        **_counts(deck.id, progress, now),
    }


@router.get("/cards/{deck_id}/learned")
async def learned(deck_id: str, user: CurrentUser, db: DbSession) -> dict:
    """Выученные слова — список, который ученик открывает посмотреть.

    Свежие сверху: заходят обычно за тем, что закрыли только что.
    """
    deck = _deck_or_404(deck_id)
    now = crud.utcnow()
    progress = await crud.card_progress(db, user.id, deck.id)

    rows = []
    for card in cards_meta.cards(deck.id):
        state = progress.get(card.key)
        if state is not None and state.learned:
            rows.append((card, state))
    rows.sort(key=lambda pair: _moment(pair[1].updated_at), reverse=True)

    return {
        "deck": deck.id,
        "title": deck.title,
        "cards": [card.payload() for card, _ in rows],
        **_counts(deck.id, progress, now),
    }


@router.get("/cards/{deck_id}/session")
async def session(deck_id: str, user: CurrentUser, db: DbSession, mode: str = MODE_NEW) -> dict:
    """Набирает подход.

    `new` — десять новых слов в случайном порядке. Заученный порядок на экзамене
    не пригодится, а узнавание слова «не по месту» — да.

    `repeat` — до десяти созревших слов, у кого раньше истёк срок, тот первый.
    Меньше пяти созревших — подход не собирается вовсе.
    """
    deck = _deck_or_404(deck_id)
    if mode not in (MODE_NEW, MODE_REPEAT):
        raise HTTPException(400, {"code": "bad_mode", "message": "Неизвестный режим"})

    now = crud.utcnow()
    progress = await crud.card_progress(db, user.id, deck.id)

    if mode == MODE_REPEAT:
        ready = [card for card, state in _waiting(deck.id, progress) if state.ready(now)]
        if len(ready) < REPEAT_MIN:
            raise HTTPException(400, {
                "code": "not_enough_due",
                "message": "Ты сможешь повторить, когда хотя бы у 5 слов истечет таймер. "
                           "Это самая рабочая методика заучивания",
            })
        chosen = ready[:SESSION_SIZE]
    else:
        chosen = [card for card in cards_meta.cards(deck.id) if card.key not in progress]
        random.shuffle(chosen)
        chosen = chosen[:SESSION_SIZE]

    return {
        "deck": deck.id,
        "mode": mode,
        "cards": [card.payload() for card in chosen],
        **_counts(deck.id, progress, now),
    }


@router.post("/cards/{deck_id}/answer")
async def answer(deck_id: str, payload: CardAnswer, user: CurrentUser, db: DbSession) -> dict:
    """Отмечает карточку. Пишется сразу — подход часто бросают на середине."""
    deck = _deck_or_404(deck_id)
    if cards_meta.by_key(deck.id, payload.card) is None:
        raise HTTPException(404, {"code": "no_such_card", "message": "Такого слова в колоде нет"})

    stage = await crud.mark_card(db, user.id, deck.id, payload.card, payload.known)
    progress = await crud.card_progress(db, user.id, deck.id)
    state = progress.get(payload.card)
    return {
        "ok": True,
        # Этап слова после ответа: по нему экран подхода говорит, вернётся оно
        # через восемь часов, через сутки или не вернётся вовсе.
        "stage": stage,
        "due_at": as_utc(state.due_at).isoformat() if state and state.due_at else None,
        **_counts(deck.id, progress, crud.utcnow()),
    }


@router.post("/cards/{deck_id}/reset")
async def reset(deck_id: str, user: CurrentUser, db: DbSession) -> dict:
    """Забывает прогресс по колоде целиком — и выученные, и отложенные."""
    deck = _deck_or_404(deck_id)
    forgotten = await crud.reset_cards(db, user.id, deck.id)
    return {"ok": True, "forgotten": forgotten, **_counts(deck.id, {}, crud.utcnow())}
