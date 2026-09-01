"""Стартовое состояние, список заданий и профиль."""
from fastapi import APIRouter

from api.deps import CurrentUser, DbSession
from api.serializers import onboarding_payload, unfinished_payload
from core import scoring
from core.tasks_meta import RENDERABLE_KINDS, TASK_NUMBERS, subtitle, title
from db import crud
from db.models import PLAN_PRO

router = APIRouter(tags=["app"])


@router.get("/state")
async def state(user: CurrentUser, db: DbSession) -> dict:
    """Всё, что нужно для первой отрисовки: кто вошёл, есть ли незавершённая
    тренировка и хватает ли в базе заданий.

    Отдельный запрос на старте — источник правды: приложение показывает сплэш и не
    мигает не тем экраном, пока ответ не пришёл (playbook 5.5).
    """
    active = await crud.get_active_session(db, user.id)
    if active is not None:
        active = await crud.finish_if_time_is_up(db, active)
        if active.status != "active":
            active = None

    counts = await crud.task_counts(db, kinds=RENDERABLE_KINDS)
    return {
        "user": {
            "id": user.id,
            "name": user.display_name,
            "username": user.username,
            "plan": user.plan,
            "is_pro": user.plan == PLAN_PRO,
        },
        # Пока анкета не заполнена, приложение показывает её вместо главного экрана.
        "needs_onboarding": user.onboarded_at is None,
        "unfinished": unfinished_payload(active, list(active.items) if active else []),
        "counts": {str(n): counts.get(n, 0) for n in TASK_NUMBERS},
        "variants_available": await crud.variants_count(db) > 0,
        "tasks_total": sum(counts.values()),
        "training_counts": list(scoring.TRAINING_COUNTS),
        "variant_time_limit": scoring.VARIANT_TIME_LIMIT_SEC,
    }


@router.get("/tasks")
async def tasks(user: CurrentUser, db: DbSession) -> dict:
    """Список заданий №1-26 с темами и количеством доступных вопросов (ТЗ п.3.1)."""
    counts = await crud.task_counts(db, kinds=RENDERABLE_KINDS)
    return {
        "tasks": [
            {
                "number": n,
                "title": title(n),
                "subtitle": subtitle(n),
                "available": counts.get(n, 0),
            }
            for n in TASK_NUMBERS
        ],
        "counts": list(scoring.TRAINING_COUNTS),
    }


@router.get("/profile")
async def profile(user: CurrentUser, db: DbSession) -> dict:
    """Данные профиля (ТЗ п.13). Подписка пока только отображается."""
    overall = await crud.overall_stats(db, user.id)
    return {
        "name": user.display_name,
        "username": user.username,
        "plan": user.plan,
        "is_pro": user.plan == PLAN_PRO,
        "plan_until": user.plan_until.isoformat() if user.plan_until else None,
        "registered_at": user.created_at.isoformat() if user.created_at else None,
        "solved_total": overall["total"],
        "accuracy": overall["accuracy"],
        "onboarding": onboarding_payload(user),
    }
