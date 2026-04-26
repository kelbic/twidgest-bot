"""Клиент для TwitterAPI.io — получение последних твитов аккаунта."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

BASE_URL = "https://api.twitterapi.io"


@dataclass
class Tweet:
    id: str
    username: str
    text: str
    likes: int
    retweets: int
    replies: int
    is_reply: bool
    url: str
    created_at: str
    media_url: str | None = None

    @property
    def engagement(self) -> int:
        return self.likes + self.retweets * 3

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "Tweet":
        author = data.get("author") or {}
        username = (author.get("userName") or author.get("screen_name") or "").lstrip("@")
        tweet_id = str(data.get("id") or "")

        # Парсим media — пробуем разные структуры (твиттер API менялся со временем)
        media_url = cls._extract_media_url(data)

        return cls(
            id=tweet_id,
            username=username,
            text=data.get("text") or "",
            likes=int(data.get("likeCount") or 0),
            retweets=int(data.get("retweetCount") or 0),
            replies=int(data.get("replyCount") or 0),
            is_reply=bool(data.get("isReply")),
            url=data.get("url") or f"https://x.com/{username}/status/{tweet_id}",
            created_at=data.get("createdAt") or "",
            media_url=media_url,
        )

    @staticmethod
    def _extract_media_url(data: dict[str, Any]) -> str | None:
        """Извлекает URL картинки из твита. Пробует разные структуры API."""
        # Вариант 1: extendedEntities.media (Twitter API v1.1)
        ext = data.get("extendedEntities") or {}
        media_list = ext.get("media") or []
        for m in media_list:
            if m.get("type") == "photo":
                url = m.get("media_url_https") or m.get("media_url")
                if url:
                    return url

        # Вариант 2: entities.media
        ent = data.get("entities") or {}
        media_list = ent.get("media") or []
        for m in media_list:
            if m.get("type") == "photo":
                url = m.get("media_url_https") or m.get("media_url")
                if url:
                    return url

        # Вариант 3: attachments + includes (Twitter API v2 style)
        attachments = data.get("attachments") or {}
        media_keys = attachments.get("media_keys") or []
        if media_keys:
            # Тут нужно мап через includes — сложнее, пока не делаем
            pass

        # Вариант 4: media field directly
        direct_media = data.get("media") or []
        if isinstance(direct_media, list):
            for m in direct_media:
                if isinstance(m, dict) and m.get("type") == "photo":
                    url = m.get("media_url_https") or m.get("url")
                    if url:
                        return url

        return None


class TwitterClient:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self._headers = {"X-API-Key": api_key}

    async def get_user_tweets(self, username: str, limit: int = 20) -> list[Tweet]:
        params = {"userName": username}
        url = f"{BASE_URL}/twitter/user/last_tweets"

        try:
            async with aiohttp.ClientSession(headers=self._headers) as session:
                async with session.get(url, params=params, timeout=30) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.error(
                            "twitterapi.io HTTP %s for @%s: %s",
                            resp.status, username, body[:300],
                        )
                        return []
                    data = await resp.json()
        except Exception:
            logger.exception("Network error fetching @%s", username)
            return []

        # API всегда возвращает {"data": {"tweets": [...]}, "status": ...}
        if not isinstance(data, dict):
            logger.warning("Unexpected response type for @%s: %s", username, type(data))
            return []

        if data.get("status") != "success":
            logger.warning(
                "API error for @%s: code=%s msg=%s",
                username, data.get("code"), data.get("msg"),
            )
            return []

        inner = data.get("data") or {}
        tweets_raw = inner.get("tweets") or []

        if not tweets_raw:
            logger.info("No tweets returned for @%s (account may have no recent activity)", username)
            return []

        tweets: list[Tweet] = []
        for item in tweets_raw[:limit]:
            try:
                tweets.append(Tweet.from_api(item))
            except Exception:
                logger.exception("Failed to parse tweet from @%s", username)

        logger.info("Fetched %d tweets for @%s", len(tweets), username)
        return tweets
    async def validate_usernames(
        self, usernames: list[str]
    ) -> dict[str, bool]:
        """Для каждого username делаем 1 get_user_tweets, считаем живым если >= 1 твит.

        Возвращает {username: is_alive}.
        """
        import asyncio as _asyncio

        async def _check(username: str) -> tuple[str, bool]:
            try:
                tweets = await self.get_user_tweets(username, limit=5)
                return username, len(tweets) > 0
            except Exception:
                logger.exception("validate failed for @%s", username)
                return username, False

        # Параллельно, но с ограничением — API не любит 20 RPS
        results: dict[str, bool] = {}
        semaphore = _asyncio.Semaphore(5)

        async def _bounded(u: str):
            async with semaphore:
                u_clean, alive = await _check(u)
                results[u_clean] = alive

        await _asyncio.gather(*(_bounded(u.lstrip("@").strip()) for u in usernames if u.strip()))
        return results
    async def search_users(
        self, query: str, limit: int = 20
    ) -> list[dict]:
        """Поиск реальных аккаунтов X по ключевым словам.

        Возвращает список dict с полями:
        - screen_name (реальный @username, без @)
        - name (отображаемое имя)
        - description (био)
        - followers_count
        - statuses_count
        - is_verified (Blue Verified)
        """
        url = f"{BASE_URL}/twitter/user/search"
        params = {"query": query, "limit": str(limit)}
        try:
            async with aiohttp.ClientSession(headers=self._headers) as session:
                async with session.get(url, params=params, timeout=30) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.error(
                            "search_users HTTP %s for query=%r: %s",
                            resp.status, query, body[:300],
                        )
                        return []
                    data = await resp.json()
        except Exception:
            logger.exception("Network error in search_users(%r)", query)
            return []

        users_raw = data.get("users") or data.get("data", {}).get("users") or []
        if not users_raw:
            logger.info("search_users: no results for query=%r", query)
            return []

        result = []
        for u in users_raw[:limit]:
            screen_name = u.get("screen_name") or ""
            if not screen_name:
                continue
            result.append({
                "screen_name": screen_name.lstrip("@").strip(),
                "name": u.get("name") or "",
                "description": (u.get("description") or "")[:300],
                "followers_count": int(u.get("followers_count") or 0),
                "statuses_count": int(u.get("statuses_count") or 0),
                "is_verified": bool(u.get("isBlueVerified") or u.get("verified")),
            })
        logger.info("search_users(%r): %d users found", query, len(result))
        return result

