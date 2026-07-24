"""Воронка /admin funnel: стадии из существующих таблиц, исключение админа."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

import db.session as dbs
from db.models import Channel, Payment, PostLog, User
from db.repositories.admin import get_funnel


@pytest.fixture
async def session_maker_fx(tmp_path):
    dbs.init_engine(f"sqlite+aiosqlite:///{tmp_path}/funnel.db")
    await dbs.init_db()
    yield dbs.session_maker()
    dbs._engine = dbs._session_maker = None


async def _seed(sm):
    async with sm() as s:
        # админ (исключается) + 4 юзера на разных стадиях
        s.add_all([
            User(tg_user_id=1),               # админ
            User(tg_user_id=10),              # только /start
            User(tg_user_id=20),              # создал канал
            User(tg_user_id=30),              # создал + привязал + посты
            User(tg_user_id=40),              # всё + оплатил
        ])
        s.add_all([
            Channel(user_id=1, title="admin", target_chat_id=-1),
            Channel(user_id=20, title="a"),
            Channel(user_id=30, title="b", target_chat_id=-100),
            Channel(user_id=40, title="c", target_chat_id=-200),
        ])
        await s.flush()
        s.add_all([
            PostLog(user_id=30, target_id=1),
            PostLog(user_id=30, target_id=1),
            PostLog(user_id=40, target_id=2),
        ])
        s.add(Payment(user_id=40, amount_stars=999, currency="XTR",
                      tier="slot:3", telegram_payment_charge_id="c1"))
        await s.commit()


async def test_funnel_counts_and_admin_excluded(session_maker_fx):
    await _seed(session_maker_fx)
    async with session_maker_fx() as s:
        stages = await get_funnel(s, since=None, exclude_user_id=1)
    assert stages == {
        "registered": 4,
        "created_channel": 3,
        "bound_channel": 2,
        "got_posts": 2,
        "paid": 1,
    }


async def test_funnel_cohort_by_date(session_maker_fx):
    await _seed(session_maker_fx)
    async with session_maker_fx() as s:
        # created_at ставится server_default'ом «сейчас» — когорта из будущего пуста
        stages = await get_funnel(
            s, since=datetime.utcnow() + timedelta(days=1), exclude_user_id=1
        )
    assert stages["registered"] == 0
    assert stages["paid"] == 0
