"""Раз в день: даунгрейд пользователей с истёкшими подписками."""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import and_, select

from db.models import User
from db.repositories.billing import downgrade_to_free
from db.session import session_maker

logger = logging.getLogger(__name__)


async def run_expiry_check() -> None:
    logger.info("=== Expiry check started ===")
    async with session_maker()() as session:
        result = await session.execute(
            select(User).where(
                and_(
                    User.tier != "free",
                    User.tier_expires_at != None,  # noqa: E711
                    User.tier_expires_at < datetime.utcnow(),
                )
            )
        )
        expired = list(result.scalars().all())

    for user in expired:
        async with session_maker()() as session:
            await downgrade_to_free(session, user.tg_user_id)
            logger.info("User %s downgraded to free (expired)", user.tg_user_id)

    logger.info("=== Expiry check done. Downgraded %d users ===", len(expired))
