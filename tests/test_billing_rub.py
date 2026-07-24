"""Оплата рублями: валюта в записи платежа и отображение сумм."""
from __future__ import annotations

import pytest

import db.session as dbs
from bot.handlers.billing import payment_amount_display
from db.models import Payment
from db.repositories.billing import get_user_payments, record_payment


def test_display_stars():
    p = Payment(user_id=1, amount_stars=999, currency="XTR", tier="slot:1")
    assert payment_amount_display(p) == "999⭐"


def test_display_rub_kopecks():
    p = Payment(user_id=1, amount_stars=149000, currency="RUB", tier="slot:1")
    assert payment_amount_display(p) == "1490 ₽"


def test_display_legacy_row_without_currency():
    # Строки, записанные до миграции: ORM ещё не проставил default
    p = Payment(user_id=1, amount_stars=999, tier="slot:1")
    assert payment_amount_display(p) == "999⭐"


@pytest.fixture
async def session_maker_fx(tmp_path):
    dbs.init_engine(f"sqlite+aiosqlite:///{tmp_path}/pay.db")
    await dbs.init_db()
    yield dbs.session_maker()
    dbs._engine = dbs._session_maker = None


async def test_record_payment_rub_roundtrip(session_maker_fx):
    async with session_maker_fx() as session:
        p = await record_payment(
            session, 42, 149000, "slot:7", "charge-rub-1", currency="RUB"
        )
        assert p is not None and p.currency == "RUB"

        # дубль charge_id (ретрай Telegram) — не записывается
        dup = await record_payment(
            session, 42, 149000, "slot:7", "charge-rub-1", currency="RUB"
        )
        assert dup is None

        payments = await get_user_payments(session, 42)
        assert len(payments) == 1
        assert payment_amount_display(payments[0]) == "1490 ₽"
