"""Раз в день удаляет старые твиты из digest_queue.

TTL = 7 дней. Твит старше 7 дней не имеет шанса попасть ни в single (фильтр 24ч),
ни в digest (фильтр 14ч), поэтому хранить его бессмысленно — занимает место на диске.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import delete

from db.models import DigestQueueItem
from db.session import session_maker

logger = logging.getLogger(__name__)

TTL_DAYS = 7


async def run_queue_cleanup() -> None:
    """Удаляет из digest_queue твиты старше TTL_DAYS дней."""
    cutoff = datetime.utcnow() - timedelta(days=TTL_DAYS)
    logger.info("=== Queue cleanup started (TTL=%d days) ===", TTL_DAYS)

    async with session_maker()() as session:
        result = await session.execute(
            delete(DigestQueueItem)
            .where(DigestQueueItem.queued_at < cutoff)
        )
        deleted_count = result.rowcount or 0
        await session.commit()

    logger.info(
        "=== Queue cleanup done. Deleted: %d rows (queued before %s) ===",
        deleted_count, cutoff.isoformat(),
    )
