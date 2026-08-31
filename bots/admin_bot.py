"""Админ-бот: управление базой заданий и вариантов (ТЗ п.16-19).

Отдельный бот со своим токеном — чтобы ученики физически не видели админских команд.
Доступ дополнительно ограничен списком ADMIN_IDS.
"""
import html
import logging
from types import SimpleNamespace

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BufferedInputFile,
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
    parse_variant,
    to_template,
)
from core.raw_variant import parse as parse_raw_variant
from core.raw_variant import parse_one, render_preview
from core.tasks_meta import (
    KIND_CHOICE,
    KIND_DIGITS,
    KIND_MATCH,
    KIND_OPEN,
    LAST_TASK,
    TASK_NUMBERS,
    letter,
    title,
)
from core.variant_import import (
    import_tasks,
    load_payload,
    next_free_variant_number,
    to_parsed,
)
from db.crud import (
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

# Шаблоны ручного набора. Разбор у них общий с вариантом (core/raw_variant.py):
# вид берётся из номера задания, а не из формы сообщения. Поэтому варианты ответа
# подписываются цифрами — как в условиях ЕГЭ и как их печатает источник.
TEMPLATES = {
    KIND_CHOICE: """Задание 4
В каком слове правильно поставлено ударение? Выпишите это слово.
1) звонИт
2) звОнит
3) позвОнит
Ответ: 1""",
    KIND_OPEN: """Задание 5
Отредактируйте предложение: исправьте лексическую ошибку, исключив лишнее слово.
Выпишите это слово.
Ответ: заклятым, заклятый
Текст: Он всегда был моим заклятым врагом.""",
    KIND_DIGITS: """Задание 15
Укажите цифру(-ы), на месте которой(-ых) пишется НН.
Ответ: 134
Текст: Дли(1)ая мощё(2)ая дорога вела к стари(3)ому дому.""",
    KIND_MATCH: """Задание 8
Установите соответствие между грамматическими ошибками и предложениями.
А) нарушение в построении предложения с деепричастным оборотом
Б) ошибка в употреблении падежной формы
В) нарушение связи между подлежащим и сказуемым
Г) неверное построение предложения с косвенной речью
Д) ошибка в построении предложения с причастным оборотом
1) Приехав в город, мне сразу понравились улицы.
2) Согласно расписания поезд уходит в семь.
3) Все, кто читал повесть, помнят её финал.
4) Он сказал, что я приду завтра.
5) Книга, лежащая на столе, моя.
Ответ: 12345""",
}

# Шаблон по умолчанию: с него начинали, на него ссылаются старые инструкции.
TEMPLATE = TEMPLATES[KIND_CHOICE]

KIND_NAMES = {
    KIND_CHOICE: "выбор варианта",
    KIND_OPEN: "вписать слово",
    KIND_DIGITS: "вписать цифры",
    KIND_MATCH: "соответствие",
}

# Материал задания бывает на целый экзаменационный текст, а в сообщение Telegram
# влезает 4096 символов — в карточке показываем начало.
PASSAGE_PREVIEW = 600

MENU = (
    "<b>Админ-панель тренажёра ЕГЭ</b>\n\n"
    "/add — добавить одно задание текстом\n"
    "/templates — шаблоны всех видов заданий\n"
    "/upload — залить вариант файлом, скопированным с РЕШУ ЕГЭ\n"
    "/find — найти задание по ID\n"
    "/list — задания по номеру\n"
    "/variant — собрать вариант из существующих ID\n"
    "/variants — список вариантов\n"
    "/stats — что сейчас в базе\n"
    "/cancel — отменить текущее действие"
)


class Add(StatesGroup):
    waiting = State()


class Upload(StatesGroup):
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
    """Карточка задания для админа — по своим правилам для каждого вида.

    Раньше карточка печатала любое задание как выбор варианта. У заданий с
    вписыванием ответа она выходила пустой, а у соответствия — уверенно неверной:
    правый столбец шёл как варианты, а correct у соответствия считается с единицы,
    и галочка вставала не на том. Проверить по такой карточке было нечего.
    """
    kind = getattr(task, "kind", None) or KIND_CHOICE
    correct = list(task.correct or [])
    options = list(task.options or [])
    answers = list(getattr(task, "answers", None) or [])
    match_left = list(getattr(task, "match_left", None) or [])

    lines = [
        f"<b>№{task.number} · {html.escape(title(task.number))}</b>",
        f"ID: <code>{task.id}</code> · {KIND_NAMES.get(kind, kind)}",
        "",
        html.escape(task.text),
        "",
    ]

    if kind == KIND_MATCH:
        for i, item in enumerate(match_left):
            value = correct[i] if i < len(correct) else None
            target = options[value - 1] if value and 1 <= value <= len(options) else None
            arrow = f" → {value}" if value else " → ?"
            lines.append(f"{letter(i)}) {html.escape(str(item))}{arrow}")
            if target:
                lines.append(f"    <i>{html.escape(str(target))}</i>")
        lines.append("")
        for i, option in enumerate(options):
            lines.append(f"{i + 1}) {html.escape(str(option))}")
        lines.append("")
        lines.append("Ответ: " + ", ".join(
            f"{letter(i)}-{value}" for i, value in enumerate(correct)
        ))
        if len(correct) != len(match_left):
            lines.append(
                f"⚠️ слева позиций {len(match_left)}, а в ответе {len(correct)} — "
                "задание заполнено не до конца"
            )
    elif kind in (KIND_OPEN, KIND_DIGITS):
        if answers:
            lines.append("Ответ: " + ", ".join(html.escape(str(a)) for a in answers))
            if len(answers) > 1:
                lines.append("<i>засчитывается любая из форм</i>")
        else:
            lines.append("⚠️ Ответ не заполнен — задание не будет засчитано ученику")
    else:
        # Цифрами, а не буквами: так вариант напечатан в источнике и так его видит
        # ученик. Карточка на то и нужна, чтобы сверить её с исходником построчно.
        for i, option in enumerate(options):
            mark = " ✅" if i in correct else ""
            lines.append(f"{i + 1}) {html.escape(str(option))}{mark}")
        lines.append("")
        if correct:
            lines.append("Ответ: " + ", ".join(str(i + 1) for i in correct))
        else:
            lines.append("⚠️ Правильный ответ не указан")

    passage = getattr(task, "passage", None)
    if passage:
        shown = passage[:PASSAGE_PREVIEW]
        lines += ["", "<b>Текст задания</b>", html.escape(shown)]
        if len(passage) > PASSAGE_PREVIEW:
            lines.append(f"<i>… ещё {len(passage) - PASSAGE_PREVIEW} символов</i>")
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
@router.message(Command("templates"))
async def templates(message: Message) -> None:
    """Шаблоны всех видов. Вид бот берёт из номера задания, а не из формы сообщения."""
    blocks = [
        f"<b>№ вида «{KIND_NAMES[kind]}»</b>\n<pre>{html.escape(TEMPLATES[kind])}</pre>"
        for kind in (KIND_CHOICE, KIND_OPEN, KIND_DIGITS, KIND_MATCH)
    ]
    await message.answer(
        "<b>Вид задания определяется его номером</b>, указывать его не нужно: "
        "№4 — это всегда выбор варианта, №15 — всегда цифры, №8 — всегда соответствие.\n\n"
        "Проще всего скопировать задание с РЕШУ ЕГЭ целиком, вместе с шапкой и "
        "«Пояснением» — тогда у него будет ID источника, и второй раз оно в базу "
        "не попадёт. Ручные шаблоны на случай, когда задание своё:\n\n"
        + "\n".join(blocks)
        + "\n<b>Строка «Текст:»</b> необязательна: всё после неё — материал задания "
        "(отрывок, предложения), он показывается ученику отдельно от условия.\n"
        "<b>Варианты ответа нумеруются цифрами</b> — как в бланке ЕГЭ.\n"
        "Несколько верных: <code>Ответ: 24</code>. Синонимы: <code>Ответ: вследствие, ввиду</code>."
    )


@router.message(Command("add"))
async def add_prompt(message: Message, state: FSMContext) -> None:
    await state.set_state(Add.waiting)
    await message.answer(
        "Пришлите <b>одно задание текстом</b> — скопированное с РЕШУ ЕГЭ как есть, "
        "вместе с шапкой «Задание 8 № 10262 тип 8» и «Пояснением». Разбирается тем же "
        "кодом, что и целый вариант, и ID источника сохранится: повторно то же задание "
        "в базу не попадёт.\n\n"
        "Своё задание — по шаблону, шапки хватит короткой:\n\n"
        f"<pre>{html.escape(TEMPLATE)}</pre>\n"
        "Другие виды — /templates. ID присвоится сам. /cancel — отмена."
    )


def draft_card(payload: dict) -> str:
    """Карточка ещё не залитого задания — в том же виде, в каком оно потом ляжет в базу.

    Показывать именно её важно: correct у выбора вариантов в разборе считается с
    единицы, а в базе с нуля, и сдвиг видно только на готовой карточке.
    """
    loaded = load_payload(payload, where="задание")
    if loaded.errors:
        return "❌ " + "\n".join(loaded.errors)
    parsed = to_parsed(loaded.tasks[0])
    draft = SimpleNamespace(
        id="будет присвоен",
        number=parsed.number,
        kind=parsed.kind,
        text=parsed.text,
        passage=parsed.passage,
        options=parsed.options,
        match_left=parsed.match_left,
        correct=parsed.correct,
        answers=parsed.answers,
    )
    return task_card(draft)


@router.message(Add.waiting, F.text)
async def add_receive(message: Message, state: FSMContext) -> None:
    """Разбирает задание и показывает карточку. В базу — только по кнопке."""
    result = parse_one(message.text)

    if result.problems:
        # Состояние не сбрасываем: обычно достаточно поправить строку и прислать снова.
        lines = ["❌ Не разобрал, в базу ничего не пошло.", ""]
        lines += [html.escape(str(p)) for p in result.problems]
        lines += ["", "Поправьте и пришлите снова. Шаблоны — /templates, отмена — /cancel."]
        await message.answer(clip("\n".join(lines)))
        return

    payload = result.payload()
    await state.update_data(payload=payload)

    task = result.tasks[0]
    lines = [draft_card(payload)]
    if task.get("source_id"):
        lines.append(f"\nID источника: <code>{task['source_id']}</code>")
    else:
        lines.append(
            "\n⚠️ В шапке нет номера источника — дубли такого задания ищутся "
            "по совпадению текста."
        )
    for note in result.notes:
        lines.append(f"\n⚠️ {html.escape(note)}")
    lines.append("\nДобавляем?")

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Добавить", callback_data="add:yes"),
        InlineKeyboardButton(text="Отмена", callback_data="add:no"),
    ]])
    await message.answer(clip("\n".join(lines)), reply_markup=keyboard)


@router.callback_query(F.data == "add:no")
async def add_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Отменено, база не тронута")


@router.callback_query(F.data == "add:yes")
async def add_save(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    payload = data.get("payload")
    if not payload:
        await callback.answer("Разобранное задание потерялось, пришлите заново", show_alert=True)
        return

    # Проверка перед базой и дедуп — те же, что у заливки варианта.
    loaded = load_payload(payload, where="задание")
    if loaded.errors:
        await state.clear()
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            "❌ Проверка перед заливкой не прошла:\n\n"
            + "\n".join(html.escape(e) for e in loaded.errors)
        )
        return

    report = await import_tasks(loaded.tasks)
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer()

    if report.reused:
        number, task_id = report.reused[0]
        async with SessionMaker() as db:
            task = await get_task(db, task_id)
        await callback.message.answer(
            f"ℹ️ Это задание уже есть в базе — <code>{task_id}</code>. Ничего не добавлено.\n\n"
            + (task_card(task) if task else ""),
            reply_markup=task_keyboard(task_id),
        )
        return

    number, task_id = report.created[0]
    async with SessionMaker() as db:
        task = await get_task(db, task_id)
    await callback.message.answer(
        f"✅ Задание добавлено\n\n№{number}\nID: <code>{task_id}</code>\n\n"
        + (task_card(task) if task else ""),
        reply_markup=task_keyboard(task_id),
    )


# --------------------------------------------------------------------------- #
# Заливка сырого варианта файлом
# --------------------------------------------------------------------------- #
# Только документом: сырой вариант весит около сотни килобайт, а в сообщение
# Telegram влезает 4096 символов. Разбор — core/raw_variant.py, тот же, что и у
# скрипта parse_raw.py, поэтому поведение здесь и в терминале совпадает.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
TELEGRAM_LIMIT = 4000


def clip(text: str, limit: int = TELEGRAM_LIMIT) -> str:
    """Обрезает сообщение до лимита Telegram, честно сообщая об обрезке."""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n… сообщение обрезано"


@router.message(Command("upload"))
async def upload_prompt(message: Message, state: FSMContext) -> None:
    await state.set_state(Upload.waiting)
    await message.answer(
        "Пришлите <b>файлом</b> вариант, скопированный с РЕШУ ЕГЭ как есть — "
        "вместе с «Пояснениями», ничего не вычищая.\n\n"
        "Текстом не получится: вариант весит больше, чем помещается в сообщение. "
        "Сохраните его в <code>.txt</code> в кодировке UTF-8 и отправьте документом.\n\n"
        "/cancel — отмена."
    )


@router.message(Upload.waiting, F.document)
async def upload_receive(message: Message, state: FSMContext) -> None:
    document = message.document
    if document.file_size and document.file_size > MAX_UPLOAD_BYTES:
        await message.answer(
            f"❌ Файл {document.file_size // 1024} КБ — это слишком много. "
            f"Ожидается вариант примерно на 100-200 КБ."
        )
        return

    await message.answer("Читаю файл…")
    buffer = await message.bot.download(document)
    try:
        raw = buffer.read().decode("utf-8")
    except UnicodeDecodeError:
        await state.clear()
        await message.answer(
            "❌ Файл не в кодировке UTF-8, прочитать не могу.\n\n"
            "В «Блокноте» при сохранении выберите кодировку <b>UTF-8</b> и пришлите снова."
        )
        return

    result = parse_raw_variant(raw)

    if result.problems:
        await state.clear()
        lines = [f"❌ Разбор остановлен, проблем: {len(result.problems)}", ""]
        lines += [html.escape(str(p)) for p in result.problems]
        lines += ["", "Ничего не залито. Поправьте исходник и пришлите заново."]
        await message.answer(clip("\n".join(lines)))
        return

    # Держим разобранный вариант до нажатия кнопки: заливка идёт только по
    # явному подтверждению, чтобы случайный файл не попал в базу.
    payload = result.payload()
    await state.update_data(payload=payload)

    # Разбор отправляем файлом: 26 заданий с текстами — это десятки тысяч
    # символов, в сообщение они не влезут даже близко.
    preview = render_preview(payload, result.notes)
    await message.answer_document(
        BufferedInputFile(
            preview.encode("utf-8"),
            filename=f"разбор-{document.file_name or 'варианта'}.txt",
        ),
        caption="Разбор целиком — проверьте перед заливкой.",
    )

    lines = [
        f"✅ Разобрано заданий: {len(result.tasks)} из {LAST_TASK}",
    ]
    if result.texts:
        sizes = ", ".join(f"{k} — {len(v)} симв" for k, v in result.texts.items())
        lines.append(f"Тексты: {sizes}")
    if result.notes:
        lines += ["", "Обратите внимание:"]
        lines += [f"• {html.escape(note)}" for note in result.notes]
    lines += ["", "Сверьтесь с файлом выше. Заливаем?"]

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Залить и собрать вариант", callback_data="upl:variant")],
        [InlineKeyboardButton(text="Только задания", callback_data="upl:tasks")],
        [InlineKeyboardButton(text="Отмена", callback_data="upl:no")],
    ])
    await message.answer(clip("\n".join(lines)), reply_markup=keyboard)


@router.message(Upload.waiting)
async def upload_not_a_file(message: Message) -> None:
    await message.answer(
        "Жду именно файл. Сохраните вариант в <code>.txt</code> и отправьте документом.\n"
        "/cancel — отмена."
    )


@router.callback_query(F.data == "upl:no")
async def upload_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Отменено, база не тронута")


@router.callback_query(F.data.startswith("upl:"))
async def upload_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    payload = data.get("payload")
    if not payload:
        await callback.answer("Разобранный вариант потерялся, пришлите файл заново", show_alert=True)
        return

    with_variant = callback.data == "upl:variant"
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer()
    await callback.message.answer("Заливаю…")

    # Вторая проверка перед базой: у бота и у скрипта она одна и та же.
    loaded = load_payload(payload, where="вариант")
    if loaded.errors:
        await state.clear()
        lines = [f"❌ Проверка перед заливкой не прошла, ошибок: {len(loaded.errors)}", ""]
        lines += [html.escape(e) for e in loaded.errors]
        lines += ["", "Ничего не залито."]
        await callback.message.answer(clip("\n".join(lines)))
        return

    number = await next_free_variant_number() if with_variant else None
    report = await import_tasks(loaded.tasks, number)
    await state.clear()

    # Вариант опознаётся по №26: совпал — значит этот же вариант уже заливали.
    if report.is_duplicate:
        await callback.message.answer(
            f"⛔️ Такой вариант уже есть в базе — <b>№{report.duplicate_of}</b>.\n"
            f"Его №{LAST_TASK} совпадает по ID источника.\n\n"
            "Ничего не залито."
        )
        return

    lines = [f"✅ Добавлено заданий: {len(report.created)}"]
    if report.duplicates:
        lines.append(f"Пропущено как уже залитые: {report.duplicates}")
    if report.variant_status:
        lines.append(f"Вариант №{report.variant_number} {report.variant_status}.")
    for warning in report.warnings:
        lines.append(f"⚠️ {html.escape(warning)}")
    lines.append(f"Всего заданий в базе: {report.total_in_db}")
    await callback.message.answer(clip("\n".join(lines)))


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
    """Отдаёт задание готовым шаблоном: скопировать, поправить строку, прислать назад.

    Так правится любое поле, включая ответ у заданий с вписыванием, — набирать
    шаблон с нуля и гадать, какой он для этого вида, не нужно.
    """
    task_id = callback.data.split(":", 1)[1]
    async with SessionMaker() as db:
        task = await get_task(db, task_id)
    if task is None:
        await callback.answer("Задание уже удалено", show_alert=True)
        return

    await state.set_state(Edit.waiting)
    await state.update_data(task_id=task_id)
    await callback.message.answer(
        f"Задание <code>{task_id}</code> целиком. Скопируйте, поправьте нужное "
        f"и пришлите обратно:\n\n<pre>{html.escape(to_template(task))}</pre>\n"
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
