"""Полный вариант ЕГЭ: запуск, таймер, пауза (ТЗ п.8, 9, 10)."""
from fastapi import APIRouter, HTTPException

from api.deps import CurrentUser, DbSession
from api.serializers import result_payload, session_payload, timer_payload
from core.tasks_meta import LAST_TASK
from db import crud
from db.models import KIND_VARIANT, STATUS_FINISHED

router = APIRouter(tags=["variant"])


@router.post("/variant/start")
async def start(user: CurrentUser, db: DbSession, variant_id: int | None = None) -> dict:
    """Запускает полный вариант №1-26. Таймер стартует сразу."""
    try:
        session = await crud.start_variant(db, user.id, variant_id)
    except crud.NoVariantAvailable as exc:
        missing = ", ".join(f"№{n}" for n in exc.missing[:10])
        tail = " и другие" if len(exc.missing) > 10 else ""
        raise HTTPException(
            status_code=409,
            detail={
                "code": "no_variant",
                "missing": exc.missing,
                "message": (
                    f"Не хватает заданий, чтобы собрать вариант из {LAST_TASK} номеров. "
                    f"В базе нет заданий: {missing}{tail}."
                ),
            },
        ) from exc
    return session_payload(session, list(session.items))


@router.post("/variant/pause")
async def pause(user: CurrentUser, db: DbSession) -> dict:
    """Останавливает таймер. Предупреждение о последствиях показывает Mini App (ТЗ п.9)."""
    session = await _active_variant(user, db)
    session = await crud.pause_timer(db, session)
    return {"timer": timer_payload(session)}


@router.post("/variant/resume")
async def resume(user: CurrentUser, db: DbSession) -> dict:
    session = await _active_variant(user, db)
    session = await crud.resume_timer(db, session)
    return {"timer": timer_payload(session)}


@router.get("/variant/timer")
async def timer(user: CurrentUser, db: DbSession) -> dict:
    """Сверка времени с сервером. Клиентский таймер только рисует секунды,
    источник правды — сервер: время идёт и когда приложение закрыто (ТЗ п.9)."""
    session = await _active_variant(user, db)
    session = await crud.finish_if_time_is_up(db, session)
    if session.status == STATUS_FINISHED:
        return {"finished": True, "result": result_payload(session, list(session.items))}
    return {"finished": False, "timer": timer_payload(session)}


async def _active_variant(user, db):
    session = await crud.get_active_session(db, user.id)
    if session is None or session.kind != KIND_VARIANT:
        raise HTTPException(404, "Активного варианта нет")
    return session
