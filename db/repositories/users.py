"""Доступ к таблице users + связанные сущности."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import Source, Target, User, UserSettings


async def get_or_create_user(
    session: AsyncSession, tg_user_id: int, tg_username: str | None = None
) -> User:
    """Возвращает юзера, создавая если не существует. Также создаёт UserSettings."""
    result = await session.execute(
        select(User)
        .where(User.tg_user_id == tg_user_id)
        .options(selectinload(User.sources), selectinload(User.targets), selectinload(User.settings), selectinload(User.channels))
    )
    user = result.scalar_one_or_none()
    if user:
        # Обновим username, если поменялся
        if tg_username and user.tg_username != tg_username:
            user.tg_username = tg_username
            await session.commit()
        return user

    trial_expires = datetime.utcnow() + timedelta(days=30)
    user = User(tg_user_id=tg_user_id, tg_username=tg_username, tier="free",
                tier_expires_at=trial_expires)
    session.add(user)
    settings = UserSettings(user_id=tg_user_id)
    session.add(settings)
    await session.commit()
    await session.refresh(user)
    # подгрузим relationships
    result = await session.execute(
        select(User)
        .where(User.tg_user_id == tg_user_id)
        .options(selectinload(User.sources), selectinload(User.targets), selectinload(User.settings), selectinload(User.channels))
    )
    return result.scalar_one()


async def get_active_users(session: AsyncSession) -> list[User]:
    """Все не заблокированные пользователи (для воркера сбора)."""
    result = await session.execute(
        select(User)
        .where(User.is_blocked == False)  # noqa: E712
        .options(selectinload(User.sources), selectinload(User.targets), selectinload(User.settings), selectinload(User.channels))
    )
    return list(result.scalars().all())


async def add_source(
    session: AsyncSession, user_id: int, twitter_username: str
) -> Source | None:
    """Добавляет источник. Возвращает None, если уже существует."""
    twitter_username = twitter_username.lstrip("@").strip()
    if not twitter_username:
        return None
    existing = await session.execute(
        select(Source).where(
            Source.user_id == user_id,
            Source.twitter_username == twitter_username,
        )
    )
    if existing.scalar_one_or_none():
        return None
    source = Source(user_id=user_id, twitter_username=twitter_username)
    session.add(source)
    await session.commit()
    await session.refresh(source)
    return source


async def remove_source(
    session: AsyncSession, user_id: int, twitter_username: str
) -> bool:
    """Удаляет источник. True если удалили, False если не было."""
    twitter_username = twitter_username.lstrip("@").strip()
    result = await session.execute(
        select(Source).where(
            Source.user_id == user_id,
            Source.twitter_username == twitter_username,
        )
    )
    source = result.scalar_one_or_none()
    if not source:
        return False
    await session.delete(source)
    await session.commit()
    return True


async def add_target(
    session: AsyncSession,
    user_id: int,
    chat_id: int,
    chat_title: str | None,
    mode: str = "digest",
) -> Target:
    target = Target(
        user_id=user_id, chat_id=chat_id, chat_title=chat_title, mode=mode
    )
    session.add(target)
    await session.commit()
    await session.refresh(target)
    return target


async def remove_target(session: AsyncSession, user_id: int, target_id: int) -> bool:
    result = await session.execute(
        select(Target).where(Target.id == target_id, Target.user_id == user_id)
    )
    target = result.scalar_one_or_none()
    if not target:
        return False
    await session.delete(target)
    await session.commit()
    return True


async def is_tier_active(user: User) -> bool:
    """Проверяет активность тарифа. Free = trial 30 дней, Pro = до expires_at."""
    if user.tier_expires_at is None:
        # Старые Free-юзеры без expires_at — даём доступ, но логируем
        return user.tier != "free"
    return user.tier_expires_at > datetime.utcnow()
