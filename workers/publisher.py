"""Воркер, публикующий дайджесты для каналов.

Запускается раз в час. Для каждого Channel в digest-режиме проверяет интервал
и публикует, если пора.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.llm_client import DigestTweet, OpenRouterClient
from core.plan import channel_active, digest_floor, posts_cap
from core import metrics
from db.repositories.channel_costs import save_channel_cost
from core.safe_sender import ChannelTarget, send_to_target
from core.topic_dedup import dedup_within
from db.models import ChannelSource, Channel, User
from db.repositories.tweets import (
    clear_digest_items,
    get_digest_queue,
    last_digest_time,
    log_digest,
    log_post,
    posts_today_channel,
)
from db.session import session_maker
from prompts import build_digest_prompt, build_unfiltered_digest_prompt

logger = logging.getLogger(__name__)


async def _get_digest_channels(session: AsyncSession) -> list[Channel]:
    result = await session.execute(
        select(Channel)
        .join(User, Channel.user_id == User.tg_user_id)
        .where(
            Channel.is_active == True,  # noqa: E712
            Channel.mode.in_(["digest", "hybrid"]),
            Channel.target_chat_id != None,  # noqa: E711
            User.is_blocked == False,  # noqa: E712
        )
        .options(selectinload(Channel.user))
    )
    return list(result.scalars().all())


async def run_publish_cycle(
    bot: Bot, llm_default: OpenRouterClient, llm_pro: OpenRouterClient
) -> None:
    logger.info("=== Publisher cycle started ===")

    async with session_maker()() as session:
        channels = await _get_digest_channels(session)

    for channel in channels:
        acc = metrics.channel_begin()
        try:
            await _process_channel(channel, bot, llm_default, llm_pro)
        except Exception:
            logger.exception("Failed to publish for channel %d", channel.id)
        finally:
            metrics.channel_end()
            try:
                await save_channel_cost(channel.id, acc)
            except Exception as exc:
                logger.warning("channel cost save failed: %s", exc)

    logger.info("=== Publisher cycle done ===")


async def _process_channel(
    channel: Channel,
    bot: Bot,
    llm_default: OpenRouterClient,
    llm_pro: OpenRouterClient,
) -> None:
    user = channel.user
    if not channel_active(channel):
        logger.info(
            "publisher: skipping channel %d — inactive (no paid/trial)",
            channel.id,
        )
        return
    # Для дайджестов всегда используем Pro-модель (Sonnet) — редкий вызов,
    # качество важнее экономии. Single-режим остается на default LLM.
    llm = llm_pro

    interval_h = max(channel.digest_interval_hours, digest_floor(channel))

    async with session_maker()() as session:
        logger.info(
            "publisher: processing channel %d (%s, mode=%s, niche=%s)",
            channel.id, channel.title, channel.mode, channel.niche,
        )
        # Когда последний раз публиковали для этого канала
        last = await last_digest_time(session, user.tg_user_id, channel.id)
        now = datetime.utcnow()
        logger.info(
            "publisher: channel %d last_digest=%s, now=%s, interval_h=%d",
            channel.id, last, now, interval_h,
        )
        # Если уже был дайджест — соблюдаем интервал
        # Если это первый дайджест канала — публикуем при первой возможности
        if last is not None and (now - last) < timedelta(hours=interval_h):
            return
        if last is None:
            logger.info(
                "Channel %d: first digest — bypassing interval",
                channel.id,
            )

        today = await posts_today_channel(session, channel.id)
        if today >= posts_cap(channel):
            logger.info("Channel %d hit daily quota for digest.", channel.id)
            return

        # Очередь твитов этого юзера
        # Источниковые пороги канала — СИММЕТРИЧНО viral_picker, чтобы дайджест
        # и single фильтровали тему одинаково (иначе дайджест публикует оффтоп,
        # который single режет).
        sf_rows = await session.execute(
            select(ChannelSource.twitter_username, ChannelSource.min_interest)
            .where(ChannelSource.channel_id == channel.id,
                   ChannelSource.min_interest != None)  # noqa: E711
        )
        source_floors = {(u or "").lower(): mi for u, mi in sf_rows.all()}
        queue = await get_digest_queue(
            session,
            user.tg_user_id,
            channel_id=channel.id,
            max_items=channel.digest_max_tweets,
            min_interest=channel.min_interest or 0,
            source_floors=source_floors,
        )
        logger.info(
            "publisher: channel %d queue size=%d, max_tweets=%d",
            channel.id, len(queue), channel.digest_max_tweets,
        )
        if len(queue) < 2:
            logger.info(
                "Channel %d digest queue: %d < 2. Skipping.",
                channel.id, len(queue),
            )
            return

        # Внутри-дайджестный дедуп: режем явные близнецы (три запуска Starlink за
        # день → один). Очередь чистится целиком ниже, близнецы не зависнут.
        keep = set(dedup_within([t.text for t in queue]))
        deduped = [t for i, t in enumerate(queue) if i in keep]
        if len(deduped) < len(queue):
            logger.info(
                "publisher: channel %d digest dedup %d -> %d (срезаны близнецы)",
                channel.id, len(queue), len(deduped),
            )
        if len(deduped) < 2:
            logger.info(
                "Channel %d: после дедупа осталось %d < 2, пропуск дайджеста",
                channel.id, len(deduped),
            )
            return
        digest_tweets = [
            DigestTweet(
                username=t.twitter_username,
                text=t.text,
                url=t.url,
                likes=t.likes,
                retweets=t.retweets,
            )
            for t in deduped
        ]

        # Выбираем промпт в зависимости от фильтра
        if channel.filter_preset == 'unfiltered':
            system_prompt = build_unfiltered_digest_prompt(
                channel.niche, legal_rf=channel.legal_rf_filter)
        else:
            system_prompt = build_digest_prompt(
                channel.niche, legal_rf=channel.legal_rf_filter)
        digest_text = await _build_digest_with_prompt(llm, digest_tweets, system_prompt)
        # LLM может вернуть пусто ИЛИ сентинел "SKIP" (не смог собрать дайджест —
        # частый случай для крипто/новостных ниш, где фильтр душит контент).
        # safe_sender такой текст всё равно заблокирует, поэтому отсекаем здесь
        # и логируем провал, иначе last_digest=None и канал зацикливается.
        _dt = (digest_text or "").strip()
        if not _dt or _dt.upper().startswith("SKIP"):
            logger.warning(
                "LLM failed/SKIP digest for channel %d (niche=%s)",
                channel.id, channel.niche,
            )
            from db.models import RejectionLog
            for t in queue:
                session.add(RejectionLog(
                    channel_id=channel.id,
                    tweet_id=t.tweet_id,
                    twitter_username=t.twitter_username,
                    reason="digest_failed",
                ))
            await session.commit()
            return

        fake_target = ChannelTarget(
            channel_id=channel.id,
            chat_id=channel.target_chat_id,
        )

        ok = await send_to_target(bot, session, fake_target, digest_text)
        if not ok:
            return

        used_ids = [t.id for t in queue]
        await clear_digest_items(session, used_ids)
        await log_digest(session, user.tg_user_id, channel.id, len(used_ids))
        await log_post(session, user.tg_user_id, channel.id, is_digest=True)
        logger.info(
            "Published digest for channel=%d (%d tweets)",
            channel.id, len(used_ids),
        )


async def _build_digest_with_prompt(
    llm: OpenRouterClient,
    tweets: list[DigestTweet],
    system_prompt: str,
) -> str | None:
    """Дайджест с произвольным system_prompt."""
    if not tweets:
        return None

    blocks: list[str] = []
    for i, tw in enumerate(tweets, start=1):
        blocks.append(
            f"[Твит #{i}]\n"
            f"Автор: @{tw.username}\n"
            f"URL: {tw.url}\n"
            f"Лайки: {tw.likes}, Ретвиты: {tw.retweets}\n"
            f"Текст: {tw.text}"
        )
    user_prompt = (
        f"Вот {len(tweets)} твитов за последний период. "
        "Составь дайджест по формату из системного промпта. "
        "Выбирай лучшие 3–5 пунктов.\n\n"
        + "\n\n---\n\n".join(blocks)
    )
    return await llm._call_with_retry(system_prompt, user_prompt, max_tokens=1500)
