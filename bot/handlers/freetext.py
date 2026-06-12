"""Текст как тема: новичок пишет сообщение — предлагаем создать канал.

Роутер подключается ПОСЛЕДНИМ: сюда падает только то, что не поймал
ни один другой хендлер. Гейты: личка, текст не-команда, не форвард.
Работает для ВСЕХ (раньше — только при нуле каналов, и тема владельца
канала молча проваливалась: кнопка qs:ai просила «напиши тему», а текст
игнорировался — баг верхней воронки). Отказ — одна кнопка «Не надо».
Создание — строго после кнопки-подтверждения.
"""
from __future__ import annotations

import logging
import time

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import func as sa_func, select

from db.models import Channel
from db.session import session_maker

logger = logging.getLogger(__name__)
router = Router(name="freetext")

# user_id -> (тема, ts). Память процесса: при рестарте карточки протухают,
# это нормально — юзер просто напишет тему ещё раз.
_pending: dict[int, tuple[str, float]] = {}
PENDING_TTL = 15 * 60
MAX_TOPIC_LEN = 200


async def _channels_count(user_id: int) -> int:
    async with session_maker()() as session:
        result = await session.execute(
            select(sa_func.count(Channel.id)).where(Channel.user_id == user_id)
        )
        return int(result.scalar_one() or 0)


@router.message(
    F.chat.type == "private",
    F.text,
    ~F.text.startswith("/"),
    F.forward_origin.is_(None),
)
async def freetext_as_topic(message: Message) -> None:
    if message.from_user is None or message.text is None:
        return
    n_channels = await _channels_count(message.from_user.id)

    topic = message.text.strip()[:MAX_TOPIC_LEN]
    if len(topic) < 10:
        await message.answer(
            "Похоже на тему канала, но коротковато — опиши чуть подробнее "
            "(минимум 10 символов).\n"
            "Например: <i>«новости электромобилей и зарядной инфраструктуры»</i>\n\n"
            "Все команды: /help"
        )
        return

    _pending[message.from_user.id] = (topic, time.monotonic())
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🚀 Создать", callback_data="ft:go"),
        InlineKeyboardButton(text="✖️ Не надо", callback_data="ft:no"),
    ]])
    extra = " ещё один" if n_channels else ""
    await message.answer(
        f"Создать{extra} канал по теме <b>«{topic}»</b>?\n\n"
        f"Я найду авторов в X, проверю каждого по его реальным твитам "
        f"и соберу канал — займёт около минуты.",
        reply_markup=kb,
    )


@router.callback_query(F.data == "ft:go")
async def cb_freetext_go(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.message is None:
        return
    entry = _pending.pop(callback.from_user.id, None)
    if entry is None or time.monotonic() - entry[1] > PENDING_TTL:
        await callback.answer(
            "Карточка устарела — напиши тему ещё раз", show_alert=True
        )
        return
    topic = entry[0]
    await callback.answer("Поехали!")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    from bot.handlers.channels import _create_with_ai
    await _create_with_ai(callback.message, topic, user_id=callback.from_user.id)


@router.callback_query(F.data == "ft:no")
async def cb_freetext_no(callback: CallbackQuery) -> None:
    if callback.from_user is not None:
        _pending.pop(callback.from_user.id, None)
    await callback.answer("Ок")
    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
