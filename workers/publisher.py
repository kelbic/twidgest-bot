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
from core.safe_sender import send_to_target
from db.models import Channel, User
from db.repositories.tweets import (
    clear_digest_items,
    get_digest_queue,
    last_digest_time,
    log_digest,
    log_post,
    posts_today,
)
from db.repositories.users import is_tier_active
from db.session import session_maker
from niches import build_digest_prompt
from tiers import get_limits

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
        try:
            await _process_channel(channel, bot, llm_default, llm_pro)
        except Exception:
            logger.exception("Failed to publish for channel %d", channel.id)

    logger.info("=== Publisher cycle done ===")


async def _process_channel(
    channel: Channel,
    bot: Bot,
    llm_default: OpenRouterClient,
    llm_pro: OpenRouterClient,
) -> None:
    user = channel.user
    async with session_maker()() as session:
        active = await is_tier_active(user)
    effective_tier = user.tier if active else "free"
    limits = get_limits(effective_tier)
    # Для дайджестов всегда используем Pro-модель (Sonnet) — редкий вызов,
    # качество важнее экономии. Single-режим остается на default LLM.
    llm = llm_pro

    interval_h = max(channel.digest_interval_hours, limits.digest_min_interval_hours)

    async with session_maker()() as session:
        # Когда последний раз публиковали для этого канала
        last = await last_digest_time(session, user.tg_user_id, channel.id)
        now = datetime.utcnow()
        if last is not None and (now - last) < timedelta(hours=interval_h):
            return

        today = await posts_today(session, user.tg_user_id)
        if today >= limits.max_posts_per_day:
            logger.info("User %s hit daily quota for digest.", user.tg_user_id)
            return

        # Очередь твитов этого юзера
        queue = await get_digest_queue(
            session,
            user.tg_user_id,
            channel_id=channel.id,
            max_items=channel.digest_max_tweets,
        )
        if len(queue) < 2:
            logger.info(
                "Channel %d digest queue: %d < 2. Skipping.",
                channel.id, len(queue),
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
            for t in queue
        ]

        # Используем niche-промпт для этого канала
        system_prompt = build_digest_prompt(channel.niche)
        digest_text = await _build_digest_with_prompt(llm, digest_tweets, system_prompt)
        if not digest_text:
            logger.warning("LLM failed digest for channel %d", channel.id)
            return

        # Fake target для send_to_target
        fake_target = type("FakeTarget", (), {
            "id": channel.id,
            "chat_id": channel.target_chat_id,
            "is_active": True,
        })()

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
