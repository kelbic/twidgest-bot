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

    @property
    def engagement(self) -> int:
        return self.likes + self.retweets * 3

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "Tweet":
        author = data.get("author") or {}
        username = (author.get("userName") or author.get("screen_name") or "").lstrip("@")
        tweet_id = str(data.get("id") or "")
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
        )


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
