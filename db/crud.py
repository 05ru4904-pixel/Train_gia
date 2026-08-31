"""Операции с базой. Вся работа с сессиями SQLAlchemy собрана здесь."""
from datetime import datetime, timezone

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import noload
from sqlalchemy.exc import IntegrityError

from core import scoring
from core.parser import ParsedTask, generate_task_id
from core.tasks_meta import LAST_TASK, RENDERABLE_KINDS, TASK_NUMBERS
from db.models import (
    KIND_CHOICE,
    KIND_DIGITS,
    KIND_MATCH,
    KIND_OPEN,
    KIND_TRAINING,
    KIND_VARIANT,
    PLAN_FREE,
    STATUS_ABANDONED,
    STATUS_ACTIVE,
    STATUS_FINISHED,
    Session,
    SessionItem,
    Task,
    User,
    Variant,
    VariantItem,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Пользователи
# --------------------------------------------------------------------------- #
async def get_or_create_user(db, user_id: int, first_name=None, last_name=None, username=None) -> User:
    """Пользователь заводится при первом входе в Mini App (ТЗ п.14).

    Имя и username обновляем на каждом входе: в Telegram их меняют, а в профиле
    должно быть актуальное.
    """
    user = await db.get(User, user_id)
    if user is None:
        user = User(
            id=user_id,
            first_name=first_name,
            last_name=last_name,
            username=username,
            plan=PLAN_FREE,
            last_seen_at=utcnow(),
        )
        db.add(user)
        try:
            await db.commit()
        except IntegrityError:
            # параллельный первый вход с двух устройств
            await db.rollback()
            user = await db.get(User, user_id)
        else:
            return user

    changed = False
    for field, value in (("first_name", first_name), ("last_name", last_name), ("username", username)):
        if value is not None and getattr(user, field) != value:
            setattr(user, field, value)
            changed = True
    user.last_seen_at = utcnow()
    await db.commit()
    if changed:
        await db.refresh(user)
    return user


# --------------------------------------------------------------------------- #
# Задания
# --------------------------------------------------------------------------- #
async def create_task(db, parsed: ParsedTask) -> Task:
    """Сохраняет задание, подбирая свободный ID (ТЗ п.15).

    Пишутся все поля разбора, включая вид: задание с вписыванием ответа, попавшее
    в базу как choice, ученик увидел бы как вопрос без единого варианта.
    """
    for _ in range(10):
        task = Task(
            id=generate_task_id(),
            number=parsed.number,
            kind=parsed.kind,
            text=parsed.text,
            passage=parsed.passage,
            options=list(parsed.options),
            match_left=list(parsed.match_left) or None,
            correct=list(parsed.correct),
            answers=list(parsed.answers) or None,
            source_id=parsed.source_id,
        )
        db.add(task)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            continue
        return task
    raise RuntimeError("Не удалось подобрать свободный ID задания")


async def get_task(db, task_id: str) -> Task | None:
    return await db.get(Task, task_id)


async def update_task(db, task: Task, parsed: ParsedTask) -> Task:
    """Перезаписывает задание целиком.

    Именно целиком: если обновлять только текст и варианты, у задания с вписыванием
    ответа останется старый answers, и после правки условия оно будет ждать ответ от
    прошлой версии. Поэтому админ-бот и отдаёт на правку задание целиком (to_template).
    """
    task.number = parsed.number
    task.kind = parsed.kind
    task.text = parsed.text
    task.passage = parsed.passage
    task.options = list(parsed.options)
    task.match_left = list(parsed.match_left) or None
    task.correct = list(parsed.correct)
    task.answers = list(parsed.answers) or None
    # Шаблон правки ID источника не содержит: раз админ его не менял, оставляем свой.
    if parsed.source_id is not None:
        task.source_id = parsed.source_id
    await db.commit()
    await db.refresh(task)
    return task


async def delete_task(db, task: Task) -> None:
    await db.delete(task)
    await db.commit()


async def task_counts(db, kinds: tuple[str, ...] | None = None) -> dict[int, int]:
    """Сколько заданий в базе по каждому номеру.

    По умолчанию считаются все виды — так админу видно реальное наполнение базы.
    Mini App передаёт RENDERABLE_KINDS, иначе экран выбора обещал бы задания,
    которые приложение пока не умеет показать.
    """
    query = select(Task.number, func.count())
    if kinds:
        query = query.where(Task.kind.in_(kinds))
    rows = await db.execute(query.group_by(Task.number))
    return {number: count for number, count in rows.all()}


async def list_tasks(db, number: int, limit: int = 20, offset: int = 0) -> list[Task]:
    rows = await db.execute(
        select(Task).where(Task.number == number).order_by(Task.created_at.desc())
        .limit(limit).offset(offset)
    )
    return list(rows.scalars())


async def find_tasks_by_source(db, pairs: list[tuple[int, int]]) -> dict[tuple[int, int], str]:
    """(номер задания, ID источника) -> ID уже залитого задания.

    По этой паре при заливке варианта решается, создавать задание или переиспользовать
    существующее. Номер в ключе обязателен: нумерация у источника своя на каждый номер
    задания, и один и тот же ID встречается у разных номеров.
    """
    if not pairs:
        return {}
    rows = await db.execute(
        select(Task.number, Task.source_id, Task.id)
        .where(
            Task.number.in_({n for n, _ in pairs}),
            Task.source_id.in_({s for _, s in pairs}),
        )
        .order_by(Task.created_at)
    )
    wanted = set(pairs)
    found: dict[tuple[int, int], str] = {}
    for number, source_id, task_id in rows.all():
        key = (number, source_id)
        # Первым идёт самое старое: если в базе уже лежат две копии (залиты до
        # появления source_id), переиспользуем ту, что появилась раньше.
        if key in wanted and key not in found:
            found[key] = task_id
    return found


async def find_variant_by_slot_source(db, slot: int, source_id: int) -> Variant | None:
    """Вариант, у которого на позиции slot стоит задание с этим ID источника.

    Так вариант проверяется на дубль целиком: совпал последний номер — значит этот
    вариант уже заливали. Совпадение остальных заданий ничего не значит, одно и то же
    задание источник ставит в разные варианты.
    """
    rows = await db.execute(
        select(Variant)
        .join(VariantItem, VariantItem.variant_id == Variant.id)
        .join(Task, Task.id == VariantItem.task_id)
        .where(VariantItem.number == slot, Task.source_id == source_id)
        .order_by(Variant.number)
        .limit(1)
    )
    return rows.scalar_one_or_none()


async def pick_random_tasks(
    db, number: int, count: int, kinds: tuple[str, ...] = RENDERABLE_KINDS
) -> list[Task]:
    """Случайные неповторяющиеся задания одного номера (ТЗ п.4).

    Отбираются только виды, которые приложение умеет отрисовать: задание с вводом
    ответа сейчас выглядело бы как вопрос без единого варианта.
    """
    query = select(Task).where(Task.number == number)
    if kinds:
        query = query.where(Task.kind.in_(kinds))
    rows = await db.execute(query.order_by(func.random()).limit(count))
    return list(rows.scalars())


# --------------------------------------------------------------------------- #
# Варианты
# --------------------------------------------------------------------------- #
async def get_variant_by_number(db, number: int) -> Variant | None:
    rows = await db.execute(select(Variant).where(Variant.number == number))
    return rows.scalar_one_or_none()


async def create_variant(db, number: int, task_ids: dict[int, str]) -> Variant:
    """Собирает вариант из существующих заданий (ТЗ п.19).

    Существование каждого ID проверяет вызывающая сторона — так админ-бот может
    показать сразу все ненайденные ID, а не падать на первом.
    """
    variant = Variant(number=number)
    db.add(variant)
    await db.flush()
    for slot, task_id in sorted(task_ids.items()):
        db.add(VariantItem(variant_id=variant.id, number=slot, task_id=task_id))
    await db.commit()
    await db.refresh(variant)
    return variant


async def replace_variant_items(db, variant: Variant, task_ids: dict[int, str]) -> Variant:
    await db.execute(delete(VariantItem).where(VariantItem.variant_id == variant.id))
    for slot, task_id in sorted(task_ids.items()):
        db.add(VariantItem(variant_id=variant.id, number=slot, task_id=task_id))
    await db.commit()
    await db.refresh(variant)
    return variant


async def missing_task_ids(db, task_ids: list[str]) -> list[str]:
    if not task_ids:
        return []
    rows = await db.execute(select(Task.id).where(Task.id.in_(task_ids)))
    found = set(rows.scalars())
    return [task_id for task_id in task_ids if task_id not in found]


async def list_variants(db, limit: int = 50) -> list[Variant]:
    rows = await db.execute(select(Variant).order_by(Variant.number).limit(limit))
    return list(rows.scalars())


async def pick_random_variant(db) -> Variant | None:
    rows = await db.execute(select(Variant).order_by(func.random()).limit(1))
    return rows.scalar_one_or_none()


async def delete_variant(db, variant: Variant) -> None:
    await db.delete(variant)
    await db.commit()


# --------------------------------------------------------------------------- #
# Сессии: общее
# --------------------------------------------------------------------------- #
async def reload_session(db, session_id: int) -> Session:
    """Перечитывает сессию запросом.

    Объект, собранный в памяти, не знает про свои SessionItem: обращение к ним
    позже вызвало бы ленивый запрос вне async-контекста (MissingGreenlet).
    Запрос через select() подтягивает их сразу — так настроено в моделях.
    """
    rows = await db.execute(select(Session).where(Session.id == session_id))
    return rows.scalar_one()


async def get_active_session(db, user_id: int) -> Session | None:
    rows = await db.execute(
        select(Session)
        .where(Session.user_id == user_id, Session.status == STATUS_ACTIVE)
        .order_by(Session.started_at.desc())
        .limit(1)
    )
    return rows.scalar_one_or_none()


async def abandon_active_sessions(db, user_id: int) -> None:
    """Незавершённая сессия при старте новой сбрасывается, и её ответы в статистику
    не попадают (ТЗ п.7, п.12) — статистика читает только status='finished'."""
    await db.execute(
        update(Session)
        .where(Session.user_id == user_id, Session.status == STATUS_ACTIVE)
        .values(status=STATUS_ABANDONED, finished_at=utcnow())
    )
    await db.commit()


async def get_session(db, session_id: int, user_id: int) -> Session | None:
    rows = await db.execute(
        select(Session).where(Session.id == session_id, Session.user_id == user_id)
    )
    return rows.scalar_one_or_none()


# --------------------------------------------------------------------------- #
# Сессии: обычная тренировка
# --------------------------------------------------------------------------- #
class NotEnoughTasks(Exception):
    """Заданий в базе меньше, чем запросил пользователь (ТЗ п.4)."""

    def __init__(self, available: int, requested: int):
        self.available = available
        self.requested = requested
        super().__init__(f"доступно {available} из {requested}")


async def start_training(db, user_id: int, number: int, count: int) -> Session:
    tasks = await pick_random_tasks(db, number, count)
    if len(tasks) < count:
        raise NotEnoughTasks(available=len(tasks), requested=count)

    await abandon_active_sessions(db, user_id)
    session = Session(
        user_id=user_id,
        kind=KIND_TRAINING,
        status=STATUS_ACTIVE,
        task_number=number,
        total=count,
        started_at=utcnow(),
    )
    db.add(session)
    await db.flush()
    for position, task in enumerate(tasks):
        db.add(
            SessionItem(
                session_id=session.id,
                position=position,
                task_id=task.id,
                task_number=task.number,
            )
        )
    await db.commit()
    return await reload_session(db, session.id)


# --------------------------------------------------------------------------- #
# Сессии: полный вариант
# --------------------------------------------------------------------------- #
class NoVariantAvailable(Exception):
    """В базе не хватает заданий, чтобы собрать вариант №1-26."""

    def __init__(self, missing: list[int]):
        self.missing = missing
        super().__init__(f"нет заданий для номеров: {missing}")


async def start_variant(db, user_id: int, variant_id: int | None = None) -> Session:
    """Запускает полный вариант.

    Если админ собрал варианты (ТЗ п.18-19) — берём готовый. Если готовых нет,
    собираем разовый набор из случайных заданий по одному на каждый номер: иначе
    кнопка «Полный вариант» упиралась бы в ошибку даже при полной базе заданий.
    """
    variant: Variant | None = None
    if variant_id is not None:
        variant = await db.get(Variant, variant_id)
    if variant is None:
        variant = await pick_random_variant(db)

    picked: list[tuple[int, Task]] = []
    if variant is not None:
        rows = await db.execute(
            select(VariantItem.number, Task)
            .join(Task, Task.id == VariantItem.task_id)
            .where(VariantItem.variant_id == variant.id)
            .order_by(VariantItem.number)
        )
        picked = [(slot, task) for slot, task in rows.all()]
    else:
        missing: list[int] = []
        for number in TASK_NUMBERS:
            tasks = await pick_random_tasks(db, number, 1)
            if tasks:
                picked.append((number, tasks[0]))
            else:
                missing.append(number)
        if missing:
            raise NoVariantAvailable(missing)

    if not picked:
        raise NoVariantAvailable(list(TASK_NUMBERS))

    await abandon_active_sessions(db, user_id)
    now = utcnow()
    session = Session(
        user_id=user_id,
        kind=KIND_VARIANT,
        status=STATUS_ACTIVE,
        variant_id=variant.id if variant else None,
        total=len(picked),
        started_at=now,
        time_limit_sec=scoring.VARIANT_TIME_LIMIT_SEC,
        time_spent_sec=0,
        resumed_at=now,  # таймер стартует сразу
    )
    db.add(session)
    await db.flush()
    for position, (slot, task) in enumerate(picked):
        db.add(
            SessionItem(
                session_id=session.id,
                position=position,
                task_id=task.id,
                task_number=slot,
            )
        )
    await db.commit()
    return await reload_session(db, session.id)


def elapsed_seconds(session: Session, now: datetime | None = None) -> int:
    """Сколько времени уже потрачено. Пока resumed_at заполнен, время идёт по часам —
    закрытие Mini App таймер не останавливает (ТЗ п.9)."""
    spent = session.time_spent_sec or 0
    if session.resumed_at is not None:
        started = session.resumed_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        spent += max(0, int(((now or utcnow()) - started).total_seconds()))
    return spent


def remaining_seconds(session: Session, now: datetime | None = None) -> int | None:
    if not session.time_limit_sec:
        return None
    return max(0, session.time_limit_sec - elapsed_seconds(session, now))


async def pause_timer(db, session: Session) -> Session:
    if session.resumed_at is not None:
        session.time_spent_sec = elapsed_seconds(session)
        session.resumed_at = None
        await db.commit()
    return session


async def resume_timer(db, session: Session) -> Session:
    if session.resumed_at is None:
        session.resumed_at = utcnow()
        await db.commit()
    return session


# --------------------------------------------------------------------------- #
# Ответы
# --------------------------------------------------------------------------- #
async def get_item(db, session_id: int, position: int) -> SessionItem | None:
    rows = await db.execute(
        select(SessionItem).where(
            SessionItem.session_id == session_id, SessionItem.position == position
        )
    )
    return rows.scalar_one_or_none()


async def answer_item(
    db, item: SessionItem, selected: list[int] | None = None, typed: str | None = None
) -> SessionItem:
    """Записывает ответ и сразу его проверяет.

    Способ ответа зависит от вида задания: где-то выбирают варианты, где-то
    вписывают слово или цифры, где-то расставляют соответствия. Сохраняем ровно
    то, что сделал ученик, — это нужно для разбора ошибок.
    """
    task = item.task
    kind = task.kind or KIND_CHOICE
    correct = list(task.correct or [])
    answers = list(task.answers or [])

    if kind in (KIND_OPEN, KIND_DIGITS):
        item.typed = (typed or "").strip()
        item.selected = None
        response = item.typed
    elif kind == KIND_MATCH:
        # Порядок важен: значение под i отвечает i-й позиции левого столбца.
        item.selected = list(selected or [])
        item.typed = None
        response = item.selected
    else:
        item.selected = sorted(set(selected or []))
        item.typed = None
        response = item.selected

    # Проверка и балл считаются вместе: у №8 и №22 балл даётся и за частично
    # верный ответ, и по одному только is_correct его не восстановить.
    item.is_correct, item.points = scoring.score_answer(
        item.task_number, kind, response, correct, answers
    )
    item.answered_at = utcnow()
    await db.commit()
    return item


async def finish_session(db, session: Session) -> Session:
    """Считает и замораживает результат. Повторный вызов ничего не меняет."""
    if session.status == STATUS_FINISHED:
        return session

    rows = await db.execute(
        select(SessionItem).where(SessionItem.session_id == session.id)
    )
    items = list(rows.scalars())

    correct = sum(1 for i in items if i.is_correct is True)
    wrong = sum(1 for i in items if i.is_correct is False)
    skipped = sum(1 for i in items if i.is_correct is None)

    session.correct_count = correct
    session.wrong_count = wrong
    session.skipped_count = skipped
    session.status = STATUS_FINISHED
    session.finished_at = utcnow()

    if session.kind == KIND_VARIANT:
        session.time_spent_sec = elapsed_seconds(session)
        if session.time_limit_sec:
            session.time_spent_sec = min(session.time_spent_sec, session.time_limit_sec)
        session.resumed_at = None
        session.raw_score = sum(i.points or 0 for i in items)
        session.test_score = scoring.test_score(session.raw_score)

    await db.commit()
    return await reload_session(db, session.id)


async def finish_if_time_is_up(db, session: Session) -> Session:
    """Лимит времени завершает вариант, даже если пользователь не в приложении (ТЗ п.9)."""
    if session.status == STATUS_ACTIVE and session.kind == KIND_VARIANT:
        left = remaining_seconds(session)
        if left is not None and left <= 0:
            return await finish_session(db, session)
    return session


# --------------------------------------------------------------------------- #
# Статистика
# --------------------------------------------------------------------------- #
def _finished_filter(user_id: int, date_from: datetime | None, date_to: datetime | None):
    conditions = [Session.user_id == user_id, Session.status == STATUS_FINISHED]
    if date_from is not None:
        conditions.append(Session.finished_at >= date_from)
    if date_to is not None:
        conditions.append(Session.finished_at <= date_to)
    return conditions


async def overall_stats(db, user_id: int, date_from=None, date_to=None) -> dict:
    """Общая статистика по завершённым сессиям (ТЗ п.12).

    Считаются только заданные ответы: пропущенные в варианте задания не идут ни в
    «решено», ни в «неправильно», иначе точность по номеру задания занижалась бы
    из-за того, что до него просто не дошли.
    """
    rows = await db.execute(
        select(
            func.count(SessionItem.id),
            func.count(SessionItem.id).filter(SessionItem.is_correct.is_(True)),
        )
        .select_from(SessionItem)
        .join(Session, Session.id == SessionItem.session_id)
        .where(*_finished_filter(user_id, date_from, date_to), SessionItem.is_correct.isnot(None))
    )
    total, correct = rows.one()
    total, correct = int(total or 0), int(correct or 0)
    wrong = total - correct
    return {
        "total": total,
        "correct": correct,
        "wrong": wrong,
        "accuracy": scoring.accuracy(correct, total),
        "wrong_accuracy": 100 - scoring.accuracy(correct, total) if total else 0,
    }


async def stats_by_task(db, user_id: int, date_from=None, date_to=None) -> dict[int, dict]:
    rows = await db.execute(
        select(
            SessionItem.task_number,
            func.count(SessionItem.id),
            func.count(SessionItem.id).filter(SessionItem.is_correct.is_(True)),
        )
        .select_from(SessionItem)
        .join(Session, Session.id == SessionItem.session_id)
        .where(*_finished_filter(user_id, date_from, date_to), SessionItem.is_correct.isnot(None))
        .group_by(SessionItem.task_number)
    )
    result: dict[int, dict] = {}
    for number, total, correct in rows.all():
        total, correct = int(total or 0), int(correct or 0)
        result[int(number)] = {
            "total": total,
            "correct": correct,
            "wrong": total - correct,
            "accuracy": scoring.accuracy(correct, total),
        }
    return result


async def variant_history(db, user_id: int, date_from=None, date_to=None, limit: int = 50) -> list[Session]:
    """История пройденных полных вариантов (ТЗ п.11)."""
    rows = await db.execute(
        select(Session)
        .options(noload(Session.items))
        .where(*_finished_filter(user_id, date_from, date_to), Session.kind == KIND_VARIANT)
        .order_by(Session.finished_at.desc())
        .limit(limit)
    )
    return list(rows.scalars())


async def total_tasks_count(db) -> int:
    rows = await db.execute(select(func.count(Task.id)))
    return int(rows.scalar_one() or 0)


async def variants_count(db) -> int:
    rows = await db.execute(select(func.count(Variant.id)))
    return int(rows.scalar_one() or 0)


async def users_count(db) -> int:
    rows = await db.execute(select(func.count(User.id)))
    return int(rows.scalar_one() or 0)


MAX_TASK_NUMBER = LAST_TASK
