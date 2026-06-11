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

from core import budget, metrics
from core.plan import channel_active, posts_cap
from core.candidate_ranker import rank_candidates
from core.llm_client import OpenRouterClient
from config import Config
from core.image_picker import fetch_image_url_for_keywords
from core.safe_sender import ChannelTarget, send_to_target
from core.topic_dedup import compute_topic_signature, is_duplicate_topic
from db.models import Channel, DigestQueueItem, PostLog, RejectionLog, User
from db.repositories.tweets import (
    clear_digest_items,
    log_post,
    posts_today_channel,
)
from db.session import session_maker
from prompts import build_single_prompt

logger = logging.getLogger(__name__)

_cfg_unsplash = Config()

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
    session: AsyncSession, channel_id: int, limit: int = 5,
    per_source_cap: int = 2, pool: int = 40,
) -> list[DigestQueueItem]:
    """Топ-N твитов, но не больше per_source_cap от одного источника.

    Берём широкий пул по engagement, затем прореживаем по источнику,
    чтобы один популярный аккаунт не занял все слоты.
    """
    result = await session.execute(
        select(DigestQueueItem)
        .where(
            DigestQueueItem.channel_id == channel_id,
            DigestQueueItem.skipped_at.is_(None),
            DigestQueueItem.posted_at_single.is_(None),
            DigestQueueItem.queued_at > datetime.utcnow() - timedelta(hours=24),
            DigestQueueItem.tweet_created_at > datetime.utcnow() - timedelta(hours=48),
        )
        .order_by((DigestQueueItem.likes + DigestQueueItem.retweets * 3).desc())
        .limit(pool)
    )
    rows = list(result.scalars().all())

    picked: list[DigestQueueItem] = []
    per_source: dict[str, int] = {}
    for row in rows:
        src = (row.twitter_username or "").lower()
        if per_source.get(src, 0) >= per_source_cap:
            continue
        picked.append(row)
        per_source[src] = per_source.get(src, 0) + 1
        if len(picked) >= limit:
            break
    return picked


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
    logger.info("cost-totals: %s", metrics.totals_line())


async def _process_hybrid_channel(
    channel: Channel, bot: Bot, llm: OpenRouterClient
) -> None:
    user = channel.user
    if not channel_active(channel):
        logger.info(
            "viral_picker: skipping channel %d — inactive (no paid/trial)",
            channel.id,
        )
        return

    async with session_maker()() as session:
        logger.info(
            "viral_picker: processing channel %d (%s)", channel.id, channel.title
        )

        # Pacing — для новых каналов (<24ч) уменьшаем интервал
        # чтобы юзер быстро увидел результат продукта
        last_single = await _last_single_post_time(session, channel.id)
        now = datetime.utcnow()
        channel_age = now - channel.created_at
        if channel_age < timedelta(hours=24):
            pacing_minutes = 15  # активация: до 4 виральных в час
        else:
            pacing_minutes = SINGLE_POST_PACING_MINUTES  # 30 мин (default)

        if last_single and (now - last_single) < timedelta(minutes=pacing_minutes):
            logger.info(
                "Channel %d: last single was %s ago, skipping pacing (age=%s, pacing=%dm)",
                channel.id, now - last_single, channel_age, pacing_minutes,
            )
            return

        # Daily quota — на канал (слот-модель)
        today = await posts_today_channel(session, channel.id)
        cap = posts_cap(channel)
        if today >= cap:
            logger.info(
                "Channel %d hit daily quota %d/%d",
                channel.id, today, cap,
            )
            return

        # Берём топ-N кандидатов
        candidates = await _get_top_queue_items(
            session, channel.id, limit=TOP_CANDIDATES_TO_TRY
        )
        if not candidates:
            logger.info("Channel %d: queue empty, no viral pick", channel.id)
            return

        # === РЕВЬЮВЕР-РАНКЕР ===
        # Один батч-вызов дешёвой LLM на всех кандидатов сразу.
        # junk → skipped_at + RejectionLog (в digest твит всё ещё может попасть,
        # там промпт мягче — та же семантика, что у обычного SKIP).
        # Остальные сортируются по interest. При сбое LLM — fail-open:
        # остаёмся на исходном порядке по engagement, канал НЕ молчит.
        # === ДНЕВНОЙ LLM-БЮДЖЕТ: деградация, не тишина ===
        # Исчерпан -> без ранкера, только явный топ по виральности (2x порог),
        # одна попытка rewrite. Канал продолжает публиковать лучшее.
        if budget.exhausted(channel.id):
            hi_bar = max(channel.min_likes * 2, channel.min_likes + 1)
            candidates = [c for c in candidates if c.likes >= hi_bar][:1]
            if not candidates:
                logger.info(
                    "Channel %d: eval budget exhausted, no 2x-viral candidates "
                    "this cycle", channel.id,
                )
                return
            logger.warning(
                "Channel %d: eval budget exhausted — degraded mode "
                "(top-viral only, no ranker)", channel.id,
            )
            ranking = None
        else:
            budget.spend(channel.id)  # вызов ранкера
            ranking = await rank_candidates(llm, channel, candidates)
        if ranking is None:
            logger.warning(
                "Channel %d: ranker unavailable, fail-open to engagement order",
                channel.id,
            )
        else:
            kept: list[DigestQueueItem] = []
            for c in candidates:
                verdict = ranking.get(c.id)
                if verdict is None:
                    # Ранкер не вынес вердикт по этому id — не наказываем твит
                    kept.append(c)
                    continue
                c.interest_score = verdict.interest  # сырьё недельного отчёта
                if verdict.junk:
                    c.skipped_at = datetime.utcnow()
                    session.add(RejectionLog(
                        channel_id=channel.id,
                        tweet_id=c.tweet_id,
                        twitter_username=c.twitter_username,
                        # reason — String(32): "review:" + 24 символа причины
                        reason=f"review:{verdict.why[:24]}",
                    ))
                    logger.info(
                        "Channel %d: @%s junked by reviewer (%s)",
                        channel.id, c.twitter_username, verdict.why,
                    )
                    continue
                kept.append(c)
            await session.commit()

            kept.sort(
                key=lambda c: ranking[c.id].interest if c.id in ranking else 0,
                reverse=True,
            )
            if kept:
                logger.info(
                    "Channel %d: ranked order: %s",
                    channel.id,
                    [(c.twitter_username,
                      ranking[c.id].interest if c.id in ranking else "-")
                     for c in kept],
                )
            candidates = kept
            if not candidates:
                logger.info(
                    "Channel %d: reviewer junked all candidates this cycle",
                    channel.id,
                )
                return

        niche_prompt = build_single_prompt(channel.niche, channel.filter_preset,
                                           legal_rf=channel.legal_rf_filter)

        # Идём по топу, пробуем каждый
        for idx, top in enumerate(candidates, 1):
            if top.likes < channel.min_likes:
                logger.info(
                    "Channel %d: candidate %d/%d below like floor (%d), trying next",
                    channel.id, idx, len(candidates), top.likes,
                )
                continue

            logger.info(
                "Channel %d: trying candidate %d/%d @%s likes=%d",
                channel.id, idx, len(candidates),
                top.twitter_username, top.likes,
            )

            # Проверка на дубликат темы — главный фикс
            is_dup, sim, _ = await is_duplicate_topic(
                session, channel.id, top.text
            )
            if is_dup:
                logger.info(
                    "Channel %d: candidate %d is duplicate (sim=%.2f) of recent post, skipping",
                    channel.id, idx, sim,
                )
                continue

            if not budget.spend(channel.id):
                logger.warning(
                    "Channel %d: eval budget hit mid-cycle, stopping attempts",
                    channel.id,
                )
                break
            rewritten, skip_reason = await _rewrite(llm, top, niche_prompt)
            if not rewritten:
                # SKIP — помечаем skipped_at, чтобы не пробовать снова в след. циклах.
                # Из очереди НЕ удаляем — может пойти в digest (там промпт мягче).
                from datetime import datetime as _dt
                top.skipped_at = _dt.utcnow()
                from db.models import RejectionLog
                session.add(RejectionLog(
                    channel_id=channel.id,
                    tweet_id=top.tweet_id,
                    twitter_username=top.twitter_username,
                    reason=f"skip_viral:{skip_reason}",
                ))
                await session.commit()
                logger.info(
                    "Channel %d: candidate %d/%d got SKIP (%s), trying next",
                    channel.id, idx, len(candidates), skip_reason,
                )
                continue

            fake_target = ChannelTarget(
                channel_id=channel.id,
                chat_id=channel.target_chat_id,
            )

            # Подбираем релевантную картинку через LLM keywords + Unsplash API
            photo_url = None
            if (
                channel.images_enabled
                and _cfg_unsplash.unsplash_access_key
            ):
                try:
                    keywords = await llm.suggest_image_keywords(rewritten)
                    if not keywords:
                        logger.warning(
                            "Channel %d: LLM returned empty image keywords for post: %s",
                            channel.id, rewritten[:80],
                        )
                    else:
                        photo_url = await fetch_image_url_for_keywords(
                            keywords, _cfg_unsplash.unsplash_access_key
                        )
                        if photo_url:
                            logger.info(
                                "Channel %d: image found via '%s'",
                                channel.id, keywords,
                            )
                        else:
                            logger.warning(
                                "Channel %d: Unsplash returned no image for keywords=%r",
                                channel.id, keywords,
                            )
                except Exception:
                    logger.exception("Image fetch failed, posting without image")

            ok = await send_to_target(
                bot, session, fake_target, rewritten,
                photo_url=photo_url,
            )
            if ok:
                # Помечаем как опубликованный в single (не удаляем — для digest pool).
                # Single больше не возьмёт его, а digest может включить как
                # "обзор лучшего за период". После digest он уже удалится.
                top.posted_at_single = datetime.utcnow()
                await session.commit()
                await log_post(
                    session, user.tg_user_id, channel.id, is_digest=False,
                    topic_signature=compute_topic_signature(top.text),
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
        return None, "llm_empty"
    clean = result.strip()
    first_token = (
        clean.split()[0].upper().strip(".,;:!?<>[]")
        if clean.split()
        else ""
    )
    if first_token == "SKIP":
        return None, "skip_token"
    if clean.upper().startswith("SKIP") and len(clean) < 80:
        return None, "skip_short"
    meta_markers = (
        "я не вижу", "пожалуйста скопируйте", "i don't see",
        "i cannot", "i'm sorry", "as an ai",
    )
    if any(m in clean.lower() for m in meta_markers):
        return None, "meta_marker"
    return clean, None
