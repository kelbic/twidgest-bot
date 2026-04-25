"""Воркер мониторинга здоровья каналов.

Раз в 3 часа проверяет каждый активный канал. Если канал:
- Привязан (target_chat_id есть)
- Активен (is_active=True)
- За 24 часа НЕ опубликовал ни одного поста
- При этом за 24 часа было >= 5 отказов LLM

→ значит фильтр отсекает большинство контента. Уведомляем владельца канала
   в личку с диагностикой и советами.

Защита от спама: одно уведомление на канал не чаще раза в 7 дней.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from sqlalchemy import select, func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import (
    Channel,
    HealthNotification,
    PostLog,
    RejectionLog,
    User,
)
from db.session import session_maker

logger = logging.getLogger(__name__)

# Константы воркера
NO_POSTS_THRESHOLD_HOURS = 24
MIN_REJECTIONS_FOR_ALERT = 5
NOTIFICATION_COOLDOWN_DAYS = 7


async def _get_active_channels(session: AsyncSession) -> list[Channel]:
    result = await session.execute(
        select(Channel)
        .join(User, Channel.user_id == User.tg_user_id)
        .where(
            Channel.is_active == True,  # noqa: E712
            Channel.target_chat_id != None,  # noqa: E711
            User.is_blocked == False,  # noqa: E712
        )
        .options(selectinload(Channel.user), selectinload(Channel.channel_sources))
    )
    return list(result.scalars().all())


async def _last_post_time(session: AsyncSession, channel_id: int) -> datetime | None:
    result = await session.execute(
        select(sa_func.max(PostLog.posted_at))
        .where(PostLog.target_id == channel_id)
    )
    return result.scalar_one_or_none()


async def _rejections_last_24h(
    session: AsyncSession, channel_id: int
) -> list[RejectionLog]:
    cutoff = datetime.utcnow() - timedelta(hours=NO_POSTS_THRESHOLD_HOURS)
    result = await session.execute(
        select(RejectionLog)
        .where(
            RejectionLog.channel_id == channel_id,
            RejectionLog.rejected_at > cutoff,
        )
    )
    return list(result.scalars().all())


async def _rejections_since(
    session: AsyncSession, channel_id: int, since: timedelta
) -> list[RejectionLog]:
    """Возвращает все отказы за указанный период (с момента now-since)."""
    cutoff = datetime.utcnow() - since
    result = await session.execute(
        select(RejectionLog)
        .where(
            RejectionLog.channel_id == channel_id,
            RejectionLog.rejected_at > cutoff,
        )
    )
    return list(result.scalars().all())


async def _was_recently_notified(
    session: AsyncSession, channel_id: int
) -> bool:
    cutoff = datetime.utcnow() - timedelta(days=NOTIFICATION_COOLDOWN_DAYS)
    result = await session.execute(
        select(HealthNotification.id)
        .where(
            HealthNotification.channel_id == channel_id,
            HealthNotification.sent_at > cutoff,
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


def _build_diagnostic_message(
    channel: Channel, rejections: list[RejectionLog]
) -> str:
    """Формирует понятное сообщение юзеру о проблеме с каналом."""
    # Статистика по источникам — кто чаще всего отказывается
    source_stats = Counter(r.twitter_username for r in rejections)
    top_sources = source_stats.most_common(5)

    sources_str = "\n".join(
        f"  • @{u}: {n} отказов"
        for u, n in top_sources
    )

    return (
        f"🔔 <b>Проблема с каналом «{channel.title}»</b>\n\n"
        f"За последние 24 часа в твой канал не было опубликовано ни одного поста, "
        f"при этом фильтр контента отсёк <b>{len(rejections)}</b> твитов.\n\n"
        f"<b>Топ источников по отказам:</b>\n{sources_str}\n\n"
        f"<b>Возможные причины:</b>\n"
        f"• Тема канала содержит политически чувствительный контент (РФ-фильтр)\n"
        f"• Источники постят преимущественно медицинский контент с дозировками\n"
        f"• Источники постят рекламу/анонсы вместо новостей\n"
        f"• Тема канала слишком узкая, твиты не соответствуют ожиданиям\n\n"
        f"<b>Что можно сделать:</b>\n"
        f"1. Удалить канал и пересоздать с другой темой:\n"
        f"   /deletechannel {channel.id}\n"
        f"   /createchannel ai &lt;новая тема&gt;\n"
        f"2. Использовать готовый шаблон вместо AI: /templates\n"
        f"3. Если уверен в теме — оставить как есть, бот продолжит проверять\n\n"
        f"<i>Это уведомление приходит раз в неделю на канал.</i>"
    )


async def run_channel_health_cycle(bot: Bot) -> None:
    logger.info("=== Channel health cycle started ===")

    async with session_maker()() as session:
        channels = await _get_active_channels(session)

    if not channels:
        logger.info("No active channels for health check.")
        return

    logger.info("Checking health of %d channels", len(channels))

    notifications_sent = 0

    for channel in channels:
        try:
            await _check_channel(channel, bot)
        except Exception:
            logger.exception("Health check failed for channel %d", channel.id)

    logger.info("=== Channel health cycle done ===")


async def _check_channel(channel: Channel, bot: Bot) -> None:
    async with session_maker()() as session:
        # Проверяем последний пост
        last_post = await _last_post_time(session, channel.id)
        now = datetime.utcnow()

        age = now - channel.created_at

        # Канал слишком молодой — даже 1 цикла не было, ждём
        if age < timedelta(minutes=45):
            return

        # Если последний пост был меньше 24ч назад — всё ок (для старых)
        # Или канал молодой и ещё не успел опубликовать — всё ок (для молодых)
        if last_post and (now - last_post) < timedelta(hours=NO_POSTS_THRESHOLD_HOURS):
            return

        # Канал молчит. Смотрим количество отказов LLM
        rejections = await _rejections_since(session, channel.id, age)

        # Адаптивный порог в зависимости от возраста канала
        # Молодой канал (1-3ч) — порог ниже (3 отказа)
        # Старый канал (24ч+) — порог выше (5 отказов), нужно больше уверенности
        if age < timedelta(hours=3):
            min_rejections = 3
        elif age < timedelta(hours=NO_POSTS_THRESHOLD_HOURS):
            min_rejections = 5
        else:
            min_rejections = MIN_REJECTIONS_FOR_ALERT

        if len(rejections) < min_rejections:
            logger.info(
                "Channel %d silent (age %s) but only %d rejections (need %d) — not alerting",
                channel.id, age, len(rejections), min_rejections,
            )
            return

        # Проверяем что недавно не уведомляли
        if await _was_recently_notified(session, channel.id):
            logger.info(
                "Channel %d already notified recently, skipping",
                channel.id,
            )
            return

        # Шлём уведомление в личку владельцу
        message_text = _build_diagnostic_message(channel, rejections)
        try:
            await bot.send_message(
                chat_id=channel.user_id,
                text=message_text,
                disable_web_page_preview=True,
            )
            # Записываем что уведомили
            session.add(HealthNotification(
                channel_id=channel.id,
                user_id=channel.user_id,
                reason="high_rejection_rate",
            ))
            await session.commit()
            logger.info(
                "Sent health alert to user %d for channel %d "
                "(%d rejections in 24h)",
                channel.user_id, channel.id, len(rejections),
            )
        except TelegramForbiddenError:
            # Юзер заблокировал бота — это нормально, не алармим
            logger.info("User %d blocked the bot, can't send health alert", channel.user_id)
        except Exception:
            logger.exception(
                "Failed to send health alert to user %d", channel.user_id
            )
