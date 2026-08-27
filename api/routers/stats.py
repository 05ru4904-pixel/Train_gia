"""Статистика с фильтром по датам (ТЗ п.11, 12)."""
from datetime import date, datetime, time, timezone

from fastapi import APIRouter, HTTPException

from api.deps import CurrentUser, DbSession
from api.serializers import history_payload
from core.tasks_meta import TASK_NUMBERS, subtitle, title
from db import crud

router = APIRouter(tags=["stats"])


def _parse_bound(raw: str | None, end_of_day: bool) -> datetime | None:
    """Дата из фильтра -> граница периода. Пустое значение означает «без границы»."""
    if not raw:
        return None
    try:
        parsed = date.fromisoformat(raw[:10])
    except ValueError as exc:
        raise HTTPException(400, f"Дата «{raw}» не распознана, нужен формат ГГГГ-ММ-ДД") from exc
    moment = time.max if end_of_day else time.min
    return datetime.combine(parsed, moment, tzinfo=timezone.utc)


@router.get("/stats")
async def stats(
    user: CurrentUser,
    db: DbSession,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    """Общая статистика, разбивка по заданиям и история вариантов за период.

    Все три блока считаются по одному и тому же фильтру дат, чтобы цифры на экране
    не расходились между собой (ТЗ п.12).
    """
    start = _parse_bound(date_from, end_of_day=False)
    end = _parse_bound(date_to, end_of_day=True)
    if start and end and start > end:
        raise HTTPException(400, "Начало периода позже его конца")

    overall = await crud.overall_stats(db, user.id, start, end)
    by_task = await crud.stats_by_task(db, user.id, start, end)
    history = await crud.variant_history(db, user.id, start, end)

    return {
        "range": {"from": date_from, "to": date_to},
        "overall": overall,
        "tasks": [
            {
                "number": n,
                "title": title(n),
                "subtitle": subtitle(n),
                **by_task.get(n, {"total": 0, "correct": 0, "wrong": 0, "accuracy": 0}),
            }
            for n in TASK_NUMBERS
        ],
        "variants": history_payload(history),
    }
