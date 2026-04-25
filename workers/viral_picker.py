"""Hybrid mode worker: каждый час берёт топ из очереди hybrid-канала
и постит его отдельно (single-style) — для живой ленты.

Логика:
- Берём топ-5 кандидатов из очереди по engagement
- Идём по списку, для каждого пробуем сделать LLM-перевод
- Первый успешный публикуем как single-пост
- SKIP-твиты не удаляем — они могут попасть в дайджест (там промпт мягче)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from aiogram import Bot
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.llm_client import OpenRouterClient
from core.safe_sender import send_to_target
from db.models import Channel, DigestQueueItem, PostLog, User
from db.repositories.tweets import clear_digest_items, log_post, posts_today
from db.repositories.users import is_tier_active
from db.session import session_maker
from niches import build_single_prompt
from tiers import get_limits

logger = logging.getLogger(__name__)

# Сколько топ-кандидатов перебираем за один цикл
TOP_CANDIDATES_TO_TRY = 5
# Минимальное engagement, чтобы вообще рассматривать твит как single
MIN_LIKES_FOR_SINGLE = 100
# Pacing — минимальный интервал между single-постами в одном канале
SINGLE_POST_PACING_MINUTES = 30  # для теста, в проде поднять


async def _get_hybrid_channels(session: AsyncSession) -> list[Channel]:
    result = await session.execute(
        select(Channel)
        .join(User, Channel.user_id == User.tg_user_id)
        .where(
            Channel.is_active == True,  # noqa: E712
            Channel.mode == "hybrid",
            Channel.target_chat_id != None,  # noqa: E711
            User.is_blocked == False,  # noqa: E712
        )
        .options(selectinload(Channel.user))
    )
    return list(result.scalars().all())


async def _last_single_post_time(
    session: AsyncSession, channel_id: int
) -> datetime | None:
    result = await session.execute(
        select(PostLog.posted_at)
        .where(
            and_(
                PostLog.target_id == channel_id,
                PostLog.is_digest == False,  # noqa: E712
            )
        )
        .order_by(PostLog.posted_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _get_top_queue_items(
    session: AsyncSession, channel_id: int, limit: int = 5
) -> list[DigestQueueItem]:
    """Топ-N твитов из очереди по engagement."""
    result = await session.execute(
        select(DigestQueueItem)
        .where(DigestQueueItem.channel_id == channel_id)
        .order_by((DigestQueueItem.likes + DigestQueueItem.retweets * 3).desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def run_viral_picker_cycle(
    bot: Bot, llm_default: OpenRouterClient
) -> None:
    logger.info("=== Viral picker cycle started ===")

    async with session_maker()() as session:
        channels = await _get_hybrid_channels(session)

    if not channels:
        logger.info("No hybrid channels found.")
        return

    logger.info(
        "viral_picker: found %d hybrid channels: %s",
        len(channels),
        [(c.id, c.title) for c in channels],
    )

    for channel in channels:
        try:
            await _process_hybrid_channel(channel, bot, llm_default)
        except Exception:
            logger.exception("viral_picker failed for channel %d", channel.id)

    logger.info("=== Viral picker cycle done ===")


async def _process_hybrid_channel(
    channel: Channel, bot: Bot, llm: OpenRouterClient
) -> None:
    user = channel.user
    async with session_maker()() as session:
        active = await is_tier_active(user)
    effective_tier = user.tier if active else "free"
    limits = get_limits(effective_tier)

    async with session_maker()() as session:
        logger.info(
            "viral_picker: processing channel %d (%s)", channel.id, channel.title
        )

        # Pacing
        last_single = await _last_single_post_time(session, channel.id)
        now = datetime.utcnow()
        if last_single and (now - last_single) < timedelta(
            minutes=SINGLE_POST_PACING_MINUTES
        ):
            logger.info(
                "Channel %d: last single was %s ago, skipping pacing",
                channel.id, now - last_single,
            )
            return

        # Daily quota
        today = await posts_today(session, user.tg_user_id)
        if today >= limits.max_posts_per_day:
            logger.info(
                "Channel %d: user hit daily quota %d/%d",
                channel.id, today, limits.max_posts_per_day,
            )
            return

        # Берём топ-N кандидатов
        candidates = await _get_top_queue_items(
            session, channel.id, limit=TOP_CANDIDATES_TO_TRY
        )
        if not candidates:
            logger.info("Channel %d: queue empty, no viral pick", channel.id)
            return

        niche_prompt = build_single_prompt(channel.niche)

        # Идём по топу, пробуем каждый
        for idx, top in enumerate(candidates, 1):
            if top.likes < MIN_LIKES_FOR_SINGLE:
                logger.info(
                    "Channel %d: candidate %d/%d has only %d likes, "
                    "and remaining are weaker — stopping",
                    channel.id, idx, len(candidates), top.likes,
                )
                return

            logger.info(
                "Channel %d: trying candidate %d/%d @%s likes=%d",
                channel.id, idx, len(candidates),
                top.twitter_username, top.likes,
            )

            rewritten = await _rewrite(llm, top, niche_prompt)
            if not rewritten:
                # SKIP — НЕ удаляем из очереди, может пойти в digest
                # Логируем отказ для health-диагностики
                from db.models import RejectionLog
                session.add(RejectionLog(
                    channel_id=channel.id,
                    tweet_id=top.tweet_id,
                    twitter_username=top.twitter_username,
                    reason="skip_viral",
                ))
                await session.commit()
                logger.info(
                    "Channel %d: candidate %d/%d got SKIP, trying next",
                    channel.id, idx, len(candidates),
                )
                continue

            # Создаём fake target
            fake_target = type("FakeTarget", (), {
                "id": channel.id,
                "chat_id": channel.target_chat_id,
                "is_active": True,
            })()

            ok = await send_to_target(bot, session, fake_target, rewritten)
            if ok:
                # Только успешно опубликованный твит удаляем из очереди
                await clear_digest_items(session, [top.id])
                await log_post(
                    session, user.tg_user_id, channel.id, is_digest=False
                )
                logger.info(
                    "Channel %d (hybrid): posted viral tweet @%s likes=%d",
                    channel.id, top.twitter_username, top.likes,
                )
                return  # один single за цикл
            else:
                # Не отправилось — попробуем следующий кандидат
                logger.warning(
                    "Channel %d: failed to send candidate %d, trying next",
                    channel.id, idx,
                )

        logger.info(
            "Channel %d: tried %d candidates, none published",
            channel.id, len(candidates),
        )


async def _rewrite(llm, item, system_prompt):
    user_prompt = (
        f"Автор: @{item.twitter_username}\n"
        f"URL: {item.url}\n\n"
        f"Текст твита:\n{item.text}"
    )
    result = await llm._call_with_retry(system_prompt, user_prompt, max_tokens=500)
    if not result:
        return None
    clean = result.strip()
    first_token = (
        clean.split()[0].upper().strip(".,;:!?<>[]")
        if clean.split()
        else ""
    )
    if first_token == "SKIP":
        return None
    if clean.upper().startswith("SKIP") and len(clean) < 80:
        return None
    meta_markers = (
        "я не вижу", "пожалуйста скопируйте", "i don't see",
        "i cannot", "i'm sorry", "as an ai",
    )
    if any(m in clean.lower() for m in meta_markers):
        return None
    return clean
