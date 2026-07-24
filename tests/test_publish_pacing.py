"""Пейсинг single-постов: настройка владельца, floors по статусу, активация."""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from workers.viral_picker import (
    ACTIVATION_INTERVAL_MIN,
    DEFAULT_SINGLE_INTERVAL_MIN,
    pacing_minutes,
)


def _chan(**kw) -> SimpleNamespace:
    base = dict(
        user_id=2,  # не админ (conftest: ADMIN_USER_ID=1)
        paid_until=datetime.utcnow() + timedelta(days=10),
        trial_until=None,
        created_at=datetime.utcnow() - timedelta(days=5),
        single_interval_minutes=DEFAULT_SINGLE_INTERVAL_MIN,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_default_after_first_day():
    assert pacing_minutes(_chan()) == DEFAULT_SINGLE_INTERVAL_MIN


def test_activation_boost_first_day_on_default():
    ch = _chan(created_at=datetime.utcnow() - timedelta(hours=2))
    assert pacing_minutes(ch) == ACTIVATION_INTERVAL_MIN


def test_activation_respects_explicit_slow_choice():
    # Владелец сам поставил «раз в 2 часа» в первый день — уважаем
    ch = _chan(
        created_at=datetime.utcnow() - timedelta(hours=2),
        single_interval_minutes=120,
    )
    assert pacing_minutes(ch) == 120


def test_owner_setting_honored():
    assert pacing_minutes(_chan(single_interval_minutes=20)) == 20
    assert pacing_minutes(_chan(single_interval_minutes=240)) == 240


def test_inactive_floor_wins():
    ch = _chan(paid_until=None, trial_until=None, single_interval_minutes=15)
    assert pacing_minutes(ch) == 60  # floor «inactive»


def test_paid_floor_is_15():
    assert pacing_minutes(_chan(single_interval_minutes=15)) == 15
