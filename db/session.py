"""Единая точка для асинхронной БД-сессии."""
from __future__ import annotations

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
