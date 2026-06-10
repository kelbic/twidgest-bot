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
    """Создаёт все таблицы, если их нет. Безопасно вызывать многократно."""
    if _engine is None:
        raise RuntimeError("Call init_engine() first")
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def session_maker() -> async_sessionmaker[AsyncSession]:
    if _session_maker is None:
        raise RuntimeError("Call init_engine() first")
    return _session_maker
