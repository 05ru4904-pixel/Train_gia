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
# Путь слова: новое (строки в таблице ещё нет) -> ждёт 8 часов -> ждёт 24 часа ->
# выучено. Двигает слово только ответ «Знаю», и только в тренажёре «Повторить».
CARD_WAIT_8 = "wait8"
CARD_WAIT_24 = "wait24"
CARD_LEARNED = "learned"

# Пауза перед показом. Первая короткая — слово ещё держится в памяти и его надо
# успеть поймать; вторая длинная — вспомнить через сутки уже что-то значит.
CARD_HOURS_FIRST = 8
CARD_HOURS_SECOND = 24

# На какой этап переводит ответ «Знаю» и сколько после него ждать.
CARD_NEXT_STAGE = {
    None: (CARD_LEARNED, None),                    # новое слово, «Знаю» в основном
    CARD_WAIT_8: (CARD_WAIT_24, CARD_HOURS_SECOND),
    CARD_WAIT_24: (CARD_LEARNED, None),
}


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
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=CARD_WAIT_8)
    # Сколько раз карточка показывалась — по нему видно, что даётся тяжело.
    seen_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Когда слово можно показать снова. У выученных пусто — их не показывают
    # вовсе. Это и есть таймер: 8 часов после первого «не знаю», 24 после повтора.
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("user_id", "deck", "card", name="uq_card_progress"),
        Index("ix_card_progress_user_deck", "user_id", "deck"),
        # Подход «Повторить» ищет слова с истёкшим сроком — по этому индексу.
        Index("ix_card_progress_due", "user_id", "deck", "due_at"),
    )


# --- паронимы ------------------------------------------------------------------
# Задание №5 живёт отдельно от карточек ударений: своя таблица, свои этапы, свои
# часы. Разное задание — разный код, правка одного не должна доставать до другого.
PARONYM_WAIT_8 = "wait8"
PARONYM_WAIT_24 = "wait24"
PARONYM_LEARNED = "learned"

PARONYM_HOURS_FIRST = 8
PARONYM_HOURS_SECOND = 24

# На какой этап переводит ответ «Знаю» и сколько после него ждать.
PARONYM_NEXT_STAGE = {
    None: (PARONYM_LEARNED, None),                       # новая группа, «Знаю» сразу
    PARONYM_WAIT_8: (PARONYM_WAIT_24, PARONYM_HOURS_SECOND),
    PARONYM_WAIT_24: (PARONYM_LEARNED, None),
}


class ParonymProgress(Base):
    """Что ученик уже знает в словнике паронимов.

    Одна строка на пару «ученик — группа паронимов». Единица прогресса — вся
    группа целиком: ответил верно про «эффектный / эффективный» — закрылись оба
    слова сразу. Колонки `deck` здесь нет: словник один.
    """

    __tablename__ = "paronym_progress"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Ключ группы: первое слово в нижнем регистре. Правка значения прогресс не
    # сбрасывает, правка самого слова — заводит новую группу, и это честно.
    card: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=PARONYM_WAIT_8)
    # Сколько раз группа показывалась — по нему видно, что даётся тяжело.
    seen_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Когда группу можно показать снова. У выученных пусто.
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("user_id", "card", name="uq_paronym_progress"),
        # Подход «Повторить» ищет группы с истёкшим сроком — по этому индексу.
        Index("ix_paronym_progress_due", "user_id", "due_at"),
    )


# --- средства выразительности --------------------------------------------------
# Задание №22 живёт отдельно от карточек и паронимов: своя таблица, свой код.
# Здесь не запоминание, а проверка, поэтому ни этапов, ни таймеров нет — копится
# только точность по трём группам приёмов.
class MeansStat(Base):
    """Точность ученика по одной группе средств выразительности.

    Одна строка на пару «ученик — группа», всего три строки на ученика. Счётчики,
    а не журнал ответов: на подбор вопросов точность не влияет и в общую
    статистику (вкладка «Статистика») не входит — она про решённые задания.
    Показывается только в самом тренажёре.
    """

    __tablename__ = "means_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Идентификатор группы из core/means.py: lexical, syntactic, phonetic.
    # Колонка названа group_id, а не group: GROUP — зарезервированное слово SQL.
    group_id: Mapped[str] = mapped_column(String(16), nullable=False)
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    correct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("user_id", "group_id", name="uq_means_stats"),
    )
