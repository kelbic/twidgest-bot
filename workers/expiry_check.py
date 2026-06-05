"""Раз в день: уведомление за 1 день до истечения + даунгрейд истёкших."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from aiogram import Bot
from sqlalchemy import and_, select, update as sa_update

from db.models import Channel, PostLog, User
from db.repositories.billing import downgrade_to_free
from db.session import session_maker

logger = logging.getLogger(__name__)


NOTIFY_TEXT = """Привет 👋

Триал на боте заканчивается завтра — {expires_str} UTC.

За время триала у тебя опубликовано {posts_count} постов в каналах. Рад, что бот пригодился.

Чтобы продолжить — Pro 3000 stars/мес. Команда /upgrade подскажет детали.

Если решишь не продлевать — каналы просто перестанут публиковать после истечения, ничего удалять не надо.

Если есть что улучшить — напиши, я слушаю."""


async def _send_trial_notification(bot: Bot, user: User) -> bool:
    """Отправляет уведомление и записывает timestamp. Returns True если ок."""
    async with session_maker()() as session:
        # Считаем посты пользователя
        from sqlalchemy import func
        result = await session.execute(
            select(func.count(PostLog.id))
            .join(Channel, Channel.id == PostLog.target_id)
            .where(Channel.user_id == user.tg_user_id)
        )
        posts_count = result.scalar() or 0

    expires_str = user.tier_expires_at.strftime("%d %B %H:%M")
    text = NOTIFY_TEXT.format(expires_str=expires_str, posts_count=posts_count)

    try:
        await bot.send_message(user.tg_user_id, text)
    except Exception as exc:
        logger.warning("Failed to notify user %s: %s", user.tg_user_id, exc)
        return False

    # Помечаем отправку
    async with session_maker()() as session:
        await session.execute(
            sa_update(User)
            .where(User.tg_user_id == user.tg_user_id)
            .values(trial_notify_sent_at=datetime.utcnow())
        )
        await session.commit()
    logger.info("Notified user %s about trial ending", user.tg_user_id)
    return True


async def run_expiry_check(bot: Bot) -> None:
    logger.info("=== Expiry check started ===")
    now = datetime.utcnow()

    # Блок 1: уведомление за 1 день до истечения (окно 12-36 часов)
    window_start = now + timedelta(hours=12)
    window_end = now + timedelta(hours=36)
    async with session_maker()() as session:
        result = await session.execute(
            select(User).where(
                and_(
                    User.tier == "free",
                    User.tier_expires_at != None,  # noqa: E711
                    User.tier_expires_at > window_start,
                    User.tier_expires_at <= window_end,
                    User.trial_notify_sent_at == None,  # noqa: E711
                    User.is_blocked == False,  # noqa: E712
                )
            )
        )
        to_notify = list(result.scalars().all())

    notified = 0
    for user in to_notify:
        ok = await _send_trial_notification(bot, user)
        if ok:
            notified += 1

    # Блок 2: истёкшие — обнуляем expires_at / даунгрейдим
    async with session_maker()() as session:
        result = await session.execute(
            select(User).where(
                and_(
                    User.tier_expires_at != None,  # noqa: E711
                    User.tier_expires_at < now,
                )
            )
        )
        expired = list(result.scalars().all())

    downgraded = 0
    trial_expired = 0
    for user in expired:
        async with session_maker()() as session:
            if user.tier == "free":
                await session.execute(
                    sa_update(User)
                    .where(User.tg_user_id == user.tg_user_id)
                    .values(tier_expires_at=None)
                )
                await session.commit()
                trial_expired += 1
                logger.info("User %s free trial expired", user.tg_user_id)
            else:
                await downgrade_to_free(session, user.tg_user_id)
                downgraded += 1
                logger.info("User %s downgraded to free (expired)", user.tg_user_id)

    logger.info(
        "=== Expiry check done. Notified: %d, Downgraded: %d, Trial expired: %d ===",
        notified, downgraded, trial_expired,
    )
