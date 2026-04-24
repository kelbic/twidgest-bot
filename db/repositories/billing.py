"""Управление платежами и тарифами."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Payment, User
from tiers import Tier


async def activate_tier(
    session: AsyncSession,
    user_id: int,
    tier: Tier,
    duration_days: int = 30,
    extend_existing: bool = True,
) -> datetime:
    """Активирует тариф для пользователя на duration_days.

    Если у пользователя уже активна подписка того же tier и extend_existing=True —
    добавляет дни к текущей дате окончания (а не от now).
    """
    result = await session.execute(select(User).where(User.tg_user_id == user_id))
    user = result.scalar_one()

    now = datetime.utcnow()
    base = now
    if (
        extend_existing
        and user.tier == tier.value
        and user.tier_expires_at is not None
        and user.tier_expires_at > now
    ):
        base = user.tier_expires_at

    new_expiry = base + timedelta(days=duration_days)
    user.tier = tier.value
    user.tier_expires_at = new_expiry
    await session.commit()
    return new_expiry


async def downgrade_to_free(session: AsyncSession, user_id: int) -> None:
    result = await session.execute(select(User).where(User.tg_user_id == user_id))
    user = result.scalar_one()
    user.tier = "free"
    user.tier_expires_at = None
    await session.commit()


async def record_payment(
    session: AsyncSession,
    user_id: int,
    amount_stars: int,
    tier: Tier,
    telegram_payment_charge_id: str,
) -> Payment:
    payment = Payment(
        user_id=user_id,
        amount_stars=amount_stars,
        tier=tier.value,
        telegram_payment_charge_id=telegram_payment_charge_id,
    )
    session.add(payment)
    await session.commit()
    await session.refresh(payment)
    return payment


async def get_user_payments(
    session: AsyncSession, user_id: int, limit: int = 10
) -> list[Payment]:
    result = await session.execute(
        select(Payment)
        .where(Payment.user_id == user_id)
        .order_by(Payment.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
