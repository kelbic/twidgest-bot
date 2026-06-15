"""Админская экономика: /costs [days] — пер-канальная себестоимость.

LLM — точная атрибуция (channel_costs, скоуп вокруг обработки канала).
Twitter — глобальные дельты снапшотов в ТВИТАХ (биллинг twitterapi.io:
$0.15/1k возвращённых твитов, минимум 15 кредитов/вызов), раскиданные
по каналам пропорционально их активным источникам — источники шарятся,
точнее не атрибутировать. Фикс-косты (VPS, время) сюда не входят.
"""
from __future__ import annotations

import html
from datetime import datetime, timedelta

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from config import Config
from core.plan import PRICE_STARS, channel_active
from db.models import Channel
from db.repositories.channel_costs import llm_by_channel_since
from core.pricing import llm_usd_per_mtok, tw_usd_per_tweet
from db.repositories.metrics_snapshots import deltas_since
from db.session import session_maker

router = Router(name="costs")

# Цены живые: LLM — с OpenRouter по сконфигурированной модели (кэш сутки),
# twitter — env TW_USD_PER_TWEET или дефолт. См. core/pricing.py.


@router.message(Command("costs"))
async def cmd_costs(message: Message, command: CommandObject) -> None:
    if message.from_user is None or message.from_user.id != Config().admin_user_id:
        return
    days = 7
    arg = (command.args or "").strip()
    if arg.isdigit():
        days = max(1, min(90, int(arg)))
    since = datetime.utcnow() - timedelta(days=days)

    g = await deltas_since(since)               # глобальные дельты (snapshots)
    per_ch = await llm_by_channel_since(since)  # LLM по каналам (точно)
    cfg = Config()
    LLM_IN_PER_MTOK, LLM_OUT_PER_MTOK, price_src = await llm_usd_per_mtok(
        cfg.openrouter_model_default)
    TW_USD_PER_TWEET = tw_usd_per_tweet()

    async with session_maker()() as session:
        result = await session.execute(
            select(Channel).options(selectinload(Channel.channel_sources)))
        channels = list(result.scalars().all())
    active = {c.id: c for c in channels if channel_active(c)}
    src_counts = {
        cid: sum(1 for s in ch.channel_sources if s.is_active)
        for cid, ch in active.items()
    }
    total_src = sum(src_counts.values()) or 1

    tw_usd_total = 0.0
    tw_line = "Twitter: данных пока нет (tw_tweets копится с деплоя v2)"
    if g:
        billed_units = max(g.get("tw_tweets", 0), g.get("tw_api_calls", 0))
        tw_usd_total = billed_units * TW_USD_PER_TWEET
        hit = g["tw_cache_hits"]
        denom = g["tw_api_calls"] + hit
        tw_line = (f"Twitter: {g.get('tw_tweets', 0)} твитов / "
                   f"{g['tw_api_calls']} вызовов → ${tw_usd_total:.2f} "
                   f"(кэш hit-rate {hit / denom * 100 if denom else 0:.0f}%)")

    span_d = (g["hours"] / 24) if g else days
    lines = [f"💸 <b>Себестоимость Twidgest за ~{days} дн.</b>\n", tw_line, ""]
    grand = tw_usd_total

    for cid, ch in sorted(active.items()):
        llm = per_ch.get(cid, {"llm_calls": 0, "tin": 0, "tout": 0})
        llm_usd = (llm["tin"] / 1e6 * LLM_IN_PER_MTOK
                   + llm["tout"] / 1e6 * LLM_OUT_PER_MTOK)
        tw_share = tw_usd_total * src_counts.get(cid, 0) / total_src
        ch_usd = llm_usd + tw_share
        grand += llm_usd
        per_day = ch_usd / span_d if span_d else 0
        lines.append(
            f"📣 <b>{html.escape((ch.title or '')[:36])}</b> (id={cid}, источников {src_counts.get(cid, 0)})\n"
            f"   LLM: {llm['llm_calls']} вызовов, "
            f"{llm['tin']/1000:.0f}k/{llm['tout']/1000:.0f}k ток. → ${llm_usd:.3f}\n"
            f"   + доля Twitter ${tw_share:.3f} = <b>${ch_usd:.2f}</b> "
            f"(${per_day:.3f}/день, ${per_day*30:.2f}/30д против {PRICE_STARS}⭐)")

    lines.append(
        f"\nИтого переменные затраты: <b>${grand:.2f}</b>. "
        f"Фикс (VPS, Anthropic для essayist, твоё время) — отдельно.\n"
        f"Цены LLM: {price_src} ({cfg.openrouter_model_default}, "
        f"${LLM_IN_PER_MTOK:.2f}/${LLM_OUT_PER_MTOK:.2f} за Mtok).")
    await message.answer("\n".join(lines))
