"""Пер-канальные дельты LLM-затрат за проход воркера (для /costs)."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, func, select

from db.models import ChannelCost
from db.session import session_maker

_KEEP_DAYS = 90


async def save_channel_cost(channel_id: int, acc: dict[str, int]) -> None:
    """Пишет дельты прохода; пустые аккумуляторы не пишем. Самоочистка 90д."""
    if not any(acc.get(k) for k in ("llm_calls", "llm_tokens_in", "llm_tokens_out")):
        return
    async with session_maker()() as session:
        session.add(ChannelCost(
            channel_id=channel_id,
            llm_calls=acc.get("llm_calls", 0),
            llm_tokens_in=acc.get("llm_tokens_in", 0),
            llm_tokens_out=acc.get("llm_tokens_out", 0),
        ))
        await session.execute(delete(ChannelCost).where(
            ChannelCost.created_at < datetime.utcnow() - timedelta(days=_KEEP_DAYS)
        ))
        await session.commit()


async def llm_by_channel_since(since: datetime) -> dict[int, dict[str, int]]:
    """Суммы LLM-дельт по каналам за окно."""
    async with session_maker()() as session:
        result = await session.execute(
            select(
                ChannelCost.channel_id,
                func.sum(ChannelCost.llm_calls),
                func.sum(ChannelCost.llm_tokens_in),
                func.sum(ChannelCost.llm_tokens_out),
            ).where(ChannelCost.created_at > since)
            .group_by(ChannelCost.channel_id)
        )
        rows = result.all()
    return {int(cid): {"llm_calls": int(c or 0), "tin": int(ti or 0), "tout": int(to or 0)}
            for cid, c, ti, to in rows}
