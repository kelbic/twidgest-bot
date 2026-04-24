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

from core.twitter_client import Tweet, TwitterClient

logger = logging.getLogger(__name__)


@dataclass
class _CacheEntry:
    tweets: list[Tweet]
    fetched_at: float


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

        # Проверяем свежий кэш
        entry = self._cache.get(username)
        if entry and (now - entry.fetched_at) < self.ttl:
            logger.debug("Cache hit for @%s (age=%ds)", username, int(now - entry.fetched_at))
            return entry.tweets

        # Берём per-username lock, чтобы исключить дублирующие fetch'и
        async with self._lock:
            lock = self._fetch_locks.setdefault(username, asyncio.Lock())

        async with lock:
            # Может быть, пока ждали, кто-то уже зафетчил
            entry = self._cache.get(username)
            if entry and (time.time() - entry.fetched_at) < self.ttl:
                return entry.tweets

            tweets = await self.client.get_user_tweets(username, limit=limit)
            self._cache[username] = _CacheEntry(tweets=tweets, fetched_at=time.time())
            logger.info("Fetched & cached @%s: %d tweets", username, len(tweets))
            return tweets

    def stats(self) -> dict[str, Any]:
        return {
            "cached_usernames": len(self._cache),
            "active_locks": len(self._fetch_locks),
        }
