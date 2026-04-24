"""Авто-определение чата при пересылке сообщения юзером.

Логика: если у юзера есть Channel без привязанного target — привязываем к нему.
Если у юзера несколько Channel без target — показываем выбор.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message

from db.repositories.channels import (
    get_user_channels,
    set_channel_target,
)
from db.repositories.users import get_or_create_user
from db.session import session_maker

router = Router(name="forward")


@router.message(F.forward_from_chat)
async def handle_forwarded_from_channel(message: Message) -> None:
    if message.from_user is None or message.forward_from_chat is None:
        return

    chat = message.forward_from_chat
    chat_id = chat.id
    chat_title = chat.title or "Без названия"

    if chat.type not in {"channel", "supergroup", "group"}:
        await message.answer(
            "⚠️ Я работаю только с каналами и группами. "
            "Перешли мне сообщение из канала, куда хочешь постить."
        )
        return

    async with session_maker()() as session:
        user = await get_or_create_user(
            session, message.from_user.id, message.from_user.username
        )
        all_channels = await get_user_channels(session, user.tg_user_id)

        # Проверяем, не привязан ли этот chat_id уже к какому-то каналу
        already_used = next(
            (c for c in all_channels if c.target_chat_id == chat_id), None
        )
        if already_used:
            await message.answer(
                f"⚠️ Этот канал <b>{chat_title}</b> уже привязан к "
                f"твоему каналу <b>{already_used.title}</b> (id={already_used.id})."
            )
            return

        # Каналы без target
        unbound = [c for c in all_channels if c.target_chat_id is None]
        if not unbound:
            await message.answer(
                f"📍 Получил chat_id <code>{chat_id}</code> "
                f"(<b>{chat_title}</b>).\n\n"
                f"⚠️ У тебя нет каналов без настроенного target.\n"
                f"Создай новый: /createchannel"
            )
            return

        # Если ровно один канал без target — привязываем к нему
        if len(unbound) == 1:
            channel = unbound[0]
            await set_channel_target(
                session, channel.id, chat_id, chat_title
            )
            await message.answer(
                f"✅ <b>Канал привязан!</b>\n\n"
                f"📍 <b>{chat_title}</b> → "
                f"{getattr(channel, 'title', 'канал')} (id={channel.id})\n\n"
                f"Бот начнёт постить туда в ближайшие 30 минут (после следующего "
                f"цикла сбора).\n\n"
                f"⚠️ Убедись, что я админ в канале с правом «Публикация сообщений»."
            )
            return

        # Несколько каналов без target — просим выбрать
        lines = [
            f"📍 Получил chat_id <code>{chat_id}</code> "
            f"(<b>{chat_title}</b>).\n",
            "У тебя несколько каналов без target. К какому привязать?\n",
        ]
        for ch in unbound:
            lines.append(
                f"  • <b>{ch.title}</b> (id={ch.id})"
            )
        lines.append(
            f"\nКоманда: <code>/bind {chat_id} &lt;channel_id&gt;</code>"
        )
        await message.answer("\n".join(lines))


# Дополнительная команда для ручной привязки
@router.message(F.text.startswith("/bind"))
async def cmd_bind(message: Message) -> None:
    """Ручная привязка: /bind <chat_id> <channel_id>"""
    if message.from_user is None or message.text is None:
        return
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("Использование: <code>/bind &lt;chat_id&gt; &lt;channel_id&gt;</code>")
        return
    try:
        chat_id = int(parts[1])
        channel_id = int(parts[2])
    except ValueError:
        await message.answer("❌ chat_id и channel_id должны быть числами.")
        return

    async with session_maker()() as session:
        all_channels = await get_user_channels(session, message.from_user.id)
        target_channel = next((c for c in all_channels if c.id == channel_id), None)
        if target_channel is None:
            await message.answer(f"⚠️ Канал {channel_id} не найден или не твой.")
            return
        await set_channel_target(session, channel_id, chat_id, None)

    await message.answer(
        f"✅ chat_id <code>{chat_id}</code> привязан к каналу "
        f"<b>{target_channel.title}</b> (id={channel_id})."
    )
