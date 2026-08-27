"""Проверка подписи initData Telegram Mini App (playbook 5.7).

Клиенту нельзя верить на слово: Telegram ID приходит из веб-приложения, и без
проверки подписи любой мог бы прислать чужой id и получить чужую статистику.
Подпись проверяется на КАЖДЫЙ запрос к API.
"""
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl

from fastapi import Header, HTTPException

from config import settings

log = logging.getLogger(__name__)

INIT_DATA_HEADER = "X-Telegram-Init-Data"

# initData считается протухшей через сутки: Telegram переоткрывает WebView из памяти
# и может отдать давнюю строку, но бесконечно принимать её тоже нельзя.
MAX_AUTH_AGE_SEC = 24 * 60 * 60


@dataclass(frozen=True)
class TelegramUser:
    id: int
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None


def _secret_key(bot_token: str) -> bytes:
    return hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()


def verify_init_data(init_data: str, bot_token: str) -> dict:
    """Возвращает разобранные поля initData или бросает ValueError."""
    if not init_data:
        raise ValueError("initData пустая")

    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise ValueError("в initData нет hash")

    check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    expected = hmac.new(_secret_key(bot_token), check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        raise ValueError("подпись initData не сходится")

    auth_date = pairs.get("auth_date")
    if auth_date and auth_date.isdigit():
        age = time.time() - int(auth_date)
        if age > MAX_AUTH_AGE_SEC:
            raise ValueError("initData устарела, переоткройте приложение")

    return pairs


def parse_user(pairs: dict) -> TelegramUser:
    raw_user = pairs.get("user")
    if not raw_user:
        raise ValueError("в initData нет данных пользователя")
    try:
        data = json.loads(raw_user)
    except json.JSONDecodeError as exc:
        raise ValueError("данные пользователя в initData повреждены") from exc
    if "id" not in data:
        raise ValueError("в initData нет id пользователя")
    return TelegramUser(
        id=int(data["id"]),
        first_name=data.get("first_name"),
        last_name=data.get("last_name"),
        username=data.get("username"),
    )


async def current_user(
    x_telegram_init_data: str = Header(default="", alias=INIT_DATA_HEADER),
) -> TelegramUser:
    """Зависимость FastAPI: достаёт проверенного пользователя из заголовка."""
    if not settings.bot_token:
        log.error("BOT_TOKEN не задан — проверить initData невозможно")
        raise HTTPException(status_code=503, detail="Сервер не настроен")
    try:
        pairs = verify_init_data(x_telegram_init_data, settings.bot_token)
        return parse_user(pairs)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
