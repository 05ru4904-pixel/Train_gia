"""Паронимы: тренажёр к заданию №5.

Отдельный раздел со своим словником, своей таблицей прогресса и своими часами.
Механика та же, что у карточек ударений, но код общего с ними не имеет — задания
разные, и правка здесь не должна доставать до задания №4.

* **Основной тренажёр** — только новые группы, десять за подход, один проход.
  «Знаю» закрывает группу сразу, «Не знаю» кладёт её в слабые и запускает
  восьмичасовой таймер.
* **«Повторить»** — только группы с истёкшим таймером. Открывается, когда созрело
  хотя бы пять. «Знаю» двигает группу: 8 часов -> 24 часа -> выучено. «Не знаю»
  не меняет ничего, группа просто вернётся в этом же подходе — за возврат
  отвечает клиент, сервер только записывает ответы.

Единица прогресса — вся группа: закрылось «эффектный / эффективный» целиком.
"""
import random
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.deps import CurrentUser, DbSession
from core import paronyms as words
from db import crud
from db.crud import ParonymState, as_utc

router = APIRouter(tags=["paronyms"])

# Сколько групп в одном подходе — столько же, сколько карточек в ударениях:
# подход должен заканчиваться раньше, чем надоест.
SESSION_SIZE = 10

# Пока столько групп не созреет, повторение не открывается.
REPEAT_MIN = 5

MODE_NEW = "new"
MODE_REPEAT = "repeat"

LOCKED_MESSAGE = ("Ты сможешь повторить, когда хотя бы у 5 слов истечет таймер. "
                  "Это самая рабочая методика заучивания")

# Метка для строк без времени: они уходят в начало сортировки.
OLDEST = datetime(1970, 1, 1, tzinfo=timezone.utc)


class ParonymAnswer(BaseModel):
    card: str = Field(min_length=1, max_length=64)
    known: bool


def _moment(value: datetime | None) -> datetime:
    """Время для сортировки: пустое считаем давно прошедшим."""
    return as_utc(value) if value else OLDEST


def _waiting(progress: dict[str, ParonymState]) -> list[tuple]:
    """Слабые паронимы: группы, отложенные и ещё не закрытые.

    Порядок — по сроку, у кого раньше истекает, тот первый.
    """
    rows = [(card, progress[card.key]) for card in words.cards()
            if card.key in progress and not progress[card.key].learned]
    rows.sort(key=lambda pair: _moment(pair[1].due_at))
    return rows


def _counts(progress: dict[str, ParonymState], now: datetime) -> dict:
    """Цифры для экрана словника."""
    all_cards = words.cards()
    waiting = _waiting(progress)
    ready = [pair for pair in waiting if pair[1].ready(now)]
    learned = sum(1 for card in all_cards
                  if (state := progress.get(card.key)) is not None and state.learned)

    # Когда откроется повторение: срок пятой группы в очереди.
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


def _deck_payload(progress: dict[str, ParonymState], now: datetime) -> dict:
    return {**words.deck_payload(), "size": SESSION_SIZE, **_counts(progress, now)}


@router.get("/paronyms")
async def state(user: CurrentUser, db: DbSession) -> dict:
    """Состояние словника — экран перед подходом."""
    progress = await crud.paronym_progress(db, user.id)
    return _deck_payload(progress, crud.utcnow())


@router.get("/paronyms/weak")
async def weak(user: CurrentUser, db: DbSession) -> dict:
    """Слабые паронимы: что сейчас на повторе, с этапом и таймером.

    Порядок — по мере попадания в список: строка прогресса заводится первым
    «Не знаю» и больше не пересоздаётся, так что её номер и есть очерёдность.
    """
    now = crud.utcnow()
    progress = await crud.paronym_progress(db, user.id)

    rows = [(card, progress[card.key]) for card in words.cards()
            if card.key in progress and not progress[card.key].learned]
    rows.sort(key=lambda pair: pair[1].row_id)

    return {
        "cards": [
            dict(
                card.payload(),
                stage=state.status,
                ready=state.ready(now),
                due_at=as_utc(state.due_at).isoformat() if state.due_at else None,
            )
            for card, state in rows
        ],
        **_counts(progress, now),
    }


@router.get("/paronyms/learned")
async def learned(user: CurrentUser, db: DbSession) -> dict:
    """Выученные паронимы. Свежие сверху: заходят обычно за тем, что закрыли только что."""
    now = crud.utcnow()
    progress = await crud.paronym_progress(db, user.id)

    rows = [(card, progress[card.key]) for card in words.cards()
            if card.key in progress and progress[card.key].learned]
    rows.sort(key=lambda pair: _moment(pair[1].updated_at), reverse=True)

    return {
        "cards": [card.payload() for card, _ in rows],
        **_counts(progress, now),
    }


@router.get("/paronyms/session")
async def session(user: CurrentUser, db: DbSession, mode: str = MODE_NEW) -> dict:
    """Набирает подход.

    `new` — десять новых групп в случайном порядке. `repeat` — до десяти
    созревших, у кого раньше истёк срок, тот первый; меньше пяти созревших —
    подход не собирается вовсе.
    """
    if mode not in (MODE_NEW, MODE_REPEAT):
        raise HTTPException(400, {"code": "bad_mode", "message": "Неизвестный режим"})

    now = crud.utcnow()
    progress = await crud.paronym_progress(db, user.id)

    if mode == MODE_REPEAT:
        ready = [card for card, state in _waiting(progress) if state.ready(now)]
        if len(ready) < REPEAT_MIN:
            raise HTTPException(400, {"code": "not_enough_due", "message": LOCKED_MESSAGE})
        chosen = ready[:SESSION_SIZE]
    else:
        chosen = [card for card in words.cards() if card.key not in progress]
        random.shuffle(chosen)
        chosen = chosen[:SESSION_SIZE]

    return {
        "mode": mode,
        "prompt": words.PROMPT,
        "cards": [card.payload() for card in chosen],
        **_counts(progress, now),
    }


@router.post("/paronyms/answer")
async def answer(payload: ParonymAnswer, user: CurrentUser, db: DbSession) -> dict:
    """Отмечает группу. Пишется сразу — подход часто бросают на середине."""
    if words.by_key(payload.card) is None:
        raise HTTPException(404, {"code": "no_such_card", "message": "Такой группы в словнике нет"})

    stage = await crud.mark_paronym(db, user.id, payload.card, payload.known)
    now = crud.utcnow()
    progress = await crud.paronym_progress(db, user.id)
    state = progress.get(payload.card)
    return {
        "ok": True,
        "stage": stage,
        "due_at": as_utc(state.due_at).isoformat() if state and state.due_at else None,
        **_counts(progress, now),
    }


@router.post("/paronyms/reset")
async def reset(user: CurrentUser, db: DbSession) -> dict:
    """Забывает словник целиком — и выученные, и слабые."""
    forgotten = await crud.reset_paronyms(db, user.id)
    return {"ok": True, "forgotten": forgotten, **_counts({}, crud.utcnow())}
