"""CRUD для Channel + ChannelSource."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import Channel, ChannelSource
from engagement_defaults import get_engagement_defaults


async def create_channel(
    session: AsyncSession,
    user_id: int,
    title: str,
    niche: str,
    template_id: str | None = None,
    description: str | None = None,
    mode: str = "digest",
    sources: list[str] | None = None,
) -> Channel:
    min_likes, min_retweets = get_engagement_defaults(niche)
    channel = Channel(
        user_id=user_id,
        title=title,
        niche=niche,
        template_id=template_id,
        description=description,
        mode=mode,
        min_likes=min_likes,
        min_retweets=min_retweets,
    )
    session.add(channel)
    await session.flush()  # получаем id

    if sources:
        for username in sources:
            username = username.lstrip("@").strip()
            if not username:
                continue
            session.add(
                ChannelSource(
                    channel_id=channel.id,
                    twitter_username=username,
                )
            )

    await session.commit()
    await session.refresh(channel)

    # Подгружаем relationships
    result = await session.execute(
        select(Channel)
        .where(Channel.id == channel.id)
        .options(selectinload(Channel.channel_sources))
    )
    return result.scalar_one()


async def get_user_channels(session: AsyncSession, user_id: int) -> list[Channel]:
    result = await session.execute(
        select(Channel)
        .where(Channel.user_id == user_id)
        .options(selectinload(Channel.channel_sources))
        .order_by(Channel.created_at.desc())
    )
    return list(result.scalars().all())


async def get_channel(
    session: AsyncSession, channel_id: int, user_id: int | None = None
) -> Channel | None:
    """Получает канал с проверкой владельца (если user_id передан)."""
    query = select(Channel).where(Channel.id == channel_id).options(
        selectinload(Channel.channel_sources)
    )
    if user_id is not None:
        query = query.where(Channel.user_id == user_id)
    result = await session.execute(query)
    return result.scalar_one_or_none()


async def delete_channel(
    session: AsyncSession, channel_id: int, user_id: int
) -> bool:
    channel = await get_channel(session, channel_id, user_id)
    if channel is None:
        return False
    await session.delete(channel)
    await session.commit()
    return True


async def set_channel_target(
    session: AsyncSession,
    channel_id: int,
    target_chat_id: int,
    target_chat_title: str | None,
) -> bool:
    channel = await get_channel(session, channel_id)
    if channel is None:
        return False
    channel.target_chat_id = target_chat_id
    channel.target_chat_title = target_chat_title
    await session.commit()
    return True
