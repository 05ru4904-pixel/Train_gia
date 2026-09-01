"""Анкета ученика: класс, предметы ЕГЭ и цель по баллам.

Заполняется при первом входе в Mini App и правится потом из профиля. Справочник и
правила проверки живут в `core/profile_meta.py` — здесь только HTTP.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.deps import CurrentUser, DbSession
from api.serializers import onboarding_payload
from core import profile_meta
from db import crud

router = APIRouter(tags=["onboarding"])


class Onboarding(BaseModel):
    # Границы класса не дублируем: их проверяет profile_meta.validate, и текст
    # ошибки оттуда виден ученику как есть.
    grade: int
    math_level: str
    # Предметы по выбору. Русский сюда не входит: его сдают все, и он не выбирается.
    subjects: list[str] = Field(default_factory=list, max_length=8)
    target_score: str


@router.get("/onboarding/options")
async def options(user: CurrentUser) -> dict:
    """Из чего ученик выбирает. Отдаётся отдельно, чтобы не утяжелять /state."""
    return profile_meta.options_payload()


@router.post("/onboarding")
async def save(payload: Onboarding, user: CurrentUser, db: DbSession) -> dict:
    """Сохраняет анкету. Проверка идёт на сервере: клиенту верить нельзя.

    Главное правило здесь — число предметов по выбору: с профильной математикой
    ровно один, с базовой ровно два.
    """
    problem = profile_meta.validate(
        payload.grade, payload.math_level, payload.subjects, payload.target_score
    )
    if problem:
        raise HTTPException(400, {"code": "bad_onboarding", "message": problem})

    user = await crud.save_onboarding(
        db, user, payload.grade, payload.math_level, payload.subjects, payload.target_score
    )
    return onboarding_payload(user)
