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
from config import Config
from core.image_picker import fetch_image_url_for_keywords
from core.safe_sender import send_to_target
from core.topic_dedup import compute_topic_signature, is_duplicate_topic
from core.twitter_cache import TwitterCache
from core.twitter_client import Tweet
from core.vk_client import VKClient, VKPost
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
from prompts import build_single_prompt, build_vk_prompt
from tiers import get_limits

logger = logging.getLogger(__name__)

_cfg_unsplash = Config()


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
    vk_client: VKClient | None = None,
) -> None:
    logger.info("=== Collector cycle started ===")

    async with session_maker()() as session:
        channels = await _get_active_channels(session)

    if not channels:
        logger.info("No active channels with bound target.")
        return

    unique_twitter: set[str] = set()
    unique_vk: set[str] = set()
    for ch in channels:
        for src in ch.channel_sources:
            if src.is_active:
                if src.source_type == 'vk':
                    unique_vk.add(src.twitter_username.lower())
                else:
                    unique_twitter.add(src.twitter_username.lower())

    logger.info(
        "Active channels: %d. Twitter sources: %d, VK sources: %d",
        len(channels), len(unique_twitter), len(unique_vk),
    )

    fetch_results: dict[str, list[Tweet]] = {}
    for username in unique_twitter:
        try:
            fetch_results[username] = await cache.get_tweets(username, limit=20)
        except Exception:
            logger.exception("Cache fetch failed for @%s", username)
            fetch_results[username] = []

    vk_results: dict[str, list[VKPost]] = {}
    if vk_client and unique_vk:
        for identifier in unique_vk:
            try:
                vk_results[identifier] = await vk_client.get_community_posts(
                    identifier, count=20
                )
            except Exception:
                logger.exception("VK fetch failed for %s", identifier)
                vk_results[identifier] = []

    for channel in channels:
        try:
            await _process_channel(
                channel, fetch_results, vk_results, bot, llm_default, llm_pro
            )
        except Exception:
            logger.exception("Failed to process channel %d", channel.id)

    logger.info("=== Collector cycle done ===")


async def _process_channel(
    channel: Channel,
    fetch_results: dict[str, list[Tweet]],
    vk_results: dict[str, list[VKPost]],
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

    niche_prompt = build_single_prompt(channel.niche, channel.filter_preset)
    vk_niche_prompt = build_vk_prompt(channel.niche, channel.filter_preset)

    fake_target = type("FakeTarget", (), {
        "id": channel.id,
        "chat_id": channel.target_chat_id,
        "is_active": True,
    })()

    for src in channel.channel_sources:
        if not src.is_active:
            continue
        # VK источник
        if src.source_type == 'vk':
            await _process_vk_source(
                src, channel, user, limits, llm, vk_niche_prompt,
                vk_results, bot, fake_target,
            )
            continue
        # Twitter источник (default)
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

                # digest и hybrid — всегда в очередь.
                # Для hybrid отдельный воркер (viral_picker) каждый час берёт топ из очереди.
                # Для single — постим сразу (логика ниже).
                if channel.mode in ("digest", "hybrid"):
                    await enqueue_for_digest(
                        session=session,
                        user_id=user.tg_user_id,
                        channel_id=channel.id,
                        tweet_id=tw.id,
                        twitter_username=tw.username,
                        text=tw.text,
                        url=tw.url,
                        likes=tw.likes,
                        retweets=tw.retweets,
                        media_url=tw.media_url,
                    )
                    await mark_processed(session, user.tg_user_id, tw.id, tw.username)
                    continue

                # SINGLE mode
                # Проверяем дубликат темы — не публикуем то что уже было
                is_dup, sim, _ = await is_duplicate_topic(
                    session, channel.id, tw.text
                )
                if is_dup:
                    await mark_processed(session, user.tg_user_id, tw.id, tw.username)
                    logger.info(
                        "Channel %d: tweet %s is duplicate (sim=%.2f), skipping",
                        channel.id, tw.id, sim,
                    )
                    continue

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

                # Подбираем картинку через LLM keywords + Unsplash API
                photo_url = None
                if (
                    channel.images_enabled
                    and _cfg_unsplash.unsplash_access_key
                ):
                    try:
                        keywords = await llm.suggest_image_keywords(rewritten)
                        if keywords:
                            photo_url = await fetch_image_url_for_keywords(
                                keywords, _cfg_unsplash.unsplash_access_key
                            )
                            if photo_url:
                                logger.info(
                                    "Channel %d: image found via '%s'",
                                    channel.id, keywords,
                                )
                    except Exception:
                        logger.exception("Image fetch failed, posting without image")

                ok = await send_to_target(
                    bot, session, fake_target, rewritten,
                    photo_url=photo_url,
                )
                if ok:
                    await mark_processed(session, user.tg_user_id, tw.id, tw.username)
                    await log_post(
                        session, user.tg_user_id, channel.id, is_digest=False,
                        topic_signature=compute_topic_signature(tw.text),
                    )
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


async def _process_vk_source(
    src,
    channel: Channel,
    user,
    limits,
    llm: OpenRouterClient,
    niche_prompt: str,
    vk_results: dict,
    bot,
    fake_target,
) -> None:
    """Обрабатывает один VK-источник: фильтрует, переписывает, публикует."""
    identifier = src.twitter_username.lower()
    posts: list[VKPost] = vk_results.get(identifier, [])
    if not posts:
        return

    filtered = [
        p for p in posts
        if not p.is_pinned
        and p.text
        and len(p.text.strip()) >= 20
        and p.likes >= channel.min_likes
    ]
    if not filtered:
        return

    async with session_maker()() as session:
        for post in filtered:
            post_uid = f"vk_{post.owner_id}_{post.id}"

            if await is_processed(session, user.tg_user_id, post_uid):
                continue

            if channel.mode in ("digest", "hybrid"):
                await enqueue_for_digest(
                    session=session,
                    user_id=user.tg_user_id,
                    channel_id=channel.id,
                    tweet_id=post_uid,
                    twitter_username=identifier,
                    text=post.text,
                    url=post.url,
                    likes=post.likes,
                    retweets=post.reposts,
                    media_url=post.image_url,
                )
                await mark_processed(session, user.tg_user_id, post_uid, identifier)
                continue

            # SINGLE mode
            is_dup, sim, _ = await is_duplicate_topic(session, channel.id, post.text)
            if is_dup:
                await mark_processed(session, user.tg_user_id, post_uid, identifier)
                continue

            today = await posts_today(session, user.tg_user_id)
            if today >= limits.max_posts_per_day:
                return

            rewritten = await _rewrite_vk_post(llm, post, niche_prompt)
            if not rewritten:
                await mark_processed(session, user.tg_user_id, post_uid, identifier)
                continue

            photo_url = post.image_url if channel.images_enabled else None

            ok = await send_to_target(bot, session, fake_target, rewritten, photo_url=photo_url)
            if ok:
                await mark_processed(session, user.tg_user_id, post_uid, identifier)
                await log_post(
                    session, user.tg_user_id, channel.id, is_digest=False,
                    topic_signature=compute_topic_signature(post.text),
                )
                logger.info("Posted VK post %s to channel=%d", post_uid, channel.id)
                import asyncio as _asyncio
                await _asyncio.sleep(3)
            break


async def _rewrite_vk_post(
    llm: OpenRouterClient, post: VKPost, system_prompt: str
) -> str | None:
    """Адаптирует VK-пост для Telegram через LLM."""
    user_prompt = (
        f"URL: {post.url}\n\n"
        f"Текст поста:\n{post.text}"
    )
    result = await llm._call_with_retry(system_prompt, user_prompt, max_tokens=500)
    if not result:
        return None
    clean = result.strip()
    first_token = (
        clean.split()[0].upper().strip(".,;:!?<>[]")
        if clean.split() else ""
    )
    if first_token == "SKIP":
        return None
    if clean.upper().startswith("SKIP") and len(clean) < 80:
        return None
    return clean
