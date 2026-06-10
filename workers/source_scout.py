"""Скаут источников: подбирает и проверяет новые X-аккаунты для канала.

Запускается ТОЛЬКО по запросу (кнопка в health-алерте или /scout) — поиск
и превалидация стоят денег (twitterapi.io + LLM). Никогда не меняет
источники сам: результат — карточка владельцу с кнопками (HIL).

Превалидация В КОДЕ, без LLM (prevalidate_candidates, переиспользуется
в /createchannel ai): по последним твитам кандидата считаем:
- долю текстовых твитов (>= 40 знаков, не reply) — отсев медиа-аккаунтов;
- сколько твитов прошло бы пороги конкретного канала (passing);
- частоту постинга (твитов/нед) по parsed_created_at;
- ОТДАЧУ: ожидаемое число публикуемых постов в неделю =
  passing/total * твитов/нед. Это главная метрика сортировки: автор
  с фильтром 20/20, но двумя твитами в месяц, даст каналу ~0.5 поста/нед
  и честно уезжает вниз списка.
Гейты: < MIN_TWEETS твитов (мёртвый/выдуманный), text_share < порога,
passing < порога, последний твит старше MAX_SILENCE_DAYS (неактивен).
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime

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
# Минимум твитов из выборки, которые прошли бы фильтры канала
MIN_PASSING = 2
# Последний твит старше этого — автор неактивен, в новостной канал не годится
MAX_SILENCE_DAYS = 21
# Частота ниже этой помечается «⚠️ редко» в карточке (но не отсеивается)
RARE_TWEETS_PER_WEEK = 3.0
# Сколько лучших кандидатов показываем владельцу
TOP_N = 5
# Параллельность fetch'ей при превалидации
PROBE_CONCURRENCY = 3

_USERNAME_RE = re.compile(r"[a-z0-9_]{1,32}")


@dataclass
class CandidateStats:
    """Кандидат, прошедший превалидацию."""

    username: str            # lowercase, без @
    reason: str              # объяснение, почему релевантен теме
    total: int               # всего твитов в выборке
    text_share: float        # доля текстовых твитов (0..1)
    median_likes: int        # медиана лайков по текстовым твитам
    passing: int             # сколько твитов прошло бы фильтры канала
    tweets_per_week: float   # частота постинга автора
    est_posts_per_week: float  # ожидаемая отдача каналу: passing/total * частота

    def stats_line(self) -> str:
        """Человекочитаемая строка метрик — хранится в ScoutSuggestion.stats."""
        rare = " ⚠️ пишет редко" if self.tweets_per_week < RARE_TWEETS_PER_WEEK else ""
        return (
            f"текстовых {int(self.text_share * 100)}% · ❤ медиана {self.median_likes} · "
            f"фильтр {self.passing}/{self.total} · ≈{self.tweets_per_week:.0f} тв/нед — "
            f"даст ~{self.est_posts_per_week:.1f} поста/нед{rare}"
        )


def _build_topic(channel: Channel) -> str:
    bits = [channel.title or "", channel.niche or ""]
    if channel.description:
        bits.append(channel.description[:300])
    return " / ".join(b for b in bits if b)


async def prevalidate_candidates(
    candidates: list[tuple[str, str]],
    min_likes: int,
    min_retweets: int,
    cache: TwitterCache,
) -> list[CandidateStats]:
    """Проверяет кандидатов по их реальным последним твитам.

    candidates — [(username_lowercase, reason)], уже без дублей и без
    уже подключённых. Возвращает прошедших гейты, отсортированных по
    est_posts_per_week (отдача каналу), затем по медиане лайков.

    min_likes/min_retweets = 0 допустимо (например, при создании канала,
    когда пороги ещё не настроены) — тогда passing считает просто
    текстовые твиты, а отдача вырождается в text_share * частота.
    """
    sem = asyncio.Semaphore(PROBE_CONCURRENCY)
    results: list[CandidateStats] = []
    now = datetime.utcnow()

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

        dates = sorted(t.parsed_created_at for t in tweets)
        newest, oldest = dates[-1], dates[0]

        silence_days = (now - newest).days
        if silence_days > MAX_SILENCE_DAYS:
            logger.info(
                "scout: @%s dropped — last tweet %d days ago (inactive)",
                username, silence_days,
            )
            return

        span_days = max((newest - oldest).total_seconds() / 86400.0, 0.05)
        tweets_per_week = min((len(tweets) - 1) / span_days * 7.0, 99.0)

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
            if t.likes >= min_likes and t.retweets >= min_retweets
        )
        if passing < MIN_PASSING:
            logger.info(
                "scout: @%s dropped — only %d tweets pass thresholds "
                "(min_likes=%d, min_retweets=%d)",
                username, passing, min_likes, min_retweets,
            )
            return

        likes_sorted = sorted(t.likes for t in texty)
        median_likes = likes_sorted[len(likes_sorted) // 2]
        est_posts_per_week = passing / len(tweets) * tweets_per_week

        results.append(CandidateStats(
            username=username,
            reason=reason[:200],
            total=len(tweets),
            text_share=text_share,
            median_likes=median_likes,
            passing=passing,
            tweets_per_week=tweets_per_week,
            est_posts_per_week=est_posts_per_week,
        ))

    await asyncio.gather(*(_probe(u, r) for u, r in candidates))
    results.sort(key=lambda c: (c.est_posts_per_week, c.median_likes), reverse=True)
    return results


async def discover_sources(
    channel: Channel,
    llm: OpenRouterClient,
    cache: TwitterCache,
) -> list[CandidateStats]:
    """Полный цикл скаута для одного канала: LLM-подбор + превалидация.

    channel должен быть загружен с channel_sources (selectinload).
    Возвращает до TOP_N кандидатов, отсортированных по отдаче каналу.
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

    results = await prevalidate_candidates(
        candidates, eff_min_likes, eff_min_retweets, cache
    )
    top = results[:TOP_N]
    logger.info(
        "scout: channel %d, %d candidates survived prevalidation: %s",
        channel.id, len(top),
        [(c.username, f"{c.est_posts_per_week:.1f}/wk") for c in top],
    )
    return top
