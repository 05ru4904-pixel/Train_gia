"""Модели данных.

Схема рассчитана на то, что статистика считается запросом по завершённым сессиям,
а не отдельными счётчиками: так фильтр по датам (ТЗ п.12) работает бесплатно и
цифры невозможно рассинхронизировать с фактическими ответами.
"""
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from core.tasks_meta import (  # виды заданий живут в чистом модуле без SQLAlchemy
    KIND_CHOICE,
    KIND_DIGITS,
    KIND_MATCH,
    KIND_OPEN,
    RENDERABLE_KINDS,
    TASK_KINDS,
)

# В Postgres — JSONB (компактнее и быстрее), в остальных СУБД — обычный JSON.
# Нужно ради тестов: полный сценарий прогоняется на SQLite, а прод работает на
# Postgres ровно так же, как если бы здесь стоял голый JSONB.
JSONColumn = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


# --- статусы сессии ------------------------------------------------------------
STATUS_ACTIVE = "active"
STATUS_FINISHED = "finished"
STATUS_ABANDONED = "abandoned"

# --- виды сессии ---------------------------------------------------------------
KIND_TRAINING = "training"
KIND_VARIANT = "variant"

PLAN_FREE = "free"
PLAN_PRO = "pro"


class User(Base):
    """Отдельной регистрации нет: пользователь заводится при первом входе (ТЗ п.14)."""

    __tablename__ = "users"

    # Telegram ID и есть первичный ключ — второй идентификатор не нужен.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    first_name: Mapped[str | None] = mapped_column(String(128))
    last_name: Mapped[str | None] = mapped_column(String(128))
    username: Mapped[str | None] = mapped_column(String(64))
    plan: Mapped[str] = mapped_column(String(16), default=PLAN_FREE, server_default=PLAN_FREE)
    plan_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Анкета, которую ученик заполняет при первом входе (core/profile_meta.py).
    # Пока не заполнена, все четыре поля пустые — по onboarded_at и определяется,
    # показывать анкету или нет.
    grade: Mapped[int | None] = mapped_column(Integer)
    math_level: Mapped[str | None] = mapped_column(String(16))
    # Только предметы по выбору. Русский и математика не хранятся: русский сдают
    # все, а математика лежит в math_level — дублировать их значит однажды
    # разойтись с ними.
    subjects: Mapped[list | None] = mapped_column(JSONColumn)
    target_score: Mapped[str | None] = mapped_column(String(16))
    onboarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def display_name(self) -> str:
        parts = [p for p in (self.first_name, self.last_name) if p]
        return " ".join(parts) or (f"@{self.username}" if self.username else f"id{self.id}")


class Task(Base):
    """Задание базы. ID генерируется автоматически и виден админу (ТЗ п.15)."""

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    # Вид задания определяет и способ ответа, и способ проверки — см. core/scoring.py.
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default=KIND_CHOICE, server_default=KIND_CHOICE
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # Исходный текст, к которому относится задание. В варианте один текст обслуживает
    # несколько заданий, но хранится он у каждого свой: так задание остаётся
    # самодостаточным и его можно переносить между вариантами.
    passage: Mapped[str | None] = mapped_column(Text)
    # Варианты ответа. Для choice — список для выбора, для match — правый столбец
    # (позиции 1-9). Для open и digits пустой.
    options: Mapped[list] = mapped_column(JSONColumn, nullable=False)
    # Левый столбец задания на соответствие (позиции А-Д).
    match_left: Mapped[list | None] = mapped_column(JSONColumn)
    # Для choice — индексы верных вариантов. Для match — номер правой позиции для
    # каждой левой по порядку. Для open и digits пустой.
    correct: Mapped[list] = mapped_column(JSONColumn, nullable=False)
    # Допустимые текстовые ответы для open и digits: источник перечисляет все формы
    # («заклятым», «заклятый», «злейшим»), любая из них засчитывается.
    answers: Mapped[list | None] = mapped_column(JSONColumn)
    # Номер задания в базе сайта-источника. Хранится рядом со своим id, а не вместо
    # него: наш id — адрес задания во всём приложении, source_id нужен только при
    # заливке, чтобы узнать уже залитое задание, как бы ни правили его текст.
    source_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), onupdate=func.now())

    __table_args__ = (
        Index("ix_tasks_number", "number"),
        Index("ix_tasks_number_kind", "number", "kind"),
        Index("ix_tasks_source", "number", "source_id"),
    )


class Variant(Base):
    """Полный вариант ЕГЭ — набор ссылок на задания (ТЗ п.18, 19)."""

    __tablename__ = "variants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    number: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    items: Mapped[list["VariantItem"]] = relationship(
        back_populates="variant", cascade="all, delete-orphan", lazy="selectin"
    )


class VariantItem(Base):
    """Одно задание внутри варианта. Одно и то же задание может входить в разные
    варианты — поэтому связь, а не копия (ТЗ п.19)."""

    __tablename__ = "variant_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    variant_id: Mapped[int] = mapped_column(
        ForeignKey("variants.id", ondelete="CASCADE"), nullable=False
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)  # позиция 1..26
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="RESTRICT"), nullable=False)

    variant: Mapped[Variant] = relationship(back_populates="items")

    __table_args__ = (
        UniqueConstraint("variant_id", "number", name="uq_variant_items_slot"),
        Index("ix_variant_items_variant", "variant_id"),
    )


class Session(Base):
    """Прохождение: обычная тренировка или полный вариант.

    Одновременно у пользователя может быть только одна активная сессия — при старте
    новой предыдущая помечается abandoned и в статистику не попадает (ТЗ п.7).
    """

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=STATUS_ACTIVE)

    # для обычной тренировки
    task_number: Mapped[int | None] = mapped_column(Integer)
    # для полного варианта
    variant_id: Mapped[int | None] = mapped_column(ForeignKey("variants.id", ondelete="SET NULL"))

    total: Mapped[int] = mapped_column(Integer, nullable=False)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Таймер полного варианта. Время идёт по часам, а не по времени в приложении:
    # закрытие Mini App его не останавливает (ТЗ п.9). Пока таймер идёт, resumed_at
    # хранит момент запуска; на паузе он пуст, а накопленное лежит в time_spent_sec.
    time_limit_sec: Mapped[int | None] = mapped_column(Integer)
    time_spent_sec: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    resumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Снимок результата на момент завершения: правила перевода баллов могут
    # поменяться, а уже показанный ученику результат меняться не должен.
    correct_count: Mapped[int | None] = mapped_column(Integer)
    wrong_count: Mapped[int | None] = mapped_column(Integer)
    skipped_count: Mapped[int | None] = mapped_column(Integer)
    raw_score: Mapped[int | None] = mapped_column(Integer)
    # Тестового балла (100-балльной шкалы) в результате нет — показываем только
    # первичный. Колонка test_score осталась в боевой базе от прошлых версий: она
    # nullable, вставкам не мешает, и удалять её незачем.

    items: Mapped[list["SessionItem"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="SessionItem.position",
    )

    __table_args__ = (
        Index("ix_sessions_user_status", "user_id", "status"),
        Index("ix_sessions_user_finished", "user_id", "status", "finished_at"),
    )


class SessionItem(Base):
    """Один вопрос внутри сессии вместе с ответом пользователя."""

    __tablename__ = "session_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="RESTRICT"), nullable=False)
    # Дублируем номер задания, чтобы статистика по заданиям считалась без join к tasks.
    task_number: Mapped[int] = mapped_column(Integer, nullable=False)

    # Ответ выбором: индексы вариантов (choice) или последовательность
    # правых позиций по порядку левых (match).
    selected: Mapped[list | None] = mapped_column(JSONColumn)
    # Ответ вводом: слово или цифры, как их набрал ученик (open, digits).
    # Храним ровно то, что он написал, — пригодится при разборе ошибок.
    typed: Mapped[str | None] = mapped_column(Text)
    is_correct: Mapped[bool | None] = mapped_column(Boolean)
    points: Mapped[int | None] = mapped_column(Integer)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    session: Mapped[Session] = relationship(back_populates="items")
    task: Mapped[Task] = relationship(lazy="selectin")

    __table_args__ = (
        UniqueConstraint("session_id", "position", name="uq_session_items_slot"),
        Index("ix_session_items_session", "session_id"),
        Index("ix_session_items_stats", "session_id", "task_number"),
    )

    @property
    def answered(self) -> bool:
        return self.is_correct is not None


# --- карточки ------------------------------------------------------------------
# Статусы карточки у конкретного ученика. Строки прежние — база уже с ними живёт,
# а смысл теперь другой, и он важнее названий:
#   known   — слово выучено, больше не показывается; лежит в списке «Выученные»;
#   unknown — слово в работе, ждёт своей очереди на повтор.
# Одного «Знаю» мало: слово, однажды отложенное, закрывается только после двух
# повторов — правило целиком лежит в `crud.mark_card`.
CARD_KNOWN = "known"
CARD_UNKNOWN = "unknown"

# Сколько раз слово должно показаться после того, как ученик отложил его в повтор.
# Долг отрабатывается до конца, даже если внутри него ученик нажал «Знаю»:
# вспомнил один раз — ещё не выучил.
CARD_REPEAT_DEBT = 2


class CardProgress(Base):
    """Что ученик уже знает в колоде карточек (ударения и будущие колоды).

    Одна строка на пару «ученик — слово». Прогресс держится на сервере, а не в
    браузере: Telegram чистит хранилище WebView, а с телефона и с ноутбука должна
    быть одна и та же колода.
    """

    __tablename__ = "card_progress"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Идентификатор колоды из core/cards.py — «accents» и далее.
    deck: Mapped[str] = mapped_column(String(32), nullable=False)
    # Ключ карточки: слово в нижнем регистре. Правка подсказки в файле прогресс
    # не сбрасывает, правка самого слова — заводит новую карточку, и это честно.
    card: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=CARD_UNKNOWN)
    # Сколько раз карточка показывалась — по нему видно, что даётся тяжело.
    seen_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("user_id", "deck", "card", name="uq_card_progress"),
        Index("ix_card_progress_user_deck", "user_id", "deck"),
    )
