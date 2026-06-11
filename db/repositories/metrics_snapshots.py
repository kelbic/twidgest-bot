"""Персистентные снапшоты счётчиков себестоимости (для /costs)."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, select

from db.models import MetricsSnapshot
from db.session import session_maker

_KEEP_DAYS = 90


async def save_snapshot(totals: dict[str, int]) -> None:
    """Пишет снапшот и самоочищается от строк старше _KEEP_DAYS."""
    async with session_maker()() as session:
        session.add(MetricsSnapshot(
            tw_api_calls=totals.get("tw_api_calls", 0),
            tw_cache_hits=totals.get("tw_cache_hits", 0),
            llm_calls=totals.get("llm_calls", 0),
            llm_tokens_in=totals.get("llm_tokens_in", 0),
            llm_tokens_out=totals.get("llm_tokens_out", 0),
        ))
        await session.execute(delete(MetricsSnapshot).where(
            MetricsSnapshot.created_at < datetime.utcnow() - timedelta(days=_KEEP_DAYS)
        ))
        await session.commit()


async def deltas_since(since: datetime) -> dict[str, int] | None:
    """Суммы положительных дельт между соседними снапшотами окна."""
    fields = ("tw_api_calls", "tw_cache_hits", "llm_calls",
              "llm_tokens_in", "llm_tokens_out")
    async with session_maker()() as session:
        result = await session.execute(
            select(MetricsSnapshot)
            .where(MetricsSnapshot.created_at > since)
            .order_by(MetricsSnapshot.created_at.asc())
        )
        rows = list(result.scalars().all())
    if len(rows) < 2:
        return None
    out = {f: 0 for f in fields}
    for prev, cur in zip(rows, rows[1:]):
        for f in fields:
            pv, cv = getattr(prev, f), getattr(cur, f)
            out[f] += cv if cv < pv else cv - pv
    out["hours"] = max(
        1, round((rows[-1].created_at - rows[0].created_at).total_seconds() / 3600))
    return out
