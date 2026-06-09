"""Скаут источников: подбирает новые X-аккаунты для канала.

Запускается ТОЛЬКО по запросу (кнопка в health-алерте или /scout) — поиск
и превалидация стоят денег (twitterapi.io + LLM). Никогда не меняет
источники сам: результат — карточка владельцу с кнопками (HIL).

Пайплайн:
1. LLM предлагает кандидатов по теме канала (suggest_sources — уже есть).
2. Исключаем уже подключённые источники и невалидные username.
3. Превалидация В КОДЕ, без LLM: фетчим последние твиты кандидата (через
   TwitterCache) и считаем, сколько твитов реально прошло бы фильтры
   ИМЕННО ЭТОГО канала: текст >= 40 знаков, не reply, likes/retweets выше
   порогов канала. Это отсекает главную причину молчания каналов —
   медиа-аккаунты, которые постят видео/картинки с короткими подписями.
   Заодно бесплатно отсеиваются выдуманные LLM аккаунты: у них fetch
   вернёт пусто.
4. Топ-N по (passing, median_likes) возвращаем вызывающему коду.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

from core.llm_client import OpenRouterClient
from core.twitter_cache import TwitterCache
from db.models import Channel

logger = logging.getLogger(__name__)

# Сколько кандидатов просим у LLM (до превалидации)
LLM_SUGGEST_COUNT = 12
# Минимум твитов у живого аккаунта (меньше — мёртвый/закрытый/выдуманный)
MIN_TWEETS = 5
# Минимальная доля "текстовых" твитов (>= 40 знаков, не reply)
MIN_TEXT_SHARE = 0.35
# Минимум твитов из последних 20, которые прошли бы фильтры канала
MIN_PASSING = 2
# Сколько лучших кандидатов показываем владельцу
TOP_N = 5
# Параллельность fetch'ей при превалидации
PROBE_CONCURRENCY = 3

_USERNAME_RE = re.compile(r"[a-z0-9_]{1,32}")


@dataclass
class CandidateStats:
    """Кандидат, прошедший превалидацию."""

    username: str       # lowercase, без @
    reason: str         # объяснение от LLM, почему релевантен теме
    total: int          # всего твитов в выборке
    text_share: float   # доля текстовых твитов (0..1)
    median_likes: int   # медиана лайков по текстовым твитам
    passing: int        # сколько твитов прошло бы фильтры канала

    def stats_line(self) -> str:
        """Человекочитаемая строка метрик — хранится в ScoutSuggestion.stats."""
        return (
            f"текстовых {int(self.text_share * 100)}%, "
            f"медиана ❤ {self.median_likes}, "
            f"прошло бы фильтр {self.passing} из {self.total}"
        )


def _build_topic(channel: Channel) -> str:
    bits = [channel.title or "", channel.niche or ""]
    if channel.description:
        bits.append(channel.description[:300])
    return " / ".join(b for b in bits if b)


async def discover_sources(
    channel: Channel,
    llm: OpenRouterClient,
    cache: TwitterCache,
) -> list[CandidateStats]:
    """Полный цикл скаута для одного канала.

    channel должен быть загружен с channel_sources (selectinload).
    Возвращает до TOP_N валидированных кандидатов, отсортированных по
    (passing, median_likes) — то есть по реальной пользе для этого канала.
    """
    existing = {
        s.twitter_username.lower().lstrip("@")
        for s in channel.channel_sources
    }

    topic = _build_topic(channel)
    suggested = await llm.suggest_sources(topic, count=LLM_SUGGEST_COUNT) or []
    logger.info(
        "scout: channel %d, LLM suggested %d accounts", channel.id, len(suggested)
    )

    seen: set[str] = set()
    candidates: list[tuple[str, str]] = []
    for s in suggested:
        u = (s.get("username") or "").lstrip("@").strip().lower()
        if not u or u in existing or u in seen:
            continue
        if not _USERNAME_RE.fullmatch(u):
            continue
        # VK-источники скаут не трогает
        if u.startswith("vk:"):
            continue
        seen.add(u)
        candidates.append((u, (s.get("reason") or "").strip()))

    if not candidates:
        return []

    # Пороги канала: для unfiltered снимаем виральность (как в collector)
    eff_min_likes = (
        0 if channel.filter_preset == "unfiltered" else channel.min_likes
    )
    eff_min_retweets = (
        0 if channel.filter_preset == "unfiltered" else channel.min_retweets
    )

    sem = asyncio.Semaphore(PROBE_CONCURRENCY)
    results: list[CandidateStats] = []

    async def _probe(username: str, reason: str) -> None:
        async with sem:
            try:
                tweets = await cache.get_tweets(username, limit=20)
            except Exception:
                logger.exception("scout: fetch failed for @%s", username)
                return

        if len(tweets) < MIN_TWEETS:
            logger.info(
                "scout: @%s dropped — only %d tweets (dead/private/hallucinated)",
                username, len(tweets),
            )
            return

        texty = [
            t for t in tweets
            if t.text and len(t.text.strip()) >= 40 and not t.is_reply
        ]
        text_share = len(texty) / len(tweets)
        if text_share < MIN_TEXT_SHARE:
            logger.info(
                "scout: @%s dropped — text share %.0f%% (media-heavy)",
                username, text_share * 100,
            )
            return

        passing = sum(
            1 for t in texty
            if t.likes >= eff_min_likes and t.retweets >= eff_min_retweets
        )
        if passing < MIN_PASSING:
            logger.info(
                "scout: @%s dropped — only %d tweets pass channel thresholds "
                "(min_likes=%d, min_retweets=%d)",
                username, passing, eff_min_likes, eff_min_retweets,
            )
            return

        likes_sorted = sorted(t.likes for t in texty)
        median_likes = likes_sorted[len(likes_sorted) // 2]

        results.append(CandidateStats(
            username=username,
            reason=reason[:200],
            total=len(tweets),
            text_share=text_share,
            median_likes=median_likes,
            passing=passing,
        ))

    await asyncio.gather(*(_probe(u, r) for u, r in candidates))

    results.sort(key=lambda c: (c.passing, c.median_likes), reverse=True)
    top = results[:TOP_N]
    logger.info(
        "scout: channel %d, %d candidates survived prevalidation: %s",
        channel.id, len(top), [c.username for c in top],
    )
    return top
