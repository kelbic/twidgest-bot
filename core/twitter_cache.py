"""In-memory кэш для twitterapi.io с TTL.

Один и тот же @bryan_johnson может быть в источниках у 50 юзеров.
Без кэша мы бы делали 50 запросов за цикл, с кэшем — 1.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from core import metrics
from core.twitter_client import Tweet, TwitterClient

logger = logging.getLogger(__name__)

# Adaptive TTL: для источников, которые публикуют редко, увеличиваем интервал
# между опросами по схеме exponential backoff.
# Если N раз подряд fetch вернул только дубликаты (без новых твитов),
# TTL = BASE_TTL * 2^N, но не больше MAX_TTL.
# Любой новый твит сбрасывает stale_count в 0.
BASE_TTL_SECONDS = 1800     # 30 минут — для активных источников
MAX_TTL_SECONDS = 21600     # 6 часов — потолок для совсем тихих

def _effective_ttl(stale_count: int, base_ttl: int) -> int:
    """TTL с учётом backoff. stale_count=0 → base_ttl, дальше удвоение."""
    return min(base_ttl * (2 ** stale_count), MAX_TTL_SECONDS)


@dataclass
class _CacheEntry:
    tweets: list[Tweet]
    fetched_at: float
    tweet_ids: frozenset[str] = field(default_factory=frozenset)
    # stale_count = сколько раз подряд получили только дубликаты.
    # Используется для exponential backoff TTL.
    stale_count: int = 0


class TwitterCache:
    def __init__(self, client: TwitterClient, ttl_seconds: int = 1800) -> None:
        self.client = client
        self.ttl = ttl_seconds
        self._cache: dict[str, _CacheEntry] = {}
        self._lock = asyncio.Lock()
        # Per-username locks — чтобы при cold-start 100 юзеров на одного автора
        # не сделать 100 параллельных запросов
        self._fetch_locks: dict[str, asyncio.Lock] = {}

    async def get_tweets(self, username: str, limit: int = 20) -> list[Tweet]:
        username = username.lstrip("@").lower()
        now = time.time()

        # Проверяем свежий кэш с adaptive TTL
        entry = self._cache.get(username)
        effective = _effective_ttl(entry.stale_count, self.ttl) if entry else self.ttl
        if entry and (now - entry.fetched_at) < effective:
            metrics.inc("tw_cache_hits")
            logger.debug(
                "Cache hit for @%s (age=%ds, ttl=%ds, stale=%d)",
                username, int(now - entry.fetched_at), effective, entry.stale_count,
            )
            return entry.tweets

        # Берём per-username lock, чтобы исключить дублирующие fetch'и
        async with self._lock:
            lock = self._fetch_locks.setdefault(username, asyncio.Lock())

        async with lock:
            # Может быть, пока ждали, кто-то уже зафетчил
            entry = self._cache.get(username)
            effective = _effective_ttl(entry.stale_count, self.ttl) if entry else self.ttl
            if entry and (time.time() - entry.fetched_at) < effective:
                return entry.tweets

            # Свежий fetch
            tweets = await self.client.get_user_tweets(username, limit=limit)
            new_ids = frozenset(t.id for t in tweets)

            # Считаем новый stale_count относительно предыдущей записи
            if entry is None:
                new_stale = 0
            elif new_ids - entry.tweet_ids:
                # Есть хотя бы один новый ID — источник публикует, сбрасываем backoff
                new_stale = 0
            else:
                # Только дубликаты — источник тихий, увеличиваем интервал
                new_stale = entry.stale_count + 1

            # Логируем изменение stale_count (только когда реально меняется)
            if entry is not None and new_stale != entry.stale_count:
                next_ttl = _effective_ttl(new_stale, self.ttl)
                logger.info(
                    "@%s stale_count %d → %d, next TTL = %ds",
                    username, entry.stale_count, new_stale, next_ttl,
                )

            self._cache[username] = _CacheEntry(
                tweets=tweets,
                fetched_at=time.time(),
                tweet_ids=new_ids,
                stale_count=new_stale,
            )
            logger.info(
                "Fetched & cached @%s: %d tweets (stale=%d)",
                username, len(tweets), new_stale,
            )
            return tweets

    def stats(self) -> dict[str, Any]:
        # Распределение источников по effective TTL — видно, кто в backoff.
        ttl_buckets: dict[int, int] = {}
        stale_distribution: dict[int, int] = {}
        for entry in self._cache.values():
            eff = _effective_ttl(entry.stale_count, self.ttl)
            ttl_buckets[eff] = ttl_buckets.get(eff, 0) + 1
            stale_distribution[entry.stale_count] = (
                stale_distribution.get(entry.stale_count, 0) + 1
            )
        return {
            "cached_usernames": len(self._cache),
            "active_locks": len(self._fetch_locks),
            "ttl_distribution_seconds": dict(sorted(ttl_buckets.items())),
            "stale_count_distribution": dict(sorted(stale_distribution.items())),
        }
