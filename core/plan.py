"""Активность канала в модели «слот = канал» (этап C прайсинга).

Единственная точка истины для вопроса «должен ли этот канал работать».
Канал активен, если: владелец — админ (боевые витрины), ИЛИ оплачен
(paid_until в будущем), ИЛИ на триале (trial_until в будущем).
Никакого free-уровня: после триала без оплаты канал молчит полностью.
"""
from __future__ import annotations

from datetime import datetime

from config import Config

_ADMIN_ID = Config().admin_user_id

PRICE_STARS = 999
SLOT_DAYS = 30
TRIAL_DAYS = 7


def channel_active(channel, now: datetime | None = None) -> bool:
    if channel.user_id == _ADMIN_ID:
        return True
    now = now or datetime.utcnow()
    if channel.paid_until and channel.paid_until > now:
        return True
    if channel.trial_until and channel.trial_until > now:
        return True
    return False


def channel_status(channel, now: datetime | None = None) -> str:
    """'admin' | 'paid' | 'trial' | 'inactive' — для UI и логов."""
    if channel.user_id == _ADMIN_ID:
        return "admin"
    now = now or datetime.utcnow()
    if channel.paid_until and channel.paid_until > now:
        return "paid"
    if channel.trial_until and channel.trial_until > now:
        return "trial"
    return "inactive"


def extension_base(channel, now: datetime | None = None) -> datetime:
    """От какой даты продлевать при оплате: конец оплаты/триала или сейчас."""
    now = now or datetime.utcnow()
    cands = [now]
    if channel.paid_until:
        cands.append(channel.paid_until)
    if channel.trial_until:
        cands.append(channel.trial_until)
    return max(cands)


# Дневной лимит постов на КАНАЛ (по статусу). Старая модель считала на юзера
# из тарифа — в слот-модели квота принадлежит каналу.
POSTS_PER_DAY = {"admin": 500, "paid": 50, "trial": 20, "inactive": 0}


def posts_cap(channel) -> int:
    return POSTS_PER_DAY[channel_status(channel)]
