"""СБП через прямой API ЮKassa: тело платежа, идемпотентный зачёт, дочитка.

Сеть не трогаем нигде: клиент ЮKassa в этих тестах не создаётся — проверяем
сборку тела запроса и логику зачёта поверх тестовой БД.
"""
from __future__ import annotations

import asyncio

import pytest

import bot.handlers.billing as b
import db.session as dbs
from core.yookassa import build_sbp_payment_body
from db.models import Channel, Payment, RubPayment
from sqlalchemy import select


def test_sbp_body_shape():
    body = build_sbp_payment_body(
        1490, "Twidgest: «Канал», 30 дней",
        "https://t.me/TwidgestBot", {"channel_id": "5"},
    )
    assert body["amount"] == {"value": "1490.00", "currency": "RUB"}
    assert body["payment_method_data"] == {"type": "sbp"}
    assert body["confirmation"]["return_url"] == "https://t.me/TwidgestBot"
    assert body["capture"] is True
    # Чек не шлём: магазин на самозанятом, фискализации 54-ФЗ нет
    assert "receipt" not in body


def test_sbp_body_receipt_passthrough():
    body = build_sbp_payment_body(60, "x", "https://t.me/x", {}, receipt={"items": []})
    assert body["receipt"] == {"items": []}


def test_sbp_hidden_without_keys(monkeypatch):
    monkeypatch.setattr(b._cfg, "yookassa_shop_id", "", raising=False)
    monkeypatch.setattr(b._cfg, "yookassa_secret_key", "", raising=False)
    assert b.sbp_visible() is False
    monkeypatch.setattr(b._cfg, "yookassa_shop_id", "1418169", raising=False)
    monkeypatch.setattr(b._cfg, "yookassa_secret_key", "live_x", raising=False)
    assert b.sbp_visible() is True


class BotStub:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))


@pytest.fixture
async def sbp_db(tmp_path):
    dbs.init_engine(f"sqlite+aiosqlite:///{tmp_path}/sbp.db")
    await dbs.init_db()
    async with dbs.session_maker()() as session:
        channel = Channel(user_id=42, title="Тестовый канал")
        session.add(channel)
        await session.commit()
        await session.refresh(channel)
        session.add(RubPayment(
            yk_payment_id="yk-uuid-1", user_id=42,
            channel_id=channel.id, amount_rub=1490,
        ))
        await session.commit()
        channel_id = channel.id
    yield channel_id
    dbs._engine = dbs._session_maker = None


async def _payments_count() -> int:
    async with dbs.session_maker()() as session:
        result = await session.execute(select(Payment))
        return len(list(result.scalars().all()))


async def test_settle_succeeded_once(sbp_db):
    bot = BotStub()
    assert await b._settle_sbp_payment(bot, "yk-uuid-1", "succeeded") is True
    # Повторный зачёт (второй поллер/кнопка) — no-op, но «решён»
    assert await b._settle_sbp_payment(bot, "yk-uuid-1", "succeeded") is True

    assert await _payments_count() == 1
    async with dbs.session_maker()() as session:
        channel = (await session.execute(
            select(Channel).where(Channel.id == sbp_db)
        )).scalar_one()
        assert channel.paid_until is not None
        rp = (await session.execute(select(RubPayment))).scalar_one()
        assert rp.status == "succeeded"
    # Юзеру ушло ровно одно «оплата получена»
    assert len(bot.sent) == 1 and "Оплата получена" in bot.sent[0][1]


async def test_settle_race_single_credit(sbp_db):
    bot = BotStub()
    await asyncio.gather(
        b._settle_sbp_payment(bot, "yk-uuid-1", "succeeded"),
        b._settle_sbp_payment(bot, "yk-uuid-1", "succeeded"),
    )
    assert await _payments_count() == 1
    assert len(bot.sent) == 1


async def test_settle_canceled_no_credit(sbp_db):
    bot = BotStub()
    assert await b._settle_sbp_payment(bot, "yk-uuid-1", "canceled") is True
    assert await _payments_count() == 0
    async with dbs.session_maker()() as session:
        channel = (await session.execute(
            select(Channel).where(Channel.id == sbp_db)
        )).scalar_one()
        assert channel.paid_until is None
    assert len(bot.sent) == 1 and "отмен" in bot.sent[0][1]


async def test_settle_pending_keeps_waiting(sbp_db):
    bot = BotStub()
    assert await b._settle_sbp_payment(bot, "yk-uuid-1", "pending") is False
    assert await _payments_count() == 0
    assert bot.sent == []


async def test_resume_skips_stale(sbp_db, monkeypatch):
    """Свежий pending дочитывается, старше суток — нет (ЮKassa его отменила)."""
    from datetime import datetime, timedelta

    polled: list[str] = []

    async def fake_poll(bot, yk_id, **kwargs):
        polled.append(yk_id)

    monkeypatch.setattr(b, "_poll_sbp_payment", fake_poll)
    async with dbs.session_maker()() as session:
        session.add(RubPayment(
            yk_payment_id="yk-stale", user_id=42, channel_id=sbp_db,
            amount_rub=1490, created_at=datetime.utcnow() - timedelta(hours=30),
        ))
        await session.commit()

    resumed = await b.resume_pending_sbp(BotStub())
    await asyncio.sleep(0)  # даём create_task стартануть
    assert resumed == 1
    assert polled == ["yk-uuid-1"]
