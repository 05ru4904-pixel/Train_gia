"""Основной бот: единственная задача — открыть Mini App (ТЗ п.22)."""
import logging

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonWebApp,
    Message,
    WebAppInfo,
)

from api.main import APP_VERSION
from config import settings

log = logging.getLogger(__name__)
router = Router(name="user")

WELCOME = (
    "<b>Тренажёр ЕГЭ по русскому языку</b>\n\n"
    "Внутри — задания №1-26, полные варианты с таймером и статистика по каждому номеру.\n\n"
    "Нажмите кнопку ниже, чтобы начать."
)

NO_WEBAPP = (
    "Приложение ещё не подключено.\n\n"
    "Администратору: задайте переменную окружения <code>WEBAPP_URL</code> — "
    "публичный https-адрес сервиса с путём <code>/app</code>."
)


def webapp_url() -> str | None:
    """URL Mini App с версией. Telegram принимает только https (playbook 2.5)."""
    url = settings.webapp_url.strip()
    if not url.startswith("https://"):
        if url:
            log.error("WEBAPP_URL должен начинаться с https://, а сейчас: %r", url)
        return None
    return settings.webapp_url_versioned(APP_VERSION)


def open_keyboard(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Открыть тренажёр", web_app=WebAppInfo(url=url))]]
    )


@router.message(CommandStart())
async def start(message: Message) -> None:
    url = webapp_url()
    if url is None:
        await message.answer(NO_WEBAPP)
        return
    await message.answer(WELCOME, reply_markup=open_keyboard(url))


@router.message()
async def fallback(message: Message) -> None:
    url = webapp_url()
    if url is None:
        await message.answer(NO_WEBAPP)
        return
    await message.answer("Тренировка проходит в приложении:", reply_markup=open_keyboard(url))


async def setup_menu_button(bot: Bot) -> None:
    """Кнопка меню рядом с полем ввода — самый короткий путь в приложение."""
    url = webapp_url()
    if url is None:
        return
    try:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="Тренажёр", web_app=WebAppInfo(url=url))
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Не удалось выставить кнопку меню: %s", exc)


def build() -> tuple[Bot, Dispatcher]:
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    return bot, dispatcher


async def run() -> None:
    if not settings.bot_token:
        log.error("BOT_TOKEN не задан — основной бот не запущен")
        return
    bot, dispatcher = build()
    await setup_menu_button(bot)
    await bot.delete_webhook(drop_pending_updates=True)
    log.info("Основной бот запущен")
    await dispatcher.start_polling(bot)
