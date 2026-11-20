"""CRUD для Channel + ChannelSource."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from datetime import datetime, timedelta

from core.plan import TRIAL_DAYS, _ADMIN_ID
from db.models import Channel, ChannelSource, User
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
    # Слот-модель: первый канал юзера получает триал TRIAL_DAYS дней,
    # ровно один раз (users.trial_used). Остальные создаются неактивными —
    # активация за PRICE_STARS через /upgrade. Админ живёт на байпасе,
    # триал ему не нужен и не расходуется.
    if user_id != _ADMIN_ID:
        u_res = await session.execute(select(User).where(User.tg_user_id == user_id))
        owner = u_res.scalar_one_or_none()
        if owner is not None and not owner.trial_used:
            channel.trial_until = datetime.utcnow() + timedelta(days=TRIAL_DAYS)
            owner.trial_used = True

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
        .where(Channel.user_id == user_id, Channel.archived_at == None)  # noqa: E711
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
    # Явная очистка связанных таблиц (на случай если CASCADE не сработает в SQLite)
    from sqlalchemy import delete as sa_delete
    from db.models import DigestQueueItem, RejectionLog, HealthNotification, DigestLog, PostLog
    await session.execute(
        sa_delete(DigestQueueItem).where(DigestQueueItem.channel_id == channel_id)
    )
    await session.execute(
        sa_delete(RejectionLog).where(RejectionLog.channel_id == channel_id)
    )
    await session.execute(
        sa_delete(HealthNotification).where(HealthNotification.channel_id == channel_id)
    )
    # Чистим DigestLog и PostLog по target_id=channel_id
    await session.execute(
        sa_delete(DigestLog).where(DigestLog.target_id == channel_id)
    )
    await session.execute(
        sa_delete(PostLog).where(PostLog.target_id == channel_id)
    )

    await session.delete(channel)
    await session.commit()
    return True


async def set_channel_target(
    session: AsyncSession,
    channel_id: int,
    target_chat_id: int,
    target_chat_title: str | None,
    activate: bool = False,
) -> bool:
    """Привязка Telegram-канала к дайджесту (Channel.target_chat_id/title).

    activate=True — заодно снять паузу is_active=False: её ставит только
    safe_sender при потере доступа к каналу, а привязка — момент, когда
    доступ (заново) выдан."""
    channel = await get_channel(session, channel_id)
    if channel is None:
        return False
    channel.target_chat_id = target_chat_id
    channel.target_chat_title = target_chat_title
    if activate:
        channel.is_active = True
    await session.commit()
    return True


# --------------------------------------------------------------------------- #
# Канал, ждущий дайджест.
#
# Юзер сделал бота админом своего Telegram-канала (кнопка startchannel →
# my_chat_member), а дайджеста без канала у него нет (или их несколько и надо
# выбрать). Запоминаем канал до создания/выбора дайджеста.
#
# Хранилище — legacy-таблица targets (Target: user_id, chat_id, chat_title —
# ровно «куда юзер хочет постить»; воркеры её не читают). Строки ожидания
# помечены mode="pending", чтобы не смешиваться со старыми /target-записями.
# Новой схемы нет. Строка живёт до привязки или до потери прав в канале.
# --------------------------------------------------------------------------- #

PENDING_MODE = "pending"


async def remember_pending_channel(
    session: AsyncSession, user_id: int, chat_id: int, chat_title: str | None
) -> None:
    """Upsert по (user_id, chat_id): повторное добавление бота не плодит строк."""
    from db.models import Target

    res = await session.execute(
        select(Target).where(
            Target.user_id == user_id,
            Target.chat_id == chat_id,
            Target.mode == PENDING_MODE,
        )
    )
    row = res.scalars().first()
    if row is None:
        session.add(Target(
            user_id=user_id, chat_id=chat_id, chat_title=chat_title,
            mode=PENDING_MODE, is_active=True,
        ))
    else:
        row.chat_title = chat_title
    await session.commit()


async def get_pending_channels(session: AsyncSession, user_id: int) -> list:
    """Каналы юзера, ждущие дайджест, свежие первыми."""
    from db.models import Target

    res = await session.execute(
        select(Target)
        .where(Target.user_id == user_id, Target.mode == PENDING_MODE)
        .order_by(Target.added_at.desc(), Target.id.desc())
    )
    return list(res.scalars().all())


async def drop_pending_channel(
    session: AsyncSession, chat_id: int, user_id: int | None = None
) -> int:
    """Снять ожидание канала: у одного юзера или (user_id=None) у всех —
    когда бот потерял права в канале. Возвращает число снятых строк."""
    from sqlalchemy import delete as sa_delete
    from db.models import Target

    q = sa_delete(Target).where(
        Target.chat_id == chat_id, Target.mode == PENDING_MODE
    )
    if user_id is not None:
        q = q.where(Target.user_id == user_id)
    res = await session.execute(q)
    await session.commit()
    return int(res.rowcount or 0)
