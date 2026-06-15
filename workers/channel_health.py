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

import html
import logging
from collections import Counter
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select, func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import (
    Channel,
    ChannelSource,
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

# Source-domination детектор: алерт, если один источник даёт большую долю
# отказов даже на канале, который технически постит (не молчит 12ч+).
SOURCE_DOMINATION_THRESHOLD = 0.60
SOURCE_DOMINATION_MIN_REJECTIONS = 10
SOURCE_DOMINATION_COOLDOWN_HOURS = 48

# Reason-коды для HealthNotification (поле reason)
REASON_SILENT = "high_rejection_rate"   # старый код, оставляем для совместимости
REASON_SOURCE_DOMINATION = "source_domination"


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
    session: AsyncSession,
    channel_id: int,
    reason: str | None = None,
    cooldown: timedelta | None = None,
) -> bool:
    """Был ли уже отправлен алерт за период cooldown.

    Если reason=None — старое поведение (любой алерт на канал, default cooldown
    NOTIFICATION_COOLDOWN_DAYS). Если задан — фильтруем по reason и cooldown
    раздельно для каждого типа алерта.
    """
    if cooldown is None:
        cooldown = timedelta(days=NOTIFICATION_COOLDOWN_DAYS)
    cutoff = datetime.utcnow() - cooldown
    query = select(HealthNotification.id).where(
        HealthNotification.channel_id == channel_id,
        HealthNotification.sent_at > cutoff,
    )
    if reason is not None:
        query = query.where(HealthNotification.reason == reason)
    result = await session.execute(query.limit(1))
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
    # Типично для "тяжёлых" ниш (крипто, развлечения): даже на мягком фильтре
    # LLM на этапе rewrite режет хайповый контент. Помогает только unfiltered.
    digest_fails = sum(1 for r in rejections if r.reason == "digest_failed")
    heavy_niche = (channel.niche or "").lower() in ("crypto", "entertainment")
    not_unfiltered = (channel.filter_preset or "") != "unfiltered"

    if total > 0 and not_unfiltered and (digest_fails / total >= 0.5 or heavy_niche):
        # LLM душит контент на этапе rewrite — обычный фильтр не помогает,
        # спасает только unfiltered-режим (raw, без фильтра ценности).
        advice_lines.append(
            f"🎯 Похоже, бот отсекает контент вашей темы как «рискованный» "
            f"(частое явление для крипто и развлечений). Смягчение обычного "
            f"фильтра не поможет — включите режим без фильтрации по ценности:\n"
            f"   <code>/setfilter {channel.id} unfiltered</code>"
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
        f"🔔 <b>Канал «{html.escape(channel.title or '')}» молчит</b>\n\n"
        f"Уже несколько часов не опубликовано ни одного поста, "
        f"а бот отклонил <b>{total}</b> твитов — контент не прошёл обработку.\n\n"
        f"<b>Источники по числу отклонений:</b>\n{sources_str}\n\n"
        f"<b>Что можно сделать:</b>\n{advice_str}\n\n"
        f"<i>Это уведомление приходит не чаще раза в неделю.</i>"
    )


def _build_source_domination_message(
    channel: Channel,
    dominant_user: str,
    dominant_count: int,
    total: int,
) -> str:
    """Алерт о том, что один источник заваливает отбор кандидатов."""
    pct = round(100 * dominant_count / total) if total else 0
    return (
        f"⚠️ <b>Канал «{html.escape(channel.title or '')}» работает медленнее обычного</b>\n\n"
        f"Источник <b>@{html.escape(str(dominant_user))}</b> заваливает отбор: "
        f"<b>{dominant_count}</b> из <b>{total}</b> отказов за сутки "
        f"({pct}%) приходится на него. Бот теряет много времени, "
        f"отбраковывая эти твиты, и до публикации добирается мало "
        f"кандидатов.\n\n"
        f"<b>🎯 Решение:</b>\n"
        f"<code>/removesource {channel.id} @{html.escape(str(dominant_user))}</code>\n\n"
        f"Это удалит источник из канала. Бот сразу начнёт постить чаще, "
        f"потому что больше не будет пытаться публиковать твиты, "
        f"которые фильтр отклоняет.\n\n"
        f"<i>Это уведомление приходит не чаще раза в 2 дня.</i>"
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
        last_post = await _last_post_time(session, channel.id)
        now = datetime.utcnow()
        age = now - channel.created_at

        # Канал слишком молодой — даже 1 цикла не было, ждём
        if age < timedelta(minutes=45):
            return

        # Собираем отказы всегда — нужны и для silent, и для source-domination.
        # Период — min(age, 24ч), чтобы для молодых каналов не запрашивать пустоту.
        lookback = min(age, timedelta(hours=24))
        rejections = await _rejections_since(session, channel.id, lookback)

        is_silent = not (last_post and (now - last_post) < timedelta(hours=NO_POSTS_THRESHOLD_HOURS))

        # === ВЕТКА 1: SILENT (приоритет) ===
        if is_silent:
            # Адаптивный порог по возрасту канала
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

            if await _was_recently_notified(
                session, channel.id, reason=REASON_SILENT,
                cooldown=timedelta(days=NOTIFICATION_COOLDOWN_DAYS),
            ):
                logger.info(
                    "Channel %d silent: already notified recently, skipping",
                    channel.id,
                )
                return

            # SILENT-алерт: шлём уведомление в личку владельцу.
            # Кнопка скаута: алерт перестаёт быть «у тебя проблема»
            # и становится «нажми — я починю».
            message_text = _build_diagnostic_message(channel, rejections)
            scout_kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text="🔍 Подобрать новые источники",
                    callback_data=f"scoutrun:{channel.id}",
                )
            ]])
            try:
                await bot.send_message(
                    chat_id=channel.user_id,
                    text=message_text,
                    disable_web_page_preview=True,
                    reply_markup=scout_kb,
                )
                session.add(HealthNotification(
                    channel_id=channel.id,
                    user_id=channel.user_id,
                    reason=REASON_SILENT,
                ))
                await session.commit()
                logger.info(
                    "Sent silent alert to user %d for channel %d "
                    "(%d rejections in 24h)",
                    channel.user_id, channel.id, len(rejections),
                )
            except TelegramForbiddenError:
                logger.info("User %d blocked the bot, can't send silent alert", channel.user_id)
            except Exception:
                logger.exception(
                    "Failed to send silent alert to user %d", channel.user_id
                )
            return

        # === ВЕТКА 2: SOURCE-DOMINATION ===
        # Канал не молчит (постит хотя бы раз в 12ч), но смотрим — не заваливает ли
        # один источник большую долю отказов.
        total = len(rejections)
        if total < SOURCE_DOMINATION_MIN_REJECTIONS:
            logger.info(
                "Channel %d healthy: last post %s ago, only %d rejections",
                channel.id, now - last_post if last_post else "n/a", total,
            )
            return

        source_stats = Counter(r.twitter_username for r in rejections)
        if not source_stats:
            logger.info(
                "Channel %d healthy: last post %s ago, no source stats",
                channel.id, now - last_post if last_post else "n/a",
            )
            return

        dominant_user, dominant_count = source_stats.most_common(1)[0]
        share = dominant_count / total

        if share < SOURCE_DOMINATION_THRESHOLD:
            logger.info(
                "Channel %d healthy: last post %s ago, top source @%s has %.0f%% (need %.0f%%)",
                channel.id, now - last_post if last_post else "n/a",
                dominant_user, share * 100, SOURCE_DOMINATION_THRESHOLD * 100,
            )
            return

        # Если у доминирующего источника задан ПЕРСОНАЛЬНЫЙ порог интереса —
        # высокая доля отказов намеренная (владелец сам поставил жёсткий порог
        # "смешанному" источнику, его оффтоп режется by design). Советовать
        # удаление тут вредно — не шлём алерт.
        src_floor_row = await session.execute(
            select(ChannelSource.min_interest).where(
                ChannelSource.channel_id == channel.id,
                ChannelSource.twitter_username == dominant_user,
                ChannelSource.min_interest != None,  # noqa: E711
            )
        )
        if src_floor_row.scalar_one_or_none() is not None:
            logger.info(
                "Channel %d: @%s dominates rejections (%.0f%%) but has a personal "
                "interest floor — intentional, not alerting",
                channel.id, dominant_user, share * 100,
            )
            return

        if await _was_recently_notified(
            session, channel.id, reason=REASON_SOURCE_DOMINATION,
            cooldown=timedelta(hours=SOURCE_DOMINATION_COOLDOWN_HOURS),
        ):
            logger.info(
                "Channel %d source-domination by @%s, but already notified recently",
                channel.id, dominant_user,
            )
            return

        # SOURCE-DOMINATION-алерт
        message_text = _build_source_domination_message(
            channel, dominant_user, dominant_count, total,
        )
        try:
            await bot.send_message(
                chat_id=channel.user_id,
                text=message_text,
                disable_web_page_preview=True,
            )
            session.add(HealthNotification(
                channel_id=channel.id,
                user_id=channel.user_id,
                reason=REASON_SOURCE_DOMINATION,
            ))
            await session.commit()
            logger.info(
                "Sent source-domination alert to user %d for channel %d "
                "(@%s: %d of %d rejections, %.0f%%)",
                channel.user_id, channel.id, dominant_user,
                dominant_count, total, share * 100,
            )
        except TelegramForbiddenError:
            logger.info("User %d blocked the bot, can't send source-domination alert", channel.user_id)
        except Exception:
            logger.exception(
                "Failed to send source-domination alert to user %d", channel.user_id
            )
