"""Раз в день удаляет устаревшие строки из таблиц-накопителей.

TTL по таблицам:
- digest_queue (7 дней): твит старше 7 дней не имеет шанса попасть ни в
  single (фильтр 24ч), ни в digest (фильтр 14ч) — хранить бессмысленно.
- rejection_log (30 дней): channel_health и /channels смотрят окно 24ч,
  месяц хватает на любую диагностику (включая review:* от ранкера).
  Без TTL таблица росла неограниченно — каждый цикл пишет отказы.
- scout_suggestions (30 дней): кнопки карточки протухают через 48ч
  (SUGGESTION_TTL_HOURS в хендлере), записи старше месяца — мусор.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import delete

from db.models import DigestQueueItem, RejectionLog, ScoutSuggestion
from db.session import session_maker

logger = logging.getLogger(__name__)

QUEUE_TTL_DAYS = 7
REJECTION_TTL_DAYS = 30
SCOUT_TTL_DAYS = 30


async def run_queue_cleanup() -> None:
    """Удаляет устаревшие строки из digest_queue, rejection_log, scout_suggestions."""
    now = datetime.utcnow()
    logger.info(
        "=== Queue cleanup started (queue=%dd, rejections=%dd, scout=%dd) ===",
        QUEUE_TTL_DAYS, REJECTION_TTL_DAYS, SCOUT_TTL_DAYS,
    )

    async with session_maker()() as session:
        q_res = await session.execute(
            delete(DigestQueueItem)
            .where(DigestQueueItem.queued_at < now - timedelta(days=QUEUE_TTL_DAYS))
        )
        r_res = await session.execute(
            delete(RejectionLog)
            .where(RejectionLog.rejected_at < now - timedelta(days=REJECTION_TTL_DAYS))
        )
        s_res = await session.execute(
            delete(ScoutSuggestion)
            .where(ScoutSuggestion.created_at < now - timedelta(days=SCOUT_TTL_DAYS))
        )
        await session.commit()

    logger.info(
        "=== Queue cleanup done. Deleted: queue=%d, rejections=%d, scout=%d ===",
        q_res.rowcount or 0, r_res.rowcount or 0, s_res.rowcount or 0,
    )
