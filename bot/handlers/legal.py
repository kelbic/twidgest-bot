"""Пер-канальный юр-фильтр слоя A (RF-риски): /setlegal.

Слой B+C (наркотики, мед.дозировки) не отключается никогда — это не настройка.
Слой A (дискредитация ВС РФ и т.п.) включён по умолчанию; владелец канала может
отключить его ОСОЗНАННО: двухшаговое подтверждение, факт и время отказа пишутся
в БД (legal_optout_at) как аудит-след согласия.
"""
from __future__ import annotations

import html
import logging
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select

from db.models import Channel
from db.session import session_maker
from prompts import SAFETY_LEGAL_RF

logger = logging.getLogger(__name__)
router = Router(name="legal")

_DISCLAIMER = (
    "⚠️ <b>Отключение юр-фильтра — на твоей ответственности.</b>\n\n"
    "Фильтр отсеивает контент с риском по российскому законодательству "
    "(дискредитация ВС РФ и т.п.). Бот — не юрист, фильтр — не гарантия, "
    "но его отключение осознанно повышает риски владельца канала. "
    "Факт отключения и его время сохраняются.\n\n"
    "Фильтр наркотиков и медицинских дозировок не отключается."
)


async def _owned(session, uid: int, channel_id: int) -> Channel | None:
    result = await session.execute(
        select(Channel).where(Channel.id == channel_id, Channel.user_id == uid)
    )
    return result.scalar_one_or_none()


def _status_kb(ch: Channel) -> InlineKeyboardMarkup:
    if ch.legal_rf_filter:
        btn = InlineKeyboardButton(
            text="⚠️ Отключить юр-фильтр…", callback_data=f"legaloff:{ch.id}")
    else:
        btn = InlineKeyboardButton(
            text="✅ Включить юр-фильтр", callback_data=f"legalon:{ch.id}")
    return InlineKeyboardMarkup(inline_keyboard=[[btn]])


def _status_text(ch: Channel) -> str:
    state = "🟢 ВКЛЮЧЁН" if ch.legal_rf_filter else "🔴 ОТКЛЮЧЁН (под твою ответственность)"
    return (
        f"⚖️ <b>Юр-фильтр канала «{html.escape(ch.title or '')}»</b> (id={ch.id}): {state}\n\n"
        f"Что отсеивает слой A:\n<pre>{SAFETY_LEGAL_RF}</pre>\n"
        f"Слой B+C (наркотики, мед.дозировки) активен всегда."
    )


@router.message(Command("setlegal"))
async def cmd_setlegal(message: Message, command: CommandObject) -> None:
    if message.from_user is None:
        return
    uid = message.from_user.id
    arg = (command.args or "").strip()

    async with session_maker()() as session:
        if not arg:
            result = await session.execute(
                select(Channel).where(Channel.user_id == uid))
            chans = list(result.scalars().all())
            if not chans:
                await message.answer("У тебя нет каналов.")
                return
            lines = ["⚖️ <b>Юр-фильтр (слой A, RF-риски) по каналам:</b>\n"]
            for ch in chans:
                state = "🟢 вкл" if ch.legal_rf_filter else "🔴 выкл"
                lines.append(f"  {state} — <b>{html.escape((ch.title or '')[:40])}</b> (id={ch.id})")
            lines.append("\nУправление: /setlegal &lt;id&gt;")
            await message.answer("\n".join(lines))
            return

        try:
            channel_id = int(arg.split()[0])
        except ValueError:
            await message.answer("Формат: /setlegal <id канала>")
            return
        ch = await _owned(session, uid, channel_id)
        if ch is None:
            await message.answer(f"Канал {channel_id} не найден или не твой.")
            return
        await message.answer(_status_text(ch), reply_markup=_status_kb(ch))


@router.callback_query(F.data.startswith("legaloff:"))
async def cb_legal_off_step1(callback: CallbackQuery) -> None:
    """Шаг 1: дисклеймер и явное подтверждение."""
    channel_id = int(callback.data.split(":", 1)[1])
    await callback.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="Подтверждаю: отключить, ответственность на мне",
            callback_data=f"legaloff2:{channel_id}"),
    ], [
        InlineKeyboardButton(text="Оставить включённым",
                             callback_data=f"legalkeep:{channel_id}"),
    ]])
    await callback.message.answer(_DISCLAIMER, reply_markup=kb)


@router.callback_query(F.data.startswith("legaloff2:"))
async def cb_legal_off_step2(callback: CallbackQuery) -> None:
    if callback.from_user is None:
        return
    channel_id = int(callback.data.split(":", 1)[1])
    async with session_maker()() as session:
        ch = await _owned(session, callback.from_user.id, channel_id)
        if ch is None:
            await callback.answer("Канал не найден или не твой", show_alert=True)
            return
        ch.legal_rf_filter = False
        ch.legal_optout_at = datetime.utcnow()
        await session.commit()
        logger.warning(
            "legal: layer-A filter DISABLED for channel %d by owner %d at %s",
            channel_id, callback.from_user.id, ch.legal_optout_at,
        )
        await callback.answer("Отключён")
        await callback.message.edit_text(
            f"🔴 Юр-фильтр канала «{html.escape(ch.title or '')}» отключён "
            f"({ch.legal_optout_at:%d.%m.%Y %H:%M} UTC, зафиксировано). "
            f"Включить обратно: /setlegal {ch.id}")


@router.callback_query(F.data.startswith("legalon:"))
async def cb_legal_on(callback: CallbackQuery) -> None:
    if callback.from_user is None:
        return
    channel_id = int(callback.data.split(":", 1)[1])
    async with session_maker()() as session:
        ch = await _owned(session, callback.from_user.id, channel_id)
        if ch is None:
            await callback.answer("Канал не найден или не твой", show_alert=True)
            return
        ch.legal_rf_filter = True
        await session.commit()
        logger.info("legal: layer-A filter ENABLED for channel %d by owner %d",
                    channel_id, callback.from_user.id)
        await callback.answer("Включён")
        await callback.message.edit_text(
            f"🟢 Юр-фильтр канала «{html.escape(ch.title or '')}» включён.")


@router.callback_query(F.data.startswith("legalkeep:"))
async def cb_legal_keep(callback: CallbackQuery) -> None:
    await callback.answer("Оставили включённым")
    await callback.message.edit_text("🟢 Юр-фильтр остался включённым.")
