"""Обычная тренировка, ответы и результаты (ТЗ п.3.1, 4, 6, 7)."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.deps import CurrentUser, DbSession
from api.serializers import result_payload, session_payload
from core import scoring
from core.tasks_meta import LAST_TASK, is_valid_number
from db import crud
from db.models import KIND_TRAINING, KIND_VARIANT, STATUS_FINISHED

router = APIRouter(tags=["training"])


class StartTraining(BaseModel):
    number: int = Field(ge=1, le=LAST_TASK)
    count: int


class Answer(BaseModel):
    position: int = Field(ge=0)
    selected: list[int] = Field(min_length=1)


@router.post("/training/start")
async def start(payload: StartTraining, user: CurrentUser, db: DbSession) -> dict:
    """Запускает тренировку. Предыдущая незавершённая сбрасывается (ТЗ п.7)."""
    if not is_valid_number(payload.number):
        raise HTTPException(400, f"Задание должно быть от 1 до {LAST_TASK}")
    if payload.count not in scoring.TRAINING_COUNTS:
        allowed = ", ".join(str(c) for c in scoring.TRAINING_COUNTS)
        raise HTTPException(400, f"Количество вопросов может быть только: {allowed}")

    try:
        session = await crud.start_training(db, user.id, payload.number, payload.count)
    except crud.NotEnoughTasks as exc:
        # ТЗ п.4: если заданий в базе не хватает — сказать об этом прямо.
        raise HTTPException(
            status_code=409,
            detail={
                "code": "not_enough_tasks",
                "available": exc.available,
                "requested": exc.requested,
                "message": (
                    f"В базе пока {exc.available} заданий №{payload.number}, "
                    f"а нужно {exc.requested}. Выберите меньше вопросов."
                ),
            },
        ) from exc

    return session_payload(session, list(session.items))


@router.get("/session")
async def current(user: CurrentUser, db: DbSession, position: int | None = None) -> dict:
    """Состояние активной сессии — для продолжения с того же места (ТЗ п.7, 9)."""
    session = await crud.get_active_session(db, user.id)
    if session is None:
        raise HTTPException(404, "Активной тренировки нет")

    session = await crud.finish_if_time_is_up(db, session)
    items = list(session.items)
    if session.status == STATUS_FINISHED:
        return {"finished": True, "result": result_payload(session, items)}

    if position is not None and not any(i.position == position for i in items):
        raise HTTPException(400, "Такого задания в тренировке нет")
    return {"finished": False, "session": session_payload(session, items, position)}


@router.post("/session/answer")
async def answer(payload: Answer, user: CurrentUser, db: DbSession) -> dict:
    """Проверяет ответ.

    В обычной тренировке ответ окончательный — переспросить или переиграть нельзя
    (ТЗ п.4). В полном варианте ответ можно менять до завершения (ТЗ п.8).
    """
    session = await crud.get_active_session(db, user.id)
    if session is None:
        raise HTTPException(404, "Активной тренировки нет")

    session = await crud.finish_if_time_is_up(db, session)
    if session.status == STATUS_FINISHED:
        return {"finished": True, "result": result_payload(session, list(session.items))}

    item = await crud.get_item(db, session.id, payload.position)
    if item is None:
        raise HTTPException(400, "Такого задания в тренировке нет")

    if session.kind == KIND_TRAINING and item.answered:
        raise HTTPException(409, "На это задание вы уже ответили")

    option_count = len(item.task.options or [])
    if any(i < 0 or i >= option_count for i in payload.selected):
        raise HTTPException(400, "Выбран несуществующий вариант ответа")

    item = await crud.answer_item(db, item, payload.selected)

    items = list(session.items)
    all_answered = all(i.answered for i in items)

    # Тренировка без времени: если ответы кончились, завершаем сразу, чтобы
    # результат не потерялся, если пользователь просто закроет приложение.
    if session.kind == KIND_TRAINING and all_answered:
        session = await crud.finish_session(db, session)
        return {
            "finished": True,
            # Вердикт по последнему ответу нужен и здесь: сначала показывается
            # «верно/неверно», и только потом экран результата (ТЗ п.4).
            "position": item.position,
            "is_correct": item.is_correct,
            "correct": list(item.task.correct or []),
            "result": result_payload(session, list(session.items)),
        }

    reveal = session.kind != KIND_VARIANT
    response: dict = {
        "finished": False,
        "position": item.position,
        "answered": sum(1 for i in items if i.answered),
        "total": session.total,
    }
    if reveal:
        response["is_correct"] = item.is_correct
        response["correct"] = list(item.task.correct or [])
    return response


@router.post("/session/finish")
async def finish(user: CurrentUser, db: DbSession) -> dict:
    """Завершает сессию досрочно или по кнопке (ТЗ п.6, 8, 10)."""
    session = await crud.get_active_session(db, user.id)
    if session is None:
        raise HTTPException(404, "Активной тренировки нет")
    session = await crud.finish_session(db, session)
    return {"finished": True, "result": result_payload(session, list(session.items))}


@router.get("/session/{session_id}/result")
async def result(session_id: int, user: CurrentUser, db: DbSession) -> dict:
    """Результат ранее завершённой сессии — для истории и разбора ошибок."""
    session = await crud.get_session(db, session_id, user.id)
    if session is None or session.status != STATUS_FINISHED:
        raise HTTPException(404, "Результат не найден")
    return {"result": result_payload(session, list(session.items))}
