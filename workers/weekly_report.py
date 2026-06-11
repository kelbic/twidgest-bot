"""Еженедельный отчёт владельцу: что бот сделал за 7 дней (retention-инструмент).

Метрики на канал: посты (single/дайджесты), часы сэкономленной ручной работы
(та же оценка MINUTES_PER_POST, что в чеке триала), средний interest
опубликованного потока (скоры ранкера из digest_queue) и сколько мусора
ревьювер отфильтровал. Шлём владельцу одним сообщением по всем его каналам.
Неактивные каналы пропускаем (нечего отчитывать), админ получает свой отчёт.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from aiogram import Bot
from sqlalchemy import and_, func, select

from core.plan import channel_active
from db.models import Channel, DigestQueueItem, PostLog, RejectionLog
from db.session import session_maker
from workers.expiry_check import MINUTES_PER_POST, _hours_saved

logger = logging.getLogger(__name__)


async def _channel_week(session, ch: Channel, since: datetime) -> str | None:
    posts = (await session.execute(
        select(func.count(PostLog.id)).where(and_(
            PostLog.target_id == ch.id, PostLog.posted_at > since)))).scalar_one()
    if not posts:
        return None
    digests = (await session.execute(
        select(func.count(PostLog.id)).where(and_(
            PostLog.target_id == ch.id, PostLog.posted_at > since,
            PostLog.is_digest == True)))).scalar_one()  # noqa: E712
    avg_int = (await session.execute(
        select(func.avg(DigestQueueItem.interest_score)).where(and_(
            DigestQueueItem.channel_id == ch.id,
            DigestQueueItem.queued_at > since,
            DigestQueueItem.interest_score != None)))).scalar_one()  # noqa: E711
    junked = (await session.execute(
        select(func.count(RejectionLog.id)).where(and_(
            RejectionLog.channel_id == ch.id,
            RejectionLog.rejected_at > since,
            RejectionLog.reason.like("review:%"))))).scalar_one()

    line = (f"📣 <b>{ch.title[:48]}</b>\n"
            f"   Постов: <b>{posts}</b> (дайджестов {digests}, "
            f"одиночных {posts - digests})")
    if avg_int is not None:
        line += f"\n   Средний interest потока: {float(avg_int):.1f}/10"
    if junked:
        line += f"\n   Отфильтровано ревьювером: {junked} слабых тем"
    return line


async def run_weekly_report(bot: Bot) -> None:
    logger.info("=== Weekly report started ===")
    since = datetime.utcnow() - timedelta(days=7)
    sent = 0

    async with session_maker()() as session:
        result = await session.execute(select(Channel))
        channels = list(result.scalars().all())

        by_owner: dict[int, list[Channel]] = {}
        for ch in channels:
            if channel_active(ch):
                by_owner.setdefault(ch.user_id, []).append(ch)

        for owner, chans in by_owner.items():
            blocks = []
            total_posts = 0
            for ch in chans:
                block = await _channel_week(session, ch, since)
                if block:
                    blocks.append(block)
                    posts = (await session.execute(
                        select(func.count(PostLog.id)).where(and_(
                            PostLog.target_id == ch.id,
                            PostLog.posted_at > since)))).scalar_one()
                    total_posts += posts
            if not blocks:
                continue
            hours = _hours_saved(total_posts)
            text = (
                "📊 <b>Неделя с Twidgest</b>\n\n"
                + "\n\n".join(blocks)
                + f"\n\n⏱ Итого {total_posts} постов — это ~<b>{hours} ч</b> "
                f"ручной работы (поиск, перевод, редактура по "
                f"~{MINUTES_PER_POST} мин на пост), которые бот взял на себя."
            )
            try:
                await bot.send_message(owner, text)
                sent += 1
            except Exception as exc:
                logger.warning("weekly: failed to send to %d: %s", owner, exc)

    logger.info("=== Weekly report done. Sent: %d ===", sent)
