"""Воркер, собирающий новые твиты для всех активных каналов.

Работает через модель Channel (не через User.sources).
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.llm_client import OpenRouterClient
from core.safe_sender import send_to_target
from core.twitter_cache import TwitterCache
from core.twitter_client import Tweet
from db.models import Channel, User
from db.repositories.tweets import (
    enqueue_for_digest,
    is_processed,
    log_post,
    mark_processed,
    posts_today,
)
from db.repositories.users import is_tier_active
from db.session import session_maker
from niches import build_single_prompt
from tiers import get_limits

logger = logging.getLogger(__name__)


async def _get_active_channels(session: AsyncSession) -> list[Channel]:
    """Все активные каналы у не-заблокированных юзеров с привязанным target."""
    result = await session.execute(
        select(Channel)
        .join(User, Channel.user_id == User.tg_user_id)
        .where(
            Channel.is_active == True,  # noqa: E712
            Channel.target_chat_id != None,  # noqa: E711
            User.is_blocked == False,  # noqa: E712
        )
        .options(
            selectinload(Channel.channel_sources),
            selectinload(Channel.user),
        )
    )
    return list(result.scalars().all())


async def run_collect_cycle(
    bot: Bot,
    cache: TwitterCache,
    llm_default: OpenRouterClient,
    llm_pro: OpenRouterClient,
) -> None:
    logger.info("=== Collector cycle started ===")

    async with session_maker()() as session:
        channels = await _get_active_channels(session)

    if not channels:
        logger.info("No active channels with bound target.")
        return

    unique_sources: set[str] = set()
    for ch in channels:
        for src in ch.channel_sources:
            if src.is_active:
                unique_sources.add(src.twitter_username.lower())

    logger.info(
        "Active channels: %d. Unique sources to fetch: %d",
        len(channels), len(unique_sources),
    )

    fetch_results: dict[str, list[Tweet]] = {}
    for username in unique_sources:
        try:
            fetch_results[username] = await cache.get_tweets(username, limit=20)
        except Exception:
            logger.exception("Cache fetch failed for @%s", username)
            fetch_results[username] = []

    for channel in channels:
        try:
            await _process_channel(channel, fetch_results, bot, llm_default, llm_pro)
        except Exception:
            logger.exception("Failed to process channel %d", channel.id)

    logger.info("=== Collector cycle done ===")


async def _process_channel(
    channel: Channel,
    fetch_results: dict[str, list[Tweet]],
    bot: Bot,
    llm_default: OpenRouterClient,
    llm_pro: OpenRouterClient,
) -> None:
    if not channel.channel_sources:
        return

    user = channel.user
    async with session_maker()() as session:
        active = await is_tier_active(user)
    effective_tier = user.tier if active else "free"
    limits = get_limits(effective_tier)
    llm = llm_pro if limits.use_pro_llm else llm_default

    niche_prompt = build_single_prompt(channel.niche)

    fake_target = type("FakeTarget", (), {
        "id": channel.id,
        "chat_id": channel.target_chat_id,
        "is_active": True,
    })()

    for src in channel.channel_sources:
        if not src.is_active:
            continue
        tweets = fetch_results.get(src.twitter_username.lower(), [])
        if not tweets:
            continue

        filtered: list[Tweet] = []
        for tw in tweets:
            if channel.skip_replies and tw.is_reply:
                continue
            if tw.likes < channel.min_likes:
                continue
            if tw.retweets < channel.min_retweets:
                continue
            filtered.append(tw)

        if not filtered:
            continue

        async with session_maker()() as session:
            for tw in filtered:
                if not tw.text or len(tw.text.strip()) < 20:
                    await mark_processed(session, user.tg_user_id, tw.id, tw.username)
                    continue

                if await is_processed(session, user.tg_user_id, tw.id):
                    continue

                if channel.mode == "digest":
                    await enqueue_for_digest(
                        session=session,
                        user_id=user.tg_user_id,
                        tweet_id=tw.id,
                        twitter_username=tw.username,
                        text=tw.text,
                        url=tw.url,
                        likes=tw.likes,
                        retweets=tw.retweets,
                    )
                    await mark_processed(session, user.tg_user_id, tw.id, tw.username)
                    continue

                # SINGLE mode
                today = await posts_today(session, user.tg_user_id)
                if today >= limits.max_posts_per_day:
                    logger.info(
                        "User %s hit daily quota (%d/%d). Stopping.",
                        user.tg_user_id, today, limits.max_posts_per_day,
                    )
                    return

                rewritten = await _rewrite_with_niche(llm, tw, niche_prompt)
                if not rewritten:
                    await mark_processed(session, user.tg_user_id, tw.id, tw.username)
                    logger.info("Skipped tweet %s (LLM SKIP)", tw.id)
                    continue

                ok = await send_to_target(bot, session, fake_target, rewritten)
                if ok:
                    await mark_processed(session, user.tg_user_id, tw.id, tw.username)
                    await log_post(session, user.tg_user_id, channel.id, is_digest=False)
                    logger.info(
                        "Posted tweet %s to channel=%d (chat=%d)",
                        tw.id, channel.id, channel.target_chat_id,
                    )
                    await asyncio.sleep(3)
                break  # один пост за итерацию канала


async def _rewrite_with_niche(
    llm: OpenRouterClient, tweet: Tweet, system_prompt: str
) -> str | None:
    """rewrite с произвольным system_prompt вместо встроенного."""
    user_prompt = (
        f"Автор: @{tweet.username}\n"
        f"URL: {tweet.url}\n\n"
        f"Текст твита:\n{tweet.text}"
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
        "я не вижу", "пожалуйста скопируйте", "пожалуйста, скопируйте",
        "напишите текст", "не вижу текст", "i don't see", "please provide",
        "i cannot", "i'm sorry", "as an ai", "как ии",
    )
    if any(m in clean.lower() for m in meta_markers):
        return None
    return clean
