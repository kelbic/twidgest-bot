"""Скаут источников: /scout <channel_id> и колбэки HIL-карточки.

Поток:
  /scout 5  ИЛИ  кнопка «🔍 Подобрать новые источники» в health-алерте
  → discover_sources (LLM + превалидация по реальным твитам)
  → карточка с кнопками: ➕ per-кандидат / ✅ все / ✖️ не надо
  → добавление в ChannelSource только по нажатию владельца.

Кулдаун: если по каналу есть свежие (< SCOUT_COOLDOWN_HOURS) непримененные
предложения — показываем их повторно вместо нового платного поиска.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from config import Config
from core.llm_client import OpenRouterClient
from core.twitter_cache import TwitterCache
from core.twitter_client import TwitterClient
from db.models import Channel, ChannelSource, ScoutSuggestion
from db.repositories.users import is_tier_active
from db.session import session_maker
from tiers import get_limits
from workers.source_scout import discover_sources

logger = logging.getLogger(__name__)
router = Router()

_cfg = Config()
_twitter = TwitterClient(_cfg.twitter_api_key)
# Свой кэш-инстанс (хендлеры по конвенции проекта создают клиентов сами).
_cache = TwitterCache(_twitter, ttl_seconds=1800)
_llm = OpenRouterClient(_cfg.openrouter_api_key, _cfg.openrouter_model_default)

SCOUT_COOLDOWN_HOURS = 6
SUGGESTION_TTL_HOURS = 48  # кнопки старше — считаем протухшими


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _is_admin(uid: int) -> bool:
    return uid == _cfg.admin_user_id


async def _get_owned_channel(
    session: AsyncSession, uid: int, channel_id: int
) -> Channel | None:
    """Канал, если он существует и принадлежит uid (админу — любой)."""
    result = await session.execute(
        select(Channel)
        .where(Channel.id == channel_id)
        .options(
            selectinload(Channel.channel_sources),
            selectinload(Channel.user),
        )
    )
    channel = result.scalar_one_or_none()
    if channel is None:
        return None
    if channel.user_id != uid and not _is_admin(uid):
        return None
    return channel


async def _active_sources_count(session: AsyncSession, owner_id: int) -> int:
    result = await session.execute(
        select(sa_func.count(ChannelSource.id))
        .join(Channel, ChannelSource.channel_id == Channel.id)
        .where(
            Channel.user_id == owner_id,
            ChannelSource.is_active == True,  # noqa: E712
        )
    )
    return int(result.scalar_one() or 0)


async def _source_limit_reached(
    session: AsyncSession, channel: Channel
) -> tuple[bool, int, int]:
    """(достигнут ли лимит, текущее число, лимит) для владельца канала."""
    user = channel.user
    active = await is_tier_active(user)
    effective_tier = user.tier if active else "free"
    limits = get_limits(effective_tier)
    count = await _active_sources_count(session, channel.user_id)
    return count >= limits.max_sources, count, limits.max_sources


def _render_card(
    channel: Channel, suggestions: list[ScoutSuggestion]
) -> tuple[str, InlineKeyboardMarkup]:
    lines = [
        f"🔍 <b>Скаут: кандидаты для «{channel.title}»</b>\n",
        "Проверил последние твиты каждого — это реальные метрики, "
        "не обещания:\n",
    ]
    for i, s in enumerate(suggestions, 1):
        reason = f" — {s.reason}" if s.reason else ""
        lines.append(f"{i}. <b>@{s.username}</b>{reason}")
        lines.append(f"   <i>{s.stats}</i>")
    lines.append(
        "\n«Прошло бы фильтр» — сколько из последних твитов автора "
        "удовлетворяют порогам именно этого канала."
    )

    rows = [
        [InlineKeyboardButton(
            text=f"➕ @{s.username}", callback_data=f"scoutadd:{s.id}",
        )]
        for s in suggestions
    ]
    rows.append([
        InlineKeyboardButton(
            text="✅ Добавить все", callback_data=f"scoutall:{channel.id}",
        ),
        InlineKeyboardButton(
            text="✖️ Не надо", callback_data=f"scoutno:{channel.id}",
        ),
    ])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def _fresh_pending(
    session: AsyncSession, channel_id: int, hours: int
) -> list[ScoutSuggestion]:
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    result = await session.execute(
        select(ScoutSuggestion)
        .where(
            ScoutSuggestion.channel_id == channel_id,
            ScoutSuggestion.created_at > cutoff,
            ScoutSuggestion.applied_at.is_(None),
        )
        .order_by(ScoutSuggestion.id.asc())
    )
    return list(result.scalars().all())


async def _run_scout_flow(status_msg: Message, uid: int, channel_id: int) -> None:
    """Общий путь для /scout и кнопки из health-алерта.

    status_msg — сообщение, которое редактируем по ходу работы.
    """
    async with session_maker()() as session:
        channel = await _get_owned_channel(session, uid, channel_id)
        if channel is None:
            await status_msg.edit_text(
                f"⚠️ Канал {channel_id} не найден или не твой."
            )
            return

        # Кулдаун: свежие непримененные предложения показываем повторно,
        # не тратя деньги на новый поиск.
        pending = await _fresh_pending(session, channel_id, SCOUT_COOLDOWN_HOURS)
        if pending:
            text, kb = _render_card(channel, pending)
            await status_msg.edit_text(
                text + "\n\n<i>(результаты недавнего поиска — новый можно "
                f"запустить через {SCOUT_COOLDOWN_HOURS} ч после этого)</i>",
                reply_markup=kb,
                disable_web_page_preview=True,
            )
            return

    await status_msg.edit_text(
        "🔍 Ищу авторов по теме и проверяю их последние твиты... "
        "Это займёт до минуты."
    )

    # Дорогая часть — вне сессии БД
    try:
        stats = await discover_sources(channel, _llm, _cache)
    except Exception:
        logger.exception("scout: discover failed for channel %d", channel_id)
        await status_msg.edit_text(
            "❌ Скаут упал на поиске. Попробуй ещё раз позже."
        )
        return

    if not stats:
        await status_msg.edit_text(
            f"😕 Не нашёл подходящих новых авторов для «{channel.title}».\n\n"
            "Кандидаты были, но не прошли проверку: либо постят медиа без "
            "текста, либо их твиты не дотягивают до порогов канала "
            f"(min_likes={channel.min_likes}).\n\n"
            "Можно смягчить пороги (<code>/setfilter</code>) и запустить "
            "скаута снова, или добавить автора вручную: "
            f"<code>/addsource {channel.id} @username</code>"
        )
        return

    async with session_maker()() as session:
        # Старые непримененные предложения по каналу чистим — карточка одна
        old = await _fresh_pending(session, channel_id, SUGGESTION_TTL_HOURS)
        for row in old:
            await session.delete(row)

        rows = [
            ScoutSuggestion(
                channel_id=channel.id,
                user_id=channel.user_id,
                username=c.username,
                reason=c.reason,
                stats=c.stats_line(),
            )
            for c in stats
        ]
        session.add_all(rows)
        await session.commit()
        for row in rows:
            await session.refresh(row)

        text, kb = _render_card(channel, rows)

    await status_msg.edit_text(text, reply_markup=kb, disable_web_page_preview=True)


async def _apply_suggestion(
    session: AsyncSession,
    suggestion: ScoutSuggestion,
    channel: Channel,
    existing: set[str],
) -> str:
    """Добавляет один источник. Возвращает строку результата для юзера.

    existing — set lowercase-имен уже подключённых источников; поддерживается
    вызывающим кодом, чтобы «Добавить все» не вставлял дубли в одной сессии.
    """
    uname = suggestion.username.lower()
    if uname in existing:
        suggestion.applied_at = datetime.utcnow()
        return f"⚠️ @{suggestion.username} уже в канале"

    reached, count, limit = await _source_limit_reached(session, channel)
    if reached:
        return (
            f"❌ Лимит источников тарифа: {count}/{limit}. "
            f"@{suggestion.username} не добавлен"
        )

    session.add(ChannelSource(
        channel_id=channel.id,
        twitter_username=suggestion.username,
        source_type="twitter",
        is_active=True,
    ))
    await session.flush()  # чтобы _active_sources_count видел вставку
    suggestion.applied_at = datetime.utcnow()
    existing.add(uname)
    return f"✅ @{suggestion.username} добавлен"


# --------------------------------------------------------------------------- #
# Command + callbacks
# --------------------------------------------------------------------------- #


@router.message(Command("scout"))
async def cmd_scout(message: Message, command: CommandObject) -> None:
    if message.from_user is None:
        return
    args = (command.args or "").split()
    if not args:
        await message.answer(
            "Скаут подбирает новые X-источники под тему канала и проверяет "
            "их по реальным твитам.\n\n"
            "Использование: <code>/scout &lt;channel_id&gt;</code>\n"
            "ID каналов: <code>/channels</code>"
        )
        return
    try:
        channel_id = int(args[0])
    except ValueError:
        await message.answer("❌ ID канала должен быть числом.")
        return

    status_msg = await message.answer("🔍 Запускаю скаута...")
    await _run_scout_flow(status_msg, message.from_user.id, channel_id)


@router.callback_query(F.data.startswith("scoutrun:"))
async def cb_scout_run(callback: CallbackQuery) -> None:
    """Кнопка «Подобрать новые источники» из health-алерта."""
    if callback.from_user is None or callback.message is None:
        return
    try:
        channel_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Битые данные кнопки", show_alert=True)
        return

    await callback.answer("Запускаю скаута…")
    status_msg = await callback.message.answer("🔍 Запускаю скаута...")
    await _run_scout_flow(status_msg, callback.from_user.id, channel_id)


@router.callback_query(F.data.startswith("scoutadd:"))
async def cb_scout_add(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.message is None:
        return
    try:
        sid = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Битые данные кнопки", show_alert=True)
        return

    uid = callback.from_user.id
    async with session_maker()() as session:
        suggestion = await session.get(ScoutSuggestion, sid)
        if suggestion is None:
            await callback.answer(
                "Предложение устарело — запусти /scout заново", show_alert=True
            )
            return
        if suggestion.user_id != uid and not _is_admin(uid):
            await callback.answer("Это не твой канал", show_alert=True)
            return
        if suggestion.applied_at is not None:
            await callback.answer("Уже добавлен")
            return

        channel = await _get_owned_channel(session, uid, suggestion.channel_id)
        if channel is None:
            await callback.answer("Канал не найден", show_alert=True)
            return

        existing = {
            s.twitter_username.lower() for s in channel.channel_sources
        }
        result = await _apply_suggestion(session, suggestion, channel, existing)
        await session.commit()

    await callback.answer(result, show_alert=result.startswith("❌"))
    if result.startswith("✅"):
        try:
            await callback.message.edit_text(
                (callback.message.html_text or "") + f"\n\n{result}",
                reply_markup=callback.message.reply_markup,
                disable_web_page_preview=True,
            )
        except Exception:
            pass  # текст не критичен, источник уже добавлен


@router.callback_query(F.data.startswith("scoutall:"))
async def cb_scout_all(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.message is None:
        return
    try:
        channel_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Битые данные кнопки", show_alert=True)
        return

    uid = callback.from_user.id
    async with session_maker()() as session:
        channel = await _get_owned_channel(session, uid, channel_id)
        if channel is None:
            await callback.answer("Канал не найден или не твой", show_alert=True)
            return

        pending = await _fresh_pending(session, channel_id, SUGGESTION_TTL_HOURS)
        if not pending:
            await callback.answer(
                "Предложения устарели — запусти /scout заново", show_alert=True
            )
            return

        existing = {
            s.twitter_username.lower() for s in channel.channel_sources
        }
        results = [
            await _apply_suggestion(session, s, channel, existing)
            for s in pending
        ]
        await session.commit()

    summary = "\n".join(results)
    await callback.answer("Готово")
    try:
        await callback.message.edit_text(
            (callback.message.html_text or "") + f"\n\n{summary}\n\n"
            "Бот начнёт собирать твиты новых источников в следующем "
            "цикле (до 30 мин).",
            reply_markup=None,
            disable_web_page_preview=True,
        )
    except Exception:
        await callback.message.answer(summary)


@router.callback_query(F.data.startswith("scoutno:"))
async def cb_scout_no(callback: CallbackQuery) -> None:
    if callback.message is None:
        return
    await callback.answer("Ок, не трогаю")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
