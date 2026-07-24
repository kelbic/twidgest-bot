"""Дожим триала: чек 5-го дня, «последний день», кнопки оплаты (Stars/₽)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

import db.session as dbs
from db.models import Channel, PostLog, User
from workers.expiry_check import _pay_kb, run_expiry_check


class FakeBot:
    def __init__(self):
        self.sent: list[tuple[int, str, object]] = []

    async def send_message(self, chat_id, text, reply_markup=None, **kwargs):
        self.sent.append((chat_id, text, reply_markup))


@pytest.fixture
async def session_maker_fx(tmp_path):
    dbs.init_engine(f"sqlite+aiosqlite:///{tmp_path}/expiry.db")
    await dbs.init_db()
    yield dbs.session_maker()
    dbs._engine = dbs._session_maker = None


async def _trial_channel(sm, trial_ends_in_hours: float, posts: int = 3,
                         paid_until=None) -> int:
    async with sm() as s:
        s.add(User(tg_user_id=2))
        ch = Channel(
            user_id=2, title="Тестовый",
            trial_until=datetime.utcnow() + timedelta(hours=trial_ends_in_hours),
            paid_until=paid_until,
        )
        s.add(ch)
        await s.flush()
        for _ in range(posts):
            s.add(PostLog(user_id=2, target_id=ch.id))
        await s.commit()
        return ch.id


async def test_day5_check_sent(session_maker_fx):
    await _trial_channel(session_maker_fx, trial_ends_in_hours=30)
    bot = FakeBot()
    await run_expiry_check(bot)
    assert len(bot.sent) == 1
    _, text, kb = bot.sent[0]
    assert "через 2 дня" in text and "3 постов" in text
    assert any("payslot:" in b.callback_data for row in kb.inline_keyboard for b in row)


async def test_last_day_nudge_sent(session_maker_fx):
    await _trial_channel(session_maker_fx, trial_ends_in_hours=10)
    bot = FakeBot()
    await run_expiry_check(bot)
    assert len(bot.sent) == 1
    _, text, _kb = bot.sent[0]
    assert "последний день" in text and "3 постов" in text


async def test_last_day_skipped_if_already_paid(session_maker_fx):
    await _trial_channel(
        session_maker_fx, trial_ends_in_hours=10,
        paid_until=datetime.utcnow() + timedelta(days=30),
    )
    bot = FakeBot()
    await run_expiry_check(bot)
    assert bot.sent == []


async def test_pay_kb_stars_only_without_provider(monkeypatch):
    monkeypatch.delenv("PAYMENT_PROVIDER_TOKEN", raising=False)
    kb = _pay_kb(7, "Оплатить")
    cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert cbs == ["payslot:7"]


async def test_pay_kb_rub_first_with_provider(monkeypatch):
    monkeypatch.setenv("PAYMENT_PROVIDER_TOKEN", "test:token")
    monkeypatch.setenv("PRICE_RUB", "1490")
    kb = _pay_kb(7, "Оплатить")
    cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert cbs == ["payrub:7", "payslot:7"]
    assert "1490 ₽" in kb.inline_keyboard[0][0].text
