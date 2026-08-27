"""Админ-бот: управление базой заданий и вариантов (ТЗ п.16-19).

Отдельный бот со своим токеном — чтобы ученики физически не видели админских команд.
Доступ дополнительно ограничен списком ADMIN_IDS.
"""
import html
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import settings
from core.parser import (
    ParseError,
    normalize_task_id,
    parse_task,
    parse_task_batch,
    parse_variant,
)
from core.tasks_meta import LAST_TASK, TASK_NUMBERS, letter, title
from db.crud import (
    create_task,
    create_variant,
    delete_task,
    delete_variant,
    get_task,
    get_variant_by_number,
    list_tasks,
    list_variants,
    missing_task_ids,
    replace_variant_items,
    task_counts,
    total_tasks_count,
    update_task,
    users_count,
    variants_count,
)
from db.database import SessionMaker

log = logging.getLogger(__name__)
router = Router(name="admin")

TEMPLATE = (
    "Задание №4\n"
    "В каком слове правильно поставлено ударение?\n"
    "А) звонИт\n"
    "Б) звОнит\n"
    "В) позвонИт\n"
    "Г) позвОнит\n"
    "Ответ: А"
)

MENU = (
    "<b>Админ-панель тренажёра ЕГЭ</b>\n\n"
    "/add — добавить задание\n"
    "/batch — добавить сразу несколько заданий (целый вариант)\n"
    "/find — найти задание по ID\n"
    "/list — задания по номеру\n"
    "/variant — собрать вариант из существующих ID\n"
    "/variants — список вариантов\n"
    "/stats — что сейчас в базе\n"
    "/cancel — отменить текущее действие"
)


class Add(StatesGroup):
    waiting = State()


class Batch(StatesGroup):
    waiting = State()


class Find(StatesGroup):
    waiting = State()


class Edit(StatesGroup):
    waiting = State()


class Build(StatesGroup):
    waiting = State()


class Listing(StatesGroup):
    waiting = State()


def is_admin(user_id: int | None) -> bool:
    allowed = settings.admin_id_set
    if not allowed:
        # Пустой список — это не «пускать всех», а «бот не настроен».
        return False
    return user_id in allowed


def task_card(task) -> str:
    lines = [
        f"<b>№{task.number} · {html.escape(title(task.number))}</b>",
        f"ID: <code>{task.id}</code>",
        "",
        html.escape(task.text),
        "",
    ]
    for i, option in enumerate(task.options or []):
        mark = " ✅" if i in (task.correct or []) else ""
        lines.append(f"{letter(i)}) {html.escape(str(option))}{mark}")
    lines.append("")
    lines.append("Ответ: " + ", ".join(letter(i) for i in (task.correct or [])))
    return "\n".join(lines)


def task_keyboard(task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Редактировать", callback_data=f"edit:{task_id}"),
        InlineKeyboardButton(text="Удалить", callback_data=f"del:{task_id}"),
    ]])


# --------------------------------------------------------------------------- #
# Общее
# --------------------------------------------------------------------------- #
@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(MENU)


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    await state.clear()
    await message.answer("Отменено." if current else "Нечего отменять.")


@router.message(Command("stats"))
async def stats(message: Message) -> None:
    async with SessionMaker() as db:
        counts = await task_counts(db)
        total = await total_tasks_count(db)
        variants = await variants_count(db)
        users = await users_count(db)

    empty = [n for n in TASK_NUMBERS if not counts.get(n)]
    lines = [
        "<b>База</b>",
        f"Заданий: {total}",
        f"Вариантов: {variants}",
        f"Пользователей: {users}",
        "",
        "<b>По номерам</b>",
    ]
    lines += [f"№{n}: {counts.get(n, 0)}" for n in TASK_NUMBERS if counts.get(n)]
    if empty:
        lines.append("")
        lines.append("Пока пусто: " + ", ".join(f"№{n}" for n in empty))
    await message.answer("\n".join(lines))


# --------------------------------------------------------------------------- #
# Добавление задания
# --------------------------------------------------------------------------- #
@router.message(Command("add"))
async def add_prompt(message: Message, state: FSMContext) -> None:
    await state.set_state(Add.waiting)
    await message.answer(
        "Пришлите задание по шаблону:\n\n"
        f"<pre>{html.escape(TEMPLATE)}</pre>\n"
        "Вариантов может быть сколько угодно. Для нескольких правильных ответов: "
        "<code>Ответ: А, В, Д</code>\n\n"
        "ID присвоится сам. /cancel — отмена."
    )


@router.message(Add.waiting, F.text)
async def add_save(message: Message, state: FSMContext) -> None:
    try:
        parsed = parse_task(message.text)
    except ParseError as exc:
        await message.answer(f"❌ {exc}")
        return

    async with SessionMaker() as db:
        task = await create_task(db, parsed)

    await state.clear()
    await message.answer(
        f"✅ Задание добавлено\n\n№{task.number}\nID: <code>{task.id}</code>",
        reply_markup=task_keyboard(task.id),
    )


# --------------------------------------------------------------------------- #
# Пакетное добавление (целый вариант, ТЗ п.18)
# --------------------------------------------------------------------------- #
@router.message(Command("batch"))
async def batch_prompt(message: Message, state: FSMContext) -> None:
    await state.set_state(Batch.waiting)
    await message.answer(
        "Пришлите несколько заданий одним сообщением — подряд, каждое начинается со "
        "строки <b>Задание №N</b>.\n\n"
        "Каждое сохранится отдельно и получит свой ID. Если пришлёте все 26 номеров, "
        "предложу сразу собрать из них вариант.\n\n/cancel — отмена."
    )


@router.message(Batch.waiting, F.text)
async def batch_save(message: Message, state: FSMContext) -> None:
    try:
        parsed_tasks = parse_task_batch(message.text)
    except ParseError as exc:
        await message.answer(f"❌ {exc}")
        return

    async with SessionMaker() as db:
        created = [await create_task(db, parsed) for parsed in parsed_tasks]

    lines = [f"✅ Добавлено заданий: {len(created)}", ""]
    lines += [f"№{t.number} — <code>{t.id}</code>" for t in created]

    by_number = {}
    for task in created:
        by_number.setdefault(task.number, task.id)
    complete = all(n in by_number for n in TASK_NUMBERS)

    if complete:
        await state.update_data(variant_ids={str(n): by_number[n] for n in TASK_NUMBERS})
        lines += ["", "Пришли все 26 номеров — собрать из них вариант?"]
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Собрать вариант", callback_data="mkvariant"),
            InlineKeyboardButton(text="Не нужно", callback_data="novariant"),
        ]])
        await message.answer("\n".join(lines), reply_markup=keyboard)
    else:
        await state.clear()
        missing = [n for n in TASK_NUMBERS if n not in by_number]
        if missing:
            lines += ["", "Не хватает для полного варианта: " + ", ".join(f"№{n}" for n in missing)]
        await message.answer("\n".join(lines))


@router.callback_query(F.data == "novariant")
async def batch_no_variant(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Задания сохранены")


@router.callback_query(F.data == "mkvariant")
async def batch_make_variant(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    raw_ids = data.get("variant_ids") or {}
    if not raw_ids:
        await callback.answer("Список заданий потерялся, соберите вариант через /variant", show_alert=True)
        return

    task_ids = {int(k): v for k, v in raw_ids.items()}
    async with SessionMaker() as db:
        existing = {v.number for v in await list_variants(db, limit=1000)}
        number = next(n for n in range(1, 10_000) if n not in existing)
        variant = await create_variant(db, number, task_ids)

    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        f"✅ Вариант №{variant.number} собран из {len(task_ids)} заданий."
    )
    await callback.answer()


# --------------------------------------------------------------------------- #
# Поиск, редактирование, удаление
# --------------------------------------------------------------------------- #
@router.message(Command("find"))
async def find_prompt(message: Message, state: FSMContext, command: Command = None) -> None:
    argument = (message.text or "").partition(" ")[2].strip()
    if argument:
        await show_task(message, argument)
        return
    await state.set_state(Find.waiting)
    await message.answer("Пришлите ID задания, например <code>K7F29A</code>. /cancel — отмена.")


@router.message(Find.waiting, F.text)
async def find_do(message: Message, state: FSMContext) -> None:
    await state.clear()
    await show_task(message, message.text)


async def show_task(message: Message, raw_id: str) -> None:
    task_id = normalize_task_id(raw_id)
    async with SessionMaker() as db:
        task = await get_task(db, task_id)
    if task is None:
        await message.answer(f"Задание с ID <code>{html.escape(task_id)}</code> не найдено.")
        return
    await message.answer(task_card(task), reply_markup=task_keyboard(task.id))


@router.message(Command("list"))
async def list_prompt(message: Message, state: FSMContext) -> None:
    argument = (message.text or "").partition(" ")[2].strip()
    if argument.isdigit():
        await show_list(message, int(argument))
        return
    await state.set_state(Listing.waiting)
    await message.answer(f"Пришлите номер задания от 1 до {LAST_TASK}. /cancel — отмена.")


@router.message(Listing.waiting, F.text)
async def list_do(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit() or not 1 <= int(raw) <= LAST_TASK:
        await message.answer(f"Нужен номер от 1 до {LAST_TASK}.")
        return
    await state.clear()
    await show_list(message, int(raw))


async def show_list(message: Message, number: int) -> None:
    async with SessionMaker() as db:
        tasks = await list_tasks(db, number, limit=30)
        counts = await task_counts(db)
    total = counts.get(number, 0)
    if not tasks:
        await message.answer(f"Заданий №{number} в базе пока нет.")
        return
    lines = [f"<b>№{number} · {html.escape(title(number))}</b>", f"Всего: {total}", ""]
    for task in tasks:
        preview = task.text.replace("\n", " ")
        if len(preview) > 60:
            preview = preview[:57] + "…"
        lines.append(f"<code>{task.id}</code> — {html.escape(preview)}")
    if total > len(tasks):
        lines.append("")
        lines.append(f"Показаны последние {len(tasks)} из {total}.")
    await message.answer("\n".join(lines))


@router.callback_query(F.data.startswith("edit:"))
async def edit_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    task_id = callback.data.split(":", 1)[1]
    await state.set_state(Edit.waiting)
    await state.update_data(task_id=task_id)
    await callback.message.answer(
        f"Пришлите новую версию задания <code>{task_id}</code> целиком, по тому же шаблону.\n"
        "ID сохранится. /cancel — отмена."
    )
    await callback.answer()


@router.message(Edit.waiting, F.text)
async def edit_save(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    task_id = data.get("task_id")
    try:
        parsed = parse_task(message.text)
    except ParseError as exc:
        await message.answer(f"❌ {exc}")
        return

    async with SessionMaker() as db:
        task = await get_task(db, task_id)
        if task is None:
            await state.clear()
            await message.answer("Задание уже удалено.")
            return
        task = await update_task(db, task, parsed)
        card = task_card(task)

    await state.clear()
    await message.answer("✅ Задание обновлено\n\n" + card, reply_markup=task_keyboard(task_id))


@router.callback_query(F.data.startswith("del:"))
async def delete_confirm(callback: CallbackQuery) -> None:
    task_id = callback.data.split(":", 1)[1]
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Да, удалить", callback_data=f"delyes:{task_id}"),
        InlineKeyboardButton(text="Отмена", callback_data=f"delno:{task_id}"),
    ]])
    await callback.message.answer(
        f"Удалить задание <code>{task_id}</code>? Это действие необратимо.",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("delno:"))
async def delete_cancel(callback: CallbackQuery) -> None:
    await callback.message.edit_text("Удаление отменено.")
    await callback.answer()


@router.callback_query(F.data.startswith("delyes:"))
async def delete_do(callback: CallbackQuery) -> None:
    task_id = callback.data.split(":", 1)[1]
    async with SessionMaker() as db:
        task = await get_task(db, task_id)
        if task is None:
            await callback.message.edit_text("Задание уже удалено.")
            await callback.answer()
            return
        try:
            await delete_task(db, task)
        except Exception as exc:  # noqa: BLE001
            # Задание может входить в собранный вариант — тогда внешний ключ не даст удалить.
            log.warning("Не удалось удалить задание %s: %s", task_id, exc)
            await callback.message.edit_text(
                f"Не получилось удалить <code>{task_id}</code>: задание входит в собранный "
                "вариант. Сначала уберите его из варианта."
            )
            await callback.answer()
            return
    await callback.message.edit_text(f"✅ Задание <code>{task_id}</code> удалено.")
    await callback.answer()


# --------------------------------------------------------------------------- #
# Варианты
# --------------------------------------------------------------------------- #
@router.message(Command("variant"))
async def build_prompt(message: Message, state: FSMContext) -> None:
    await state.set_state(Build.waiting)
    await message.answer(
        "Пришлите сборку варианта:\n\n"
        "<pre>Вариант №10\n1: A72K91\n2: B82L43\n3: K19D52\n...\n26: M72P81</pre>\n"
        "Одно и то же задание может входить в разные варианты.\n"
        "Если вариант с таким номером уже есть, его состав будет заменён.\n\n/cancel — отмена."
    )


@router.message(Build.waiting, F.text)
async def build_save(message: Message, state: FSMContext) -> None:
    try:
        parsed = parse_variant(message.text)
    except ParseError as exc:
        await message.answer(f"❌ {exc}")
        return

    async with SessionMaker() as db:
        missing = await missing_task_ids(db, list(parsed.task_ids.values()))
        if missing:
            await message.answer(
                "❌ Не нашёл в базе задания с ID: "
                + ", ".join(f"<code>{m}</code>" for m in missing)
                + "\n\nПроверьте ID и пришлите сборку заново."
            )
            return

        existing = await get_variant_by_number(db, parsed.number)
        if existing is not None:
            variant = await replace_variant_items(db, existing, parsed.task_ids)
            verb = "обновлён"
        else:
            variant = await create_variant(db, parsed.number, parsed.task_ids)
            verb = "собран"

    await state.clear()
    absent = [n for n in TASK_NUMBERS if n not in parsed.task_ids]
    text = f"✅ Вариант №{variant.number} {verb}: {len(parsed.task_ids)} заданий."
    if absent:
        text += "\n\nНе заполнены номера: " + ", ".join(f"№{n}" for n in absent)
    await message.answer(text)


@router.message(Command("variants"))
async def variants_list(message: Message) -> None:
    async with SessionMaker() as db:
        variants = await list_variants(db, limit=100)
        rows = [(v.number, len(v.items)) for v in variants]
    if not rows:
        await message.answer("Собранных вариантов пока нет. Соберите первый: /variant")
        return
    lines = ["<b>Варианты</b>", ""]
    lines += [f"№{number} — заданий: {count}" for number, count in rows]
    await message.answer("\n".join(lines))


@router.message(Command("delvariant"))
async def variant_delete(message: Message) -> None:
    argument = (message.text or "").partition(" ")[2].strip()
    if not argument.isdigit():
        await message.answer("Укажите номер: <code>/delvariant 10</code>")
        return
    async with SessionMaker() as db:
        variant = await get_variant_by_number(db, int(argument))
        if variant is None:
            await message.answer(f"Варианта №{argument} нет.")
            return
        await delete_variant(db, variant)
    await message.answer(f"✅ Вариант №{argument} удалён. Сами задания остались в базе.")


@router.message(StateFilter(None))
async def unknown(message: Message) -> None:
    await message.answer("Не понял команду.\n\n" + MENU)


# --------------------------------------------------------------------------- #
# Запуск
# --------------------------------------------------------------------------- #
def build() -> tuple[Bot, Dispatcher]:
    bot = Bot(
        token=settings.admin_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher(storage=MemoryStorage())

    @dispatcher.update.outer_middleware()
    async def only_admins(handler, event, data):
        """Единственная точка контроля доступа — раньше любого хендлера."""
        user = data.get("event_from_user")
        if user is None or not is_admin(user.id):
            log.warning("Отклонён доступ к админ-боту: %s", getattr(user, "id", None))
            return None
        return await handler(event, data)

    dispatcher.include_router(router)
    return bot, dispatcher


async def run() -> None:
    if not settings.admin_bot_token:
        log.error("ADMIN_BOT_TOKEN не задан — админ-бот не запущен")
        return
    if not settings.admin_id_set:
        log.error("ADMIN_IDS пуст — админ-бот запущен, но никого не пустит")
    bot, dispatcher = build()
    await bot.delete_webhook(drop_pending_updates=True)
    log.info("Админ-бот запущен, админов: %s", len(settings.admin_id_set))
    await dispatcher.start_polling(bot)
