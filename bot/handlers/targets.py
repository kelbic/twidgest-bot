"""Управление целями постинга: /target, /targets, /removetarget."""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from db.repositories.users import (
    add_target,
    get_or_create_user,
    is_tier_active,
    remove_target,
)
from db.session import session_maker
from tiers import get_limits

router = Router(name="targets")


TARGET_HELP = """\
<b>Как добавить канал/чат:</b>

1. Создай канал в Telegram (или используй готовый).
2. Добавь @TwidgestBot админом с правом «Публикация сообщений».
3. Перешли любое сообщение из канала в чат с ботом.
4. Бот определит chat_id и предложит подтвердить.

Или используй: <code>/target -1001234567890 digest</code> — если знаешь chat_id.
Режим: <code>single</code> или <code>digest</code>.
"""


@router.message(Command("targets"))
async def cmd_targets(message: Message) -> None:
    if message.from_user is None:
        return
    async with session_maker()() as session:
        user = await get_or_create_user(
            session, message.from_user.id, message.from_user.username
        )
        if not user.targets:
            await message.answer(
                "У вас нет каналов для постинга.\n"
                "Настройте через /target."
            )
            return
        lines = ["📢 <b>Ваши цели для постинга:</b>\n"]
        for t in user.targets:
            mark = "✅" if t.is_active else "⏸"
            title = t.chat_title or f"chat_id={t.chat_id}"
            lines.append(f"{mark} <b>{title}</b> — режим: <code>{t.mode}</code> (id={t.id})")
        lines.append("\nУдалить: <code>/removetarget &lt;id&gt;</code>")
        await message.answer("\n".join(lines))


@router.message(Command("target"))
async def cmd_target(message: Message, command: CommandObject) -> None:
    if message.from_user is None:
        return
    if not command.args:
        await message.answer(TARGET_HELP)
        return

    parts = command.args.split()
    if len(parts) < 1 or len(parts) > 2:
        await message.answer(TARGET_HELP)
        return

    try:
        chat_id = int(parts[0])
    except ValueError:
        await message.answer("❌ chat_id должен быть числом (обычно начинается с -100...).")
        return

    mode = parts[1].lower() if len(parts) > 1 else "digest"
    if mode not in {"single", "digest"}:
        await message.answer("❌ Режим должен быть <code>single</code> или <code>digest</code>.")
        return

    async with session_maker()() as session:
        user = await get_or_create_user(
            session, message.from_user.id, message.from_user.username
        )
        active = await is_tier_active(user)
        effective_tier = user.tier if active else "free"
        limits = get_limits(effective_tier)

        if len(user.targets) >= limits.max_targets:
            await message.answer(
                f"❌ Достигнут лимит каналов для тарифа <b>{limits.name}</b>: "
                f"{limits.max_targets}."
            )
            return

        if mode == "digest" and not limits.can_use_digest_mode:
            await message.answer(
                f"❌ Режим <code>digest</code> доступен от тарифа Starter. /upgrade"
            )
            return

        target = await add_target(
            session, user.tg_user_id, chat_id=chat_id, chat_title=None, mode=mode
        )
        await message.answer(
            f"✅ Канал добавлен (id={target.id}).\n"
            f"Режим: <code>{mode}</code>.\n\n"
            f"⚠️ Убедитесь, что бот добавлен в канал админом."
        )


@router.message(Command("removetarget"))
async def cmd_removetarget(message: Message, command: CommandObject) -> None:
    if message.from_user is None or not command.args:
        await message.answer("Используйте: <code>/removetarget &lt;id&gt;</code>")
        return
    try:
        target_id = int(command.args.strip())
    except ValueError:
        await message.answer("❌ id должен быть числом.")
        return

    async with session_maker()() as session:
        user = await get_or_create_user(
            session, message.from_user.id, message.from_user.username
        )
        removed = await remove_target(session, user.tg_user_id, target_id)
        if removed:
            await message.answer(f"🗑 Цель {target_id} удалена.")
        else:
            await message.answer(f"⚠️ Цель {target_id} не найдена.")
