"""Карточка /setinterval: кнопки по режиму канала и floors по статусу."""
from __future__ import annotations

from datetime import datetime, timedelta

from bot.handlers.sources import _fmt_single_interval, _interval_card
from db.models import Channel


def _channel(mode: str = "hybrid", **kw) -> Channel:
    ch = Channel(
        user_id=2, title="Тестовый", mode=mode,
        digest_interval_hours=12, single_interval_minutes=30,
    )
    ch.id = 5
    ch.paid_until = datetime.utcnow() + timedelta(days=5)
    ch.trial_until = None
    for key, value in kw.items():
        setattr(ch, key, value)
    return ch


def _callbacks(kb) -> list[str]:
    return [b.callback_data for row in kb.inline_keyboard for b in row]


def test_hybrid_has_both_rows():
    _text, kb = _interval_card(_channel("hybrid"))
    cbs = _callbacks(kb)
    assert any(c.startswith("sint:5:") for c in cbs)
    assert any(c.startswith("dint:5:") for c in cbs)


def test_digest_mode_has_no_single_buttons():
    _text, kb = _interval_card(_channel("digest"))
    cbs = _callbacks(kb)
    assert not any(c.startswith("sint:") for c in cbs)
    assert any(c.startswith("dint:") for c in cbs)


def test_trial_digest_floor_filters_fast_options():
    # trial: floor дайджеста 6 ч — кнопок «2 ч» и «4 ч» быть не должно
    ch = _channel("digest", paid_until=None,
                  trial_until=datetime.utcnow() + timedelta(days=3))
    _text, kb = _interval_card(ch)
    hours = {int(c.split(":")[2]) for c in _callbacks(kb) if c.startswith("dint:")}
    assert hours == {6, 12, 24}


def test_paid_single_floor_allows_4_per_hour():
    _text, kb = _interval_card(_channel("hybrid"))
    minutes = {int(c.split(":")[2]) for c in _callbacks(kb) if c.startswith("sint:")}
    assert 15 in minutes  # paid: до 4 постов/час доступно


def test_fmt_single_interval():
    assert _fmt_single_interval(15) == "не чаще 4 раз в час"
    assert _fmt_single_interval(60) == "не чаще 1 раза в час"
    assert _fmt_single_interval(240) == "не чаще 1 раза в 4 ч"
