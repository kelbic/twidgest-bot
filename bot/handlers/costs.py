"""Админская экономика: /costs [days] — себестоимость по снапшотам метрик."""
from __future__ import annotations

from datetime import datetime, timedelta

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy import select

from config import Config
from core.plan import PRICE_STARS, channel_active
from db.models import Channel
from db.repositories.metrics_snapshots import deltas_since
from db.session import session_maker

router = Router(name="costs")

# Цены (USD). СВЕРЯЙ со своими тарифами: twitterapi.io и модель OpenRouter.
TW_USD_PER_1K_CALLS = 0.15
LLM_IN_PER_MTOK = 0.20
LLM_OUT_PER_MTOK = 0.80


@router.message(Command("costs"))
async def cmd_costs(message: Message, command: CommandObject) -> None:
    if message.from_user is None or message.from_user.id != Config().admin_user_id:
        return
    days = 7
    arg = (command.args or "").strip()
    if arg.isdigit():
        days = max(1, min(90, int(arg)))
    d = await deltas_since(datetime.utcnow() - timedelta(days=days))
    if d is None:
        await message.answer(
            f"Мало снапшотов за {days} дн. — они пишутся после каждого цикла "
            f"collector, подожди пару циклов.")
        return

    async with session_maker()() as session:
        result = await session.execute(select(Channel))
        active = sum(1 for c in result.scalars().all() if channel_active(c))

    usd = (d["tw_api_calls"] / 1000 * TW_USD_PER_1K_CALLS
           + d["llm_tokens_in"] / 1e6 * LLM_IN_PER_MTOK
           + d["llm_tokens_out"] / 1e6 * LLM_OUT_PER_MTOK)
    span_d = d["hours"] / 24
    per_ch_day = usd / span_d / active if (span_d and active) else 0
    cache_total = d["tw_api_calls"] + d["tw_cache_hits"]
    cache_pct = d["tw_cache_hits"] / cache_total * 100 if cache_total else 0

    await message.answer(
        f"💸 <b>Себестоимость Twidgest за ~{d['hours']} ч</b> (оценка)\n\n"
        f"Twitter API: {d['tw_api_calls']} запросов "
        f"(кэш сэкономил {d['tw_cache_hits']}, hit-rate {cache_pct:.0f}%)\n"
        f"LLM: {d['llm_calls']} вызовов, токены "
        f"{d['llm_tokens_in']/1000:.0f}k in / {d['llm_tokens_out']/1000:.0f}k out\n"
        f"Итого: <b>${usd:.2f}</b> на {active} активных каналов\n\n"
        f"≈ <b>${per_ch_day:.3f}</b>/канал/день → "
        f"<b>${per_ch_day*30:.2f}</b>/канал/30д против {PRICE_STARS}⭐.\n"
        f"Токены копятся с этого деплоя; первые сутки цифры неполные.")
