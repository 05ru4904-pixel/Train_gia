"""Общие зависимости роутеров."""
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import TelegramUser, current_user
from db.crud import get_or_create_user
from db.database import SessionMaker
from db.models import User


async def get_db():
    async with SessionMaker() as db:
        yield db


DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_user(
    telegram_user: Annotated[TelegramUser, Depends(current_user)],
    db: DbSession,
) -> User:
    """Пользователь по проверенной подписи. Заводится при первом обращении (ТЗ п.14)."""
    return await get_or_create_user(
        db,
        user_id=telegram_user.id,
        first_name=telegram_user.first_name,
        last_name=telegram_user.last_name,
        username=telegram_user.username,
    )


CurrentUser = Annotated[User, Depends(get_user)]
