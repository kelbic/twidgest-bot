"""Онбординг: общие кнопки/тексты шага «подключить канал» и редирект из групп.

Термины в текстах для юзера: «дайджест» — сущность бота (тема + источники),
«канал» — ТОЛЬКО настоящий Telegram-канал юзера, куда дайджест публикуется.

Настройка ведётся только в личке. Команды бота в группе не исполняются —
бот отвечает одной кнопкой «настроить в личном чате» (t.me/<бот>?start=setup).
Username бота берём у самого бота (bot.me() кешируется aiogram'ом).
"""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

logger = logging.getLogger(__name__)
router = Router(name="onboarding")

# Права, которые просим кнопкой startchannel. Бот в канале только ПУБЛИКУЕТ
# (send_message/send_photo в core/safe_sender.py) и никогда не правит и не
# удаляет свои посты — поэтому edit_messages/delete_messages не просим.
CHANNEL_ADMIN_RIGHTS = "post_messages"

CREATE_BUTTON = "🚀 Создать дайджест"
CONNECT_BUTTON = "📢 Подключить Telegram-канал"

CONNECT_STEP = (
    "Если канала ещё нет — создай пустой канал в Telegram. "
    "Затем нажми кнопку ниже и выбери его."
)

CONNECT_FALLBACK = (
    "Не сработала кнопка? Добавь бота админом с правом публиковать посты, "
    "напиши в канале слово «тест» и перешли это сообщение сюда. "
    "Подпись Telegram „не имеет доступа к сообщениям“ — это норма, "
    "она не мешает публикации."
)

GROUP_REDIRECT = (
    "👋 Настройка дайджеста — в личном чате со мной, в группе я её не веду. "
    "Нажми кнопку 👇"
)

# Команды бота: в группе без @упоминания реагируем только на свои
# (чужие /команды других ботов молча пропускаем).
BOT_COMMANDS = frozenset({
    "start", "help", "createchannel", "channels", "deletechannel", "bind",
    "sources", "addsource", "removesource", "regenerate", "scout", "status",
    "setinterval", "setimages", "setthreshold", "setminterest",
    "setsourceinterest", "setdescription", "setfilter", "filters", "setlegal",
    "me", "upgrade", "payments", "paycheck", "legal", "tg_help", "costs",
    "target", "targets", "removetarget", "admin",
})


async def bot_username(bot: Bot) -> str:
    """Username бота из самого бота (getMe один раз, дальше кеш aiogram)."""
    me = await bot.me()
    return me.username or ""


def setup_url(username: str) -> str:
    return f"https://t.me/{username}?start=setup"


def connect_channel_url(username: str) -> str:
    """Deep-link «выбрать канал и сделать бота админом» с нужными правами."""
    return f"https://t.me/{username}?startchannel&admin={CHANNEL_ADMIN_RIGHTS}"


async def connect_channel_button(bot: Bot) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=CONNECT_BUTTON, url=connect_channel_url(await bot_username(bot))
    )


async def connect_channel_kb(bot: Bot) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[await connect_channel_button(bot)]])


def create_digest_kb() -> InlineKeyboardMarkup:
    """Кнопка «Создать дайджест» → просим тему (обработчик qs:ai в start.py)."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=CREATE_BUTTON, callback_data="qs:ai"),
    ]])


def split_command(text: str) -> tuple[str, str | None]:
    """'/Cmd@Bot args' → ('cmd', 'bot'); '/cmd' → ('cmd', None)."""
    head = text.split(maxsplit=1)[0] if text.strip() else ""
    name, _, mention = head.lstrip("/").partition("@")
    return name.lower(), (mention.lower() or None)


@router.message(F.chat.type.in_({"group", "supergroup"}), F.text.startswith("/"))
async def group_command_redirect(message: Message, bot: Bot) -> None:
    """Любая команда бота в группе → короткий ответ с кнопкой в личку.

    Сам флоу в группе не запускаем: там не работают кнопки-продолжения,
    видят все участники, а privacy mode режет обычные ответы-тексты."""
    if message.text is None:
        return
    name, mention = split_command(message.text)
    me = (await bot_username(bot)).lower()
    if mention is not None:
        if mention != me:
            return  # команда другому боту
    elif name not in BOT_COMMANDS:
        return  # чужая/неизвестная команда без упоминания — молчим
    logger.info(
        "group redirect: chat %s user %s cmd /%s",
        message.chat.id, message.from_user.id if message.from_user else None, name,
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="💬 Настроить в личном чате", url=setup_url(await bot_username(bot))
        ),
    ]])
    await message.reply(GROUP_REDIRECT, reply_markup=kb)
