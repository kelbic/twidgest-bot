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
NO_POSTS_THRESHOLD_HOURS = 12
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
    total = len(rejections)
    source_stats = Counter(r.twitter_username for r in rejections)
    top_sources = source_stats.most_common(5)

    sources_str = "\n".join(
        f"  • @{u}: {n} отклонений"
        for u, n in top_sources
    )

    advice_lines = []

    # Доля отказов именно из-за дайджеста (LLM вернул пусто/SKIP).
    # Это типично для "тяжёлых" ниш (крипто, развлечения), где строгий
    # фильтр душит контент — виноваты не источники, а пресет фильтра.
    digest_fails = sum(1 for r in rejections if r.reason == "digest_failed")
    heavy_niche = (channel.niche or "").lower() in ("crypto", "entertainment")
    strict_filter = (channel.filter_preset or "") in ("community", "strict")

    if total > 0 and (digest_fails / total >= 0.5 or (heavy_niche and strict_filter)):
        # Причина похожа на слишком строгий фильтр, а не на плохие источники
        advice_lines.append(
            f"🎯 Похоже, причина — <b>строгий фильтр контента</b> для вашей темы. "
            f"Для ниш вроде крипто и развлечений стандартный фильтр часто отсекает "
            f"посты, которые считает рискованными.\n"
            f"   Решение: смягчите фильтр командой "
            f"<code>/setfilter {channel.id} loose</code>"
        )
    elif top_sources:
        # Иначе — если один источник доминирует, называем виновника
        top_user, top_count = top_sources[0]
        if total > 0 and top_count / total >= 0.6:
            advice_lines.append(
                f"🎯 Скорее всего, причина — источник <b>@{top_user}</b> "
                f"({top_count} из {total} отклонений). Так бывает, когда аккаунт "
                f"постит видео, картинки или короткие подписи, которые бот не может "
                f"оформить в текстовый пост.\n"
                f"   Решение: <code>/removesource {channel.id} @{top_user}</code>"
            )

    advice_lines.append(
        f"• Добавьте больше <b>текстовых</b> новостных источников: "
        f"<code>/addsource {channel.id} @username</code>"
    )
    advice_lines.append(
        f"• Посмотреть текущие источники: <code>/sources {channel.id}</code>"
    )
    advice_lines.append(
        f"• Сменить тему через готовый шаблон: <code>/templates</code>"
    )
    advice_str = "\n".join(advice_lines)

    return (
        f"🔔 <b>Канал «{channel.title}» молчит</b>\n\n"
        f"Уже несколько часов не опубликовано ни одного поста, "
        f"а бот отклонил <b>{total}</b> твитов — контент не прошёл обработку.\n\n"
        f"<b>Источники по числу отклонений:</b>\n{sources_str}\n\n"
        f"<b>Что можно сделать:</b>\n{advice_str}\n\n"
        f"<i>Это уведомление приходит не чаще раза в неделю.</i>"
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

        # Если последний пост был недавно — всё ок
        if last_post and (now - last_post) < timedelta(hours=NO_POSTS_THRESHOLD_HOURS):
            logger.info(
                "Channel %d healthy: last post %s ago",
                channel.id, now - last_post,
            )
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
