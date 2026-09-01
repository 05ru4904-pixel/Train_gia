"""Подключение к базе и инициализация схемы.

Настройки пула и порядок инициализации взяты из playbook (разделы 3.1-3.4): без пула
каждый запрос платит за TCP+TLS к базе через публичный прокси Railway, а create_all
не умеет менять уже существующие таблицы.
"""
import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings
from db.models import Base

log = logging.getLogger(__name__)

DATABASE_URL = settings.async_database_url
IS_POSTGRES = DATABASE_URL.startswith("postgresql")


def _engine_options() -> dict:
    """Настройки пула нужны и осмысленны только для Postgres.

    Параметры asyncpg (command_timeout) другой драйвер просто не примет, поэтому
    для SQLite, на котором гоняются тесты, отдаём пустые настройки.
    """
    if not IS_POSTGRES:
        return {}
    return {
        "pool_size": 10,
        "max_overflow": 5,
        "pool_pre_ping": True,   # Railway рвёт простаивающие соединения
        "pool_recycle": 1800,    # обновляем до того, как их порвут
        "pool_timeout": 30,
        "connect_args": {"timeout": 10, "command_timeout": 20},
    }


engine = create_async_engine(DATABASE_URL, echo=False, **_engine_options())

SessionMaker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


# Колонки, добавленные после первого релиза. create_all их не создаст — только
# ALTER (playbook 3.2). Все новые колонки обязаны быть nullable либо с DEFAULT,
# иначе записи, созданные раньше, перестанут читаться.
_MIGRATIONS = (
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS plan VARCHAR(16) NOT NULL DEFAULT 'free'",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS plan_until TIMESTAMPTZ",
    # Анкета ученика: класс, уровень математики, предметы по выбору, цель по баллам.
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS grade INTEGER",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS math_level VARCHAR(16)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS subjects JSONB",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS target_score VARCHAR(16)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS onboarded_at TIMESTAMPTZ",
    "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS passage TEXT",
    "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ",
    # Виды заданий: всё, что было в базе до появления колонки, — это choice.
    "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS kind VARCHAR(16) NOT NULL DEFAULT 'choice'",
    "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS match_left JSONB",
    "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS answers JSONB",
    # ID задания на сайте-источнике. Nullable: у всего, что залито до этой колонки,
    # и у заданий из /add его нет — такие ищутся по отпечатку содержимого.
    "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS source_id INTEGER",
    "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS skipped_count INTEGER",
    "ALTER TABLE session_items ADD COLUMN IF NOT EXISTS points INTEGER",
    "ALTER TABLE session_items ADD COLUMN IF NOT EXISTS typed TEXT",
)

# Postgres не индексирует внешние ключи сам (playbook 3.4). Индексы описаны и в
# моделях, но здесь дублируются как страховка для баз, созданных прошлыми версиями.
_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_tasks_number ON tasks (number)",
    "CREATE INDEX IF NOT EXISTS ix_tasks_number_kind ON tasks (number, kind)",
    "CREATE INDEX IF NOT EXISTS ix_tasks_source ON tasks (number, source_id)",
    "CREATE INDEX IF NOT EXISTS ix_sessions_user_status ON sessions (user_id, status)",
    "CREATE INDEX IF NOT EXISTS ix_sessions_user_finished ON sessions (user_id, status, finished_at)",
    "CREATE INDEX IF NOT EXISTS ix_session_items_session ON session_items (session_id)",
    "CREATE INDEX IF NOT EXISTS ix_session_items_stats ON session_items (session_id, task_number)",
    "CREATE INDEX IF NOT EXISTS ix_variant_items_variant ON variant_items (variant_id)",
)


async def init_db(attempts: int = 5, delay: float = 1.5) -> None:
    """Создаёт схему. С ретраями: в первые секунды после старта контейнера DNS базы
    может быть ещё не готов (playbook 3.3)."""
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                if IS_POSTGRES:
                    # ALTER ... IF NOT EXISTS и TIMESTAMPTZ — синтаксис Postgres.
                    for statement in _MIGRATIONS:
                        await conn.execute(text(statement))
                for statement in _INDEXES:
                    await conn.execute(text(statement))
            log.info("База готова")
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            log.warning("Не удалось подключиться к базе (%s/%s): %s", attempt, attempts, exc)
            if attempt < attempts:
                await asyncio.sleep(delay * attempt)
    raise RuntimeError(f"База недоступна после {attempts} попыток: {last_error}")


async def dispose_db() -> None:
    await engine.dispose()
