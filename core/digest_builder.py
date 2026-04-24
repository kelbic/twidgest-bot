"""Логика построения дайджеста для конкретного юзера."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from core.llm_client import DigestTweet, OpenRouterClient
from db.models import DigestQueueItem

logger = logging.getLogger(__name__)


@dataclass
class DigestBuildResult:
    text: str
    used_item_ids: list[int]


async def build_user_digest(
    items: list[DigestQueueItem],
    llm: OpenRouterClient,
    niche: str,
    custom_prompt: str | None = None,
) -> DigestBuildResult | None:
    if not items:
        return None

    # custom_prompt пока не используется — добавим в Итерации 4
    digest_tweets = [
        DigestTweet(
            username=it.twitter_username,
            text=it.text,
            url=it.url,
            likes=it.likes,
            retweets=it.retweets,
        )
        for it in items
    ]

    digest_text = await llm.build_digest(digest_tweets, niche=niche)
    if not digest_text:
        return None

    return DigestBuildResult(
        text=digest_text,
        used_item_ids=[it.id for it in items],
    )
