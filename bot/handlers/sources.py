"""Команды управления источниками каналов:
/sources <channel_id> — список
/addsource <channel_id> @user — добавить
/removesource <channel_id> @user — удалить
"""
from __future__ import annotations

import re

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy import select

from config import Config
from core.twitter_client import TwitterClient
from db.models import Channel, ChannelSource
from db.repositories.users import get_or_create_user
from db.session import session_maker

router = Router(name="channel_sources")

_cfg = Config()
_twitter = TwitterClient(_cfg.twitter_api_key)


def _parse_args(text: str | None, expected: int) -> tuple | None:
    """Простой парсер: split + проверка количества."""
    if not text:
        return None
    parts = text.strip().split()
    if len(parts) < expected:
        return None
    return tuple(parts[:expected])


async def _get_user_channel(user_id: int, channel_id: int) -> Channel | None:
    """Возвращает канал юзера со всеми источниками или None."""
    from sqlalchemy.orm import selectinload
    async with session_maker()() as session:
        result = await session.execute(
            select(Channel)
            .where(Channel.id == channel_id, Channel.user_id == user_id)
            .options(selectinload(Channel.channel_sources))
        )
        return result.scalar_one_or_none()


@router.message(Command("sources"))
async def cmd_sources(message: Message, command: CommandObject) -> None:
    """Список источников канала."""
    if message.from_user is None:
        return
    if not command.args:
        await message.answer(
            "Использование: <code>/sources &lt;channel_id&gt;</code>\n\n"
            "ID каналов — в /channels"
        )
        return

    try:
        channel_id = int(command.args.strip().split()[0])
    except (ValueError, IndexError):
        await message.answer("❌ ID канала должен быть числом.")
        return

    channel = await _get_user_channel(message.from_user.id, channel_id)
    if channel is None:
        await message.answer(f"⚠️ Канал {channel_id} не найден или не твой.")
        return

    if not channel.channel_sources:
        await message.answer(
            f"<b>{channel.title}</b> (id={channel_id})\n\n"
            f"Источников нет.\n\n"
            f"Добавить: <code>/addsource {channel_id} @username</code>"
        )
        return

    lines = [f"📡 <b>Источники канала «{channel.title}»</b> (id={channel_id})\n"]
    for i, src in enumerate(channel.channel_sources, 1):
        active = "✅" if src.is_active else "⏸"
        lines.append(f"  {i}. {active} @{src.twitter_username}")

    lines.append(
        f"\n<b>Команды:</b>\n"
        f"  Добавить: <code>/addsource {channel_id} @username</code>\n"
        f"  Удалить: <code>/removesource {channel_id} @username</code>"
    )
    await message.answer("\n".join(lines))


@router.message(Command("addsource"))
async def cmd_addsource(message: Message, command: CommandObject) -> None:
    """Добавить источник в канал. Проверяет существование через twitterapi.io."""
    if message.from_user is None:
        return
    args = _parse_args(command.args, 2)
    if not args:
        await message.answer(
            "Использование: <code>/addsource &lt;channel_id&gt; @username</code>\n\n"
            "Пример: <code>/addsource 5 @hubermanlab</code>"
        )
        return

    try:
        channel_id = int(args[0])
    except ValueError:
        await message.answer("❌ ID канала должен быть числом.")
        return

    username = args[1].lstrip("@").strip()
    if not re.fullmatch(r"[A-Za-z0-9_]{1,32}", username):
        await message.answer(
            "❌ Некорректный username. Только латиница, цифры, _ (до 32 символов)."
        )
        return

    channel = await _get_user_channel(message.from_user.id, channel_id)
    if channel is None:
        await message.answer(f"⚠️ Канал {channel_id} не найден или не твой.")
        return

    # Проверяем дубль
    existing = {s.twitter_username.lower() for s in channel.channel_sources}
    if username.lower() in existing:
        await message.answer(f"⚠️ @{username} уже добавлен в канал.")
        return

    # Валидация через twitterapi.io
    status_msg = await message.answer(
        f"🔍 Проверяю что @{username} существует в X..."
    )

    validation = await _twitter.validate_usernames([username])
    is_alive = validation.get(username.lower(), False) or validation.get(username, False)

    if not is_alive:
        await status_msg.edit_text(
            f"❌ Аккаунт @{username} не найден в X или не публикует твиты.\n\n"
            f"Проверь правильность написания имени."
        )
        return

    # Добавляем в БД
    async with session_maker()() as session:
        session.add(ChannelSource(
            channel_id=channel_id,
            twitter_username=username,
            is_active=True,
        ))
        await session.commit()

    await status_msg.edit_text(
        f"✅ Источник <b>@{username}</b> добавлен в канал <b>{channel.title}</b>.\n\n"
        f"Бот начнёт собирать твиты с него в следующем цикле (до 30 мин)."
    )


@router.message(Command("removesource"))
async def cmd_removesource(message: Message, command: CommandObject) -> None:
    """Удалить источник из канала."""
    if message.from_user is None:
        return
    args = _parse_args(command.args, 2)
    if not args:
        await message.answer(
            "Использование: <code>/removesource &lt;channel_id&gt; @username</code>"
        )
        return

    try:
        channel_id = int(args[0])
    except ValueError:
        await message.answer("❌ ID канала должен быть числом.")
        return

    username = args[1].lstrip("@").strip()

    channel = await _get_user_channel(message.from_user.id, channel_id)
    if channel is None:
        await message.answer(f"⚠️ Канал {channel_id} не найден или не твой.")
        return

    # Найдём источник
    target_source = None
    for src in channel.channel_sources:
        if src.twitter_username.lower() == username.lower():
            target_source = src
            break

    if target_source is None:
        await message.answer(
            f"⚠️ Источник @{username} не найден в этом канале.\n\n"
            f"Список: <code>/sources {channel_id}</code>"
        )
        return

    async with session_maker()() as session:
        # Удаляем явно через delete
        from sqlalchemy import delete as sa_delete
        await session.execute(
            sa_delete(ChannelSource).where(ChannelSource.id == target_source.id)
        )
        await session.commit()

    remaining = len(channel.channel_sources) - 1
    await message.answer(
        f"🗑 Источник <b>@{username}</b> удалён из канала <b>{channel.title}</b>.\n\n"
        f"Осталось источников: {remaining}"
    )
