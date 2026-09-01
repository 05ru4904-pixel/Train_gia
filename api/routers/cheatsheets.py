"""Раздел «Шпаргалки»: чек-листы по заданиям №1-26."""
from fastapi import APIRouter, HTTPException

from api.deps import CurrentUser
from core import cheatsheets
from core.tasks_meta import is_valid_number, subtitle, title

router = APIRouter(tags=["cheatsheets"])


@router.get("/cheatsheets")
async def index(user: CurrentUser) -> dict:
    """Список всех заданий с пометкой, готова ли шпаргалка."""
    items = cheatsheets.index()
    return {"items": items, "ready": cheatsheets.ready_count(), "total": len(items)}


@router.get("/cheatsheets/{number}")
async def sheet(number: int, user: CurrentUser) -> dict:
    """Текст одной шпаргалки. Ненаписанная — не ошибка, а честное «пока пусто»."""
    if not is_valid_number(number):
        raise HTTPException(404, {"code": "no_such_task", "message": "Такого задания нет"})
    return {
        "number": number,
        "title": title(number),
        "subtitle": subtitle(number),
        "body": cheatsheets.body(number),
    }
