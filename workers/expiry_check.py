"""Раз в день: жизненный цикл слотов (этап C4).

Три окна, каждое шириной 24ч = интервалу джобы, поэтому каждый канал
попадает в каждое окно ровно один раз — дедупликация без флагов в БД:

1. Оплата кончается через 12-36ч  -> напоминание с кнопкой продления.
2. Триал кончается через 24-48ч   -> «чек» 5-го дня: посты за триал,
   оценка сэкономленного времени, кнопка оплаты. Конвертящее сообщение.
3. Канал замолк за последние 24ч  -> уведомление + кнопка реактивации.

Админские каналы не трогаем (channel_status == 'admin').
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import and_, func, select

from core.plan import PRICE_STARS, TRIAL_DAYS, _ADMIN_ID, channel_status
from db.models import Channel, PostLog
from db.session import session_maker

logger = logging.getLogger(__name__)

# Оценка ручной работы на один пост: найти твит, перевести, отредактировать
MINUTES_PER_POST = 12


def _hours_saved(posts: int) -> int:
    return max(1, round(posts * MINUTES_PER_POST / 60)) if posts else 0


def _pay_kb(channel_id: int, verb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text=f"💳 {verb} — {PRICE_STARS}⭐ / 30 дней",
            callback_data=f"payslot:{channel_id}",
        )
    ]])


async def _posts_since(session, channel_id: int, since: datetime) -> int:
    result = await session.execute(
        select(func.count(PostLog.id)).where(
            and_(PostLog.target_id == channel_id, PostLog.posted_at > since)
        )
    )
    return int(result.scalar_one() or 0)


async def _safe_send(bot: Bot, uid: int, text: str, kb) -> bool:
    try:
        await bot.send_message(uid, text, reply_markup=kb)
        return True
    except Exception as exc:
        logger.warning("expiry: failed to notify %d: %s", uid, exc)
        return False


ARCHIVE_AFTER_DAYS = 7  # через сколько дней неактивности канал уходит в архив


async def run_expiry_check(bot: Bot) -> None:
    logger.info("=== Expiry check started (slot model) ===")
    now = datetime.utcnow()
    reminded = trial_checked = silenced = archived = 0

    async with session_maker()() as session:
        result = await session.execute(
            select(Channel).where(Channel.user_id != _ADMIN_ID)
        )
        channels = list(result.scalars().all())

        for ch in channels:
            # 1) Оплата кончается через 12-36ч
            if ch.paid_until and now + timedelta(hours=12) < ch.paid_until <= now + timedelta(hours=36):
                ok = await _safe_send(
                    bot, ch.user_id,
                    f"⏰ Оплата канала <b>«{ch.title}»</b> заканчивается "
                    f"<b>{ch.paid_until:%d.%m %H:%M} UTC</b>.\n\n"
                    f"После этого публикации остановятся. Продление добавит "
                    f"30 дней к текущей дате — ничего не сгорит.",
                    _pay_kb(ch.id, "Продлить"),
                )
                reminded += ok

            # 2) Чек 5-го дня триала (осталось 24-48ч)
            elif ch.trial_until and now + timedelta(hours=24) < ch.trial_until <= now + timedelta(hours=48):
                trial_start = ch.trial_until - timedelta(days=TRIAL_DAYS)
                posts = await _posts_since(session, ch.id, trial_start)
                hours = _hours_saved(posts)
                if posts > 0:
                    body = (
                        f"За триал канал <b>«{ch.title}»</b> опубликовал "
                        f"<b>{posts} постов</b> — вручную это ~{hours} ч работы: "
                        f"найти твиты, перевести, отредактировать, выложить.\n\n"
                    )
                else:
                    body = (
                        f"Канал <b>«{ch.title}»</b> пока не опубликовал ни одного "
                        f"поста — похоже, он не привязан. Перешли мне любое "
                        f"сообщение из канала, и публикации начнутся.\n\n"
                    )
                ok = await _safe_send(
                    bot, ch.user_id,
                    f"👋 Триал заканчивается через 2 дня "
                    f"({ch.trial_until:%d.%m %H:%M} UTC).\n\n" + body +
                    f"Дальше — {PRICE_STARS}⭐ за 30 дней. Без оплаты канал "
                    f"просто замолчит, удалять ничего не нужно.",
                    _pay_kb(ch.id, "Оплатить"),
                )
                trial_checked += ok

            # 3) Замолк за последние 24ч
            else:
                ends = [d for d in (ch.paid_until, ch.trial_until) if d]
                last_end = max(ends) if ends else None
                if last_end and now - timedelta(hours=24) < last_end <= now \
                        and channel_status(ch) == "inactive":
                    ok = await _safe_send(
                        bot, ch.user_id,
                        f"🔇 Канал <b>«{ch.title}»</b> остановлен — период "
                        f"закончился {last_end:%d.%m %H:%M} UTC.\n\n"
                        f"Источники, настройки и история целы. Оплата вернёт "
                        f"публикации в течение получаса.",
                        _pay_kb(ch.id, "Активировать"),
                    )
                    silenced += ok

            # 4) Архивация: неактивен >ARCHIVE_AFTER_DAYS и ещё не в архиве.
            #    Опора на max(paid_until, trial_until, created_at) — устойчиво к
            #    NULL-датам (каналы без проставленного триала уходят по created_at).
            #    Тихо: владелец уже получил уведомление в окне замолкания.
            if ch.archived_at is None and channel_status(ch) == "inactive":
                ends = [d for d in (ch.paid_until, ch.trial_until, ch.created_at) if d]
                ref = max(ends) if ends else None
                if ref and ref <= now - timedelta(days=ARCHIVE_AFTER_DAYS):
                    ch.archived_at = now
                    archived += 1
                    logger.info(
                        "expiry: channel %d archived (inactive since %s)",
                        ch.id, ref.strftime("%Y-%m-%d"),
                    )

        await session.commit()

    logger.info(
        "=== Expiry check done. Reminded: %d, trial-checks: %d, silenced: %d, "
        "archived: %d ===",
        reminded, trial_checked, silenced, archived,
    )
