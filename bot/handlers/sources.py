"""Управление источниками: /sources, /addsource, /removesource."""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from db.repositories.users import (
    add_source,
    get_or_create_user,
    is_tier_active,
    remove_source,
)
from db.session import session_maker
from tiers import get_limits

router = Router(name="sources")


@router.message(Command("sources"))
async def cmd_sources(message: Message) -> None:
    if message.from_user is None:
        return
    async with session_maker()() as session:
        user = await get_or_create_user(
            session, message.from_user.id, message.from_user.username
        )
        if not user.sources:
            await message.answer(
                "У вас нет источников.\n"
                "Добавь первый: <code>/addsource @bryan_johnson</code>"
            )
            return
        lines = ["📡 <b>Ваши источники:</b>\n"]
        for src in user.sources:
            mark = "✅" if src.is_active else "⏸"
            lines.append(f"{mark} @{src.twitter_username}")
        lines.append(f"\nВсего: <b>{len(user.sources)}</b>")
        await message.answer("\n".join(lines))


@router.message(Command("addsource"))
async def cmd_addsource(message: Message, command: CommandObject) -> None:
    if message.from_user is None:
        return
    if not command.args:
        await message.answer(
            "Используйте: <code>/addsource @username</code>\n"
            "Пример: <code>/addsource @bryan_johnson</code>"
        )
        return

    username = command.args.strip().lstrip("@")
    # Базовая валидация
    if not username or " " in username or len(username) > 32:
        await message.answer("❌ Некорректный username.")
        return

    async with session_maker()() as session:
        user = await get_or_create_user(
            session, message.from_user.id, message.from_user.username
        )
        active = await is_tier_active(user)
        effective_tier = user.tier if active else "free"
        limits = get_limits(effective_tier)

        if len(user.sources) >= limits.max_sources:
            await message.answer(
                f"❌ Достигнут лимит источников для тарифа <b>{limits.name}</b> "
                f"({limits.max_sources}).\n\n"
                f"Удалите ненужный через /removesource или /upgrade тариф."
            )
            return

        source = await add_source(session, user.tg_user_id, username)
        if source is None:
            await message.answer(f"⚠️ Источник @{username} уже добавлен.")
            return

        await message.answer(
            f"✅ Добавлен источник: @{username}\n"
            f"Источников: {len(user.sources) + 1}/{limits.max_sources}"
        )


@router.message(Command("removesource"))
async def cmd_removesource(message: Message, command: CommandObject) -> None:
    if message.from_user is None:
        return
    if not command.args:
        await message.answer("Используйте: <code>/removesource @username</code>")
        return

    username = command.args.strip().lstrip("@")
    async with session_maker()() as session:
        user = await get_or_create_user(
            session, message.from_user.id, message.from_user.username
        )
        removed = await remove_source(session, user.tg_user_id, username)
        if removed:
            await message.answer(f"🗑 Источник @{username} удалён.")
        else:
            await message.answer(f"⚠️ Источник @{username} не найден.")
