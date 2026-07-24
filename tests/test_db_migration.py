"""Авто-миграция схемы: init_db доливает недостающие колонки в старые таблицы.

create_all не трогает существующие таблицы — без этого механизма новая
колонка модели молча отсутствовала бы в проде до ручного ALTER.
"""
from __future__ import annotations

import pytest

import db.session as dbs


@pytest.fixture
async def engine(tmp_path):
    dbs.init_engine(f"sqlite+aiosqlite:///{tmp_path}/old.db")
    yield dbs._engine
    dbs._engine = dbs._session_maker = None


async def _columns(engine, table: str) -> set[str]:
    async with engine.connect() as conn:
        res = await conn.exec_driver_sql(f"PRAGMA table_info({table})")
        return {row[1] for row in res.fetchall()}


async def test_adds_missing_columns_to_old_tables(engine):
    # «Старый прод»: payments без currency, channels без single_interval_minutes
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            "CREATE TABLE payments (id INTEGER PRIMARY KEY, user_id BIGINT, "
            "amount_stars INTEGER, tier VARCHAR(16), "
            "telegram_payment_charge_id VARCHAR(128), created_at DATETIME)"
        )
        await conn.exec_driver_sql(
            "CREATE TABLE channels (id INTEGER PRIMARY KEY, user_id BIGINT, "
            "title VARCHAR(255), digest_interval_hours INTEGER)"
        )
        await conn.exec_driver_sql(
            "INSERT INTO payments (user_id, amount_stars, tier) VALUES (1, 999, 'slot:1')"
        )

    await dbs.init_db()

    assert "currency" in await _columns(engine, "payments")
    assert "single_interval_minutes" in await _columns(engine, "channels")

    # Существующие строки получили дефолты (NOT NULL DEFAULT в ALTER)
    async with engine.connect() as conn:
        res = await conn.exec_driver_sql("SELECT currency FROM payments")
        assert res.scalar_one() == "XTR"


async def test_idempotent_on_fresh_db(engine):
    await dbs.init_db()
    await dbs.init_db()  # повторный вызов не должен падать
    assert "single_interval_minutes" in await _columns(engine, "channels")
