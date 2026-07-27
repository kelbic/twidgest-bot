"""Единая точка для асинхронной БД-сессии."""
from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from db.models import Base

_engine = None
_session_maker = None


def init_engine(database_url: str) -> None:
    global _engine, _session_maker
    _engine = create_async_engine(database_url, echo=False, future=True)

    if database_url.startswith("sqlite"):
        # WAL: читатели (essayist в ro-режиме, скаут) не блокируют писателя
        # и наоборот. busy_timeout вместо мгновенного SQLITE_BUSY.
        # synchronous=NORMAL — штатная пара к WAL (fsync на чекпоинтах).
        # Режим WAL после первого включения сохраняется в заголовке файла,
        # повторное выполнение PRAGMA идемпотентно.
        @event.listens_for(_engine.sync_engine, "connect")
        def _set_sqlite_pragmas(dbapi_conn, _connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

    _session_maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    """Создаёт все таблицы и добавляет недостающие колонки.

    create_all не трогает уже существующие таблицы, поэтому новые колонки
    моделей доливаем сами: PRAGMA table_info → ALTER TABLE ADD COLUMN.
    Безопасно вызывать многократно.
    """
    if _engine is None:
        raise RuntimeError("Call init_engine() first")
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_ensure_columns)
        # Данные: до 27.07.2026 дефолт пейсинга был 30 мин, и выбрать 30
        # можно было кнопкой «2/час». Суб-часовые варианты удалены, поэтому
        # оставшиеся 30 — это нетронутый старый дефолт; поднимаем до нового.
        await conn.exec_driver_sql(
            "UPDATE channels SET single_interval_minutes = 60 "
            "WHERE single_interval_minutes = 30"
        )


def _ensure_columns(sync_conn) -> None:
    """Лёгкая миграция для SQLite: добавляет колонки, появившиеся в моделях.

    Покрывает простые случаи (nullable-колонка или скалярный default) —
    ровно то, чем прирастает схема. NOT NULL ставим только вместе с DEFAULT,
    иначе SQLite откажет на непустой таблице.
    """
    import logging

    from sqlalchemy import inspect as sa_inspect

    if sync_conn.dialect.name != "sqlite":
        return
    logger = logging.getLogger(__name__)
    inspector = sa_inspect(sync_conn)
    for table in Base.metadata.sorted_tables:
        existing = {c["name"] for c in inspector.get_columns(table.name)}
        for col in table.columns:
            if col.name in existing:
                continue
            ddl = (
                f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" '
                f"{col.type.compile(dialect=sync_conn.dialect)}"
            )
            default = None
            if col.default is not None and col.default.is_scalar:
                default = col.default.arg
            if default is not None:
                if isinstance(default, bool):
                    default = int(default)
                if isinstance(default, (int, float)):
                    ddl += f" DEFAULT {default}"
                else:
                    escaped = str(default).replace("'", "''")
                    ddl += f" DEFAULT '{escaped}'"
                if not col.nullable:
                    ddl += " NOT NULL"
            sync_conn.exec_driver_sql(ddl)
            logger.info("db migrate: %s + колонка %s", table.name, col.name)


def session_maker() -> async_sessionmaker[AsyncSession]:
    if _session_maker is None:
        raise RuntimeError("Call init_engine() first")
    return _session_maker
