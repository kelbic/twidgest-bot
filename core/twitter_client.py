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
    """Клиент twitterapi.io с retry и exp backoff."""

    # HTTP-коды на которых ретраим
    _RETRYABLE_HTTP = {408, 429, 500, 502, 503, 504}
    _MAX_ATTEMPTS = 3
    _BASE_DELAY = 2.0  # начальная пауза, потом 2x на каждую попытку

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self._headers = {"X-API-Key": api_key}

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        params: dict | None = None,
        timeout: int = 30,
    ) -> dict | None:
        """Универсальный запрос с retry на временных ошибках."""
        import asyncio as _asyncio

        last_error = "unknown"
        for attempt in range(1, self._MAX_ATTEMPTS + 1):
            try:
                async with aiohttp.ClientSession(headers=self._headers) as session:
                    async with session.request(
                        method, url, params=params, timeout=timeout
                    ) as resp:
                        if resp.status == 200:
                            try:
                                data = await resp.json()
                                if attempt > 1:
                                    logger.info(
                                        "twitterapi.io: succeeded on attempt %d/%d",
                                        attempt, self._MAX_ATTEMPTS,
                                    )
                                return data
                            except Exception as exc:
                                last_error = f"bad JSON: {exc}"
                        elif resp.status in self._RETRYABLE_HTTP:
                            body = await resp.text()
                            last_error = f"HTTP {resp.status}: {body[:200]}"
                            logger.warning(
                                "twitterapi.io retryable error attempt %d/%d: %s",
                                attempt, self._MAX_ATTEMPTS, last_error,
                            )
                        else:
                            # Non-retryable — сразу возвращаем None
                            body = await resp.text()
                            logger.error(
                                "twitterapi.io non-retryable HTTP %d: %s",
                                resp.status, body[:200],
                            )
                            return None
            except _asyncio.TimeoutError:
                last_error = f"timeout after {timeout}s"
                logger.warning(
                    "twitterapi.io timeout attempt %d/%d (URL: %s)",
                    attempt, self._MAX_ATTEMPTS, url,
                )
            except aiohttp.ClientError as exc:
                last_error = f"network error: {exc}"
                logger.warning(
                    "twitterapi.io network error attempt %d/%d: %s",
                    attempt, self._MAX_ATTEMPTS, last_error,
                )
            except Exception:
                logger.exception("twitterapi.io unexpected error")
                return None

            # Если не последняя попытка — ждём с exp backoff
            if attempt < self._MAX_ATTEMPTS:
                delay = self._BASE_DELAY * (2 ** (attempt - 1))  # 2s, 4s, 8s
                await _asyncio.sleep(delay)

        logger.error(
            "twitterapi.io exhausted %d attempts. Last error: %s",
            self._MAX_ATTEMPTS, last_error,
        )
        return None

    async def get_user_tweets(self, username: str, limit: int = 20) -> list[Tweet]:
        params = {"userName": username}
        url = f"{BASE_URL}/twitter/user/last_tweets"

        data = await self._request_with_retry("GET", url, params=params, timeout=30)
        if not data or not isinstance(data, dict):
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

    async def search_users(self, query: str, limit: int = 20) -> list[dict]:
        url = f"{BASE_URL}/twitter/user/search"
        params = {"query": query, "limit": str(limit)}

        data = await self._request_with_retry("GET", url, params=params, timeout=30)
        if not data or not isinstance(data, dict):
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

    async def validate_usernames(
        self, usernames: list[str]
    ) -> dict[str, bool]:
        """Параллельная валидация существования аккаунтов."""
        import asyncio as _asyncio

        async def _check(username: str) -> tuple[str, bool]:
            try:
                tweets = await self.get_user_tweets(username, limit=5)
                return username, len(tweets) > 0
            except Exception:
                logger.exception("validate failed for @%s", username)
                return username, False

        results: dict[str, bool] = {}
        semaphore = _asyncio.Semaphore(5)

        async def _bounded(u: str):
            async with semaphore:
                u_clean, alive = await _check(u)
                results[u_clean] = alive

        await _asyncio.gather(*(_bounded(u.lstrip("@").strip()) for u in usernames if u.strip()))
        return results
