"""Доступ к processed_tweets и digest_queue."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import DigestLog, DigestQueueItem, PostLog, ProcessedTweet


async def is_processed(session: AsyncSession, user_id: int, tweet_id: str) -> bool:
    result = await session.execute(
        select(ProcessedTweet.id).where(
            and_(
                ProcessedTweet.user_id == user_id,
                ProcessedTweet.tweet_id == tweet_id,
            )
        )
    )
    return result.scalar_one_or_none() is not None


async def mark_processed(
    session: AsyncSession, user_id: int, tweet_id: str, twitter_username: str
) -> None:
    item = ProcessedTweet(
        user_id=user_id, tweet_id=tweet_id, twitter_username=twitter_username
    )
    session.add(item)
    try:
        await session.commit()
    except Exception:
        await session.rollback()  # дубль — не страшно


async def enqueue_for_digest(
    session: AsyncSession,
    user_id: int,
    tweet_id: str,
    twitter_username: str,
    text: str,
    url: str,
    likes: int,
    retweets: int,
) -> None:
    item = DigestQueueItem(
        user_id=user_id,
        tweet_id=tweet_id,
        twitter_username=twitter_username,
        text=text,
        url=url,
        likes=likes,
        retweets=retweets,
    )
    session.add(item)
    try:
        await session.commit()
    except Exception:
        await session.rollback()


async def get_digest_queue(
    session: AsyncSession, user_id: int, max_items: int
) -> list[DigestQueueItem]:
    result = await session.execute(
        select(DigestQueueItem)
        .where(DigestQueueItem.user_id == user_id)
        .order_by((DigestQueueItem.likes + DigestQueueItem.retweets * 3).desc())
        .limit(max_items)
    )
    return list(result.scalars().all())


async def clear_digest_items(session: AsyncSession, ids: list[int]) -> None:
    if not ids:
        return
    await session.execute(delete(DigestQueueItem).where(DigestQueueItem.id.in_(ids)))
    await session.commit()


async def last_digest_time(
    session: AsyncSession, user_id: int, target_id: int
) -> datetime | None:
    result = await session.execute(
        select(DigestLog.posted_at)
        .where(
            and_(DigestLog.user_id == user_id, DigestLog.target_id == target_id)
        )
        .order_by(DigestLog.posted_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def log_digest(
    session: AsyncSession, user_id: int, target_id: int, tweet_count: int
) -> None:
    item = DigestLog(user_id=user_id, target_id=target_id, tweet_count=tweet_count)
    session.add(item)
    await session.commit()


async def posts_today(session: AsyncSession, user_id: int) -> int:
    """Сколько постов юзер опубликовал за последние 24 часа."""
    cutoff = datetime.utcnow() - timedelta(hours=24)
    result = await session.execute(
        select(func.count(PostLog.id)).where(
            and_(PostLog.user_id == user_id, PostLog.posted_at > cutoff)
        )
    )
    return int(result.scalar_one() or 0)


async def log_post(
    session: AsyncSession, user_id: int, target_id: int, is_digest: bool
) -> None:
    item = PostLog(user_id=user_id, target_id=target_id, is_digest=is_digest)
    session.add(item)
    await session.commit()
