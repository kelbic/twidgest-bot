"""Запросы для админ-панели: статистика, поиск юзеров."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import (
    DigestLog,
    Payment,
    PostLog,
    Source,
    Target,
    User,
)


async def get_user_full(session: AsyncSession, tg_user_id: int) -> User | None:
    """Юзер со всеми relationships."""
    result = await session.execute(
        select(User)
        .where(User.tg_user_id == tg_user_id)
        .options(
            selectinload(User.sources),
            selectinload(User.targets),
            selectinload(User.settings),
        )
    )
    return result.scalar_one_or_none()


async def get_global_stats(session: AsyncSession) -> dict[str, int]:
    """Глобальная статистика бота."""
    now = datetime.utcnow()
    last_24h = now - timedelta(hours=24)
    last_7d = now - timedelta(days=7)

    total_users = (
        await session.execute(select(func.count(User.tg_user_id)))
    ).scalar_one()

    paid_users = (
        await session.execute(
            select(func.count(User.tg_user_id)).where(User.tier != "free")
        )
    ).scalar_one()

    active_paid = (
        await session.execute(
            select(func.count(User.tg_user_id)).where(
                User.tier != "free",
                User.tier_expires_at > now,
            )
        )
    ).scalar_one()

    new_users_7d = (
        await session.execute(
            select(func.count(User.tg_user_id)).where(User.created_at > last_7d)
        )
    ).scalar_one()

    total_sources = (
        await session.execute(select(func.count(Source.id)))
    ).scalar_one()

    total_targets = (
        await session.execute(select(func.count(Target.id)))
    ).scalar_one()

    posts_24h = (
        await session.execute(
            select(func.count(PostLog.id)).where(PostLog.posted_at > last_24h)
        )
    ).scalar_one()

    digests_24h = (
        await session.execute(
            select(func.count(DigestLog.id)).where(DigestLog.posted_at > last_24h)
        )
    ).scalar_one()

    revenue_total = (
        await session.execute(select(func.sum(Payment.amount_stars)))
    ).scalar_one() or 0

    revenue_30d = (
        await session.execute(
            select(func.sum(Payment.amount_stars)).where(
                Payment.created_at > (now - timedelta(days=30))
            )
        )
    ).scalar_one() or 0

    return {
        "total_users": int(total_users),
        "paid_users_total": int(paid_users),
        "active_paid_users": int(active_paid),
        "new_users_7d": int(new_users_7d),
        "total_sources": int(total_sources),
        "total_targets": int(total_targets),
        "posts_24h": int(posts_24h),
        "digests_24h": int(digests_24h),
        "revenue_total_stars": int(revenue_total),
        "revenue_30d_stars": int(revenue_30d),
    }


async def get_all_user_ids(session: AsyncSession) -> list[int]:
    """Для broadcast'а."""
    result = await session.execute(
        select(User.tg_user_id).where(User.is_blocked == False)  # noqa: E712
    )
    return [row[0] for row in result.all()]
