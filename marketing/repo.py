"""Запросы к marketing.db. Все функции принимают AsyncSession."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from marketing.models import (
    ALL_STATUSES,
    Blacklist,
    DailyCounter,
    Lead,
    LeadEvent,
    NicheDemo,
    Setting,
)

logger = logging.getLogger(__name__)

NICHE_DEMO_TTL_DAYS = 7


def _utcnow() -> datetime:
    return datetime.utcnow()


def _today() -> str:
    return _utcnow().strftime("%Y-%m-%d")


# --------------------------------------------------------------------------- #
# Leads
# --------------------------------------------------------------------------- #

async def add_lead(
    session: AsyncSession,
    username: str,
    niche: str | None = None,
    source: str = "manual",
) -> tuple[Lead | None, str]:
    """Добавляет лид. Возвращает (lead|None, что_случилось).

    Дедуп по channel_username: повторный импорт — no-op с записью в журнал.
    Blacklist-фильтр: заблокированные не добавляются.
    """
    username = username.lstrip("@").lower()

    if await is_blacklisted(session, username):
        return None, "blacklisted"

    existing = await get_lead_by_username(session, username)
    if existing is not None:
        await log_event(session, existing.id, "import_duplicate", source)
        await session.commit()
        return existing, "duplicate"

    lead = Lead(channel_username=username, niche=niche, source=source, status="new")
    session.add(lead)
    await session.flush()
    await log_event(session, lead.id, "created", json.dumps({"source": source, "niche": niche}))
    await session.commit()
    return lead, "created"


async def get_lead(session: AsyncSession, lead_id: int) -> Lead | None:
    return await session.get(Lead, lead_id)


async def get_lead_by_username(session: AsyncSession, username: str) -> Lead | None:
    result = await session.execute(
        select(Lead).where(Lead.channel_username == username.lstrip("@").lower())
    )
    return result.scalar_one_or_none()


async def get_leads_by_status(
    session: AsyncSession, status: str, limit: int = 50
) -> list[Lead]:
    result = await session.execute(
        select(Lead).where(Lead.status == status).order_by(Lead.id).limit(limit)
    )
    return list(result.scalars().all())


async def get_new_leads_batch(session: AsyncSession, limit: int = 5) -> list[Lead]:
    result = await session.execute(
        select(Lead).where(Lead.status == "new").order_by(Lead.id).limit(limit)
    )
    return list(result.scalars().all())


async def get_next_queued_lead(session: AsyncSession) -> Lead | None:
    """Следующий qualified-лид для карточки: лучший скор вперёд."""
    result = await session.execute(
        select(Lead)
        .where(Lead.status == "qualified")
        .order_by(Lead.score.desc().nullslast(), Lead.id)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_unpushed_cards(session: AsyncSession, limit: int) -> list[Lead]:
    """Лиды, ждущие карточку: qualified + enriched-без-превью (оценить руками)."""
    result = await session.execute(
        select(Lead)
        .where(
            Lead.status.in_(["qualified", "enriched"]),
            Lead.card_pushed_at == None,  # noqa: E711
        )
        .order_by(Lead.score.desc().nullslast(), Lead.id)
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_due_followups(session: AsyncSession, now: datetime | None = None) -> list[Lead]:
    now = now or _utcnow()
    result = await session.execute(
        select(Lead).where(
            Lead.status == "contacted",
            Lead.next_followup_at != None,  # noqa: E711
            Lead.next_followup_at <= now,
        )
    )
    return list(result.scalars().all())


async def set_status(
    session: AsyncSession,
    lead: Lead,
    status: str,
    reason: str | None = None,
    commit: bool = True,
) -> None:
    if status not in ALL_STATUSES:
        raise ValueError(f"Unknown status: {status}")
    old = lead.status
    lead.status = status
    await log_event(
        session, lead.id, "status_change",
        json.dumps({"from": old, "to": status, "reason": reason}, ensure_ascii=False),
    )
    if commit:
        await session.commit()


async def count_by_status(session: AsyncSession) -> dict[str, int]:
    result = await session.execute(
        select(Lead.status, func.count(Lead.id)).group_by(Lead.status)
    )
    return {status: cnt for status, cnt in result.all()}


async def all_leads(session: AsyncSession) -> list[Lead]:
    result = await session.execute(select(Lead).order_by(Lead.id))
    return list(result.scalars().all())


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #

async def log_event(
    session: AsyncSession, lead_id: int, event: str, payload: str | None = None
) -> None:
    session.add(LeadEvent(lead_id=lead_id, event=event, payload=payload))


async def get_last_event(
    session: AsyncSession, lead_id: int, event: str
) -> LeadEvent | None:
    result = await session.execute(
        select(LeadEvent)
        .where(LeadEvent.lead_id == lead_id, LeadEvent.event == event)
        .order_by(LeadEvent.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def funnel_reached(session: AsyncSession, statuses: list[str]) -> dict[str, int]:
    """Сколько РАЗНЫХ лидов когда-либо доходило до каждого статуса.

    Считаем по журналу status_change, а не по текущим статусам: лид,
    умерший после contacted, всё равно прошёл через contacted — иначе
    конверсии в /stats врут (знаменатель тает по мере смертей)."""
    result = await session.execute(
        select(LeadEvent.lead_id, LeadEvent.payload)
        .where(LeadEvent.event == "status_change")
    )
    reached: dict[str, set[int]] = {s: set() for s in statuses}
    for lead_id, payload in result.all():
        try:
            to_status = json.loads(payload or "{}").get("to")
        except ValueError:
            continue
        if to_status in reached:
            reached[to_status].add(lead_id)
    return {s: len(ids) for s, ids in reached.items()}


async def avg_response_hours(session: AsyncSession) -> float | None:
    """Среднее время contacted → replied по событиям status_change (90 дней —
    журнал растёт бесконечно, /stats не должен замедляться вместе с ним)."""
    since = _utcnow() - timedelta(days=90)
    result = await session.execute(
        select(LeadEvent)
        .where(LeadEvent.event == "status_change", LeadEvent.created_at >= since)
        .order_by(LeadEvent.id)
    )
    contacted_at: dict[int, datetime] = {}
    deltas: list[float] = []
    for ev in result.scalars().all():
        try:
            data = json.loads(ev.payload or "{}")
        except ValueError:
            continue
        if data.get("to") == "contacted":
            contacted_at[ev.lead_id] = ev.created_at
        elif data.get("to") == "replied" and ev.lead_id in contacted_at:
            deltas.append((ev.created_at - contacted_at.pop(ev.lead_id)).total_seconds() / 3600)
    if not deltas:
        return None
    return sum(deltas) / len(deltas)


# --------------------------------------------------------------------------- #
# Blacklist
# --------------------------------------------------------------------------- #

async def is_blacklisted(session: AsyncSession, username: str) -> bool:
    result = await session.execute(
        select(Blacklist).where(Blacklist.channel_username == username.lstrip("@").lower())
    )
    return result.scalar_one_or_none() is not None


async def blacklist_add(
    session: AsyncSession, username: str, reason: str, commit: bool = True
) -> None:
    username = username.lstrip("@").lower()
    if not await is_blacklisted(session, username):
        session.add(Blacklist(channel_username=username, reason=reason[:256]))
    if commit:
        await session.commit()


# --------------------------------------------------------------------------- #
# Niche demos
# --------------------------------------------------------------------------- #

async def get_fresh_demo(session: AsyncSession, niche_key: str) -> NicheDemo | None:
    """Демо не старше TTL. Старше — считается протухшим (не показывать)."""
    result = await session.execute(
        select(NicheDemo).where(NicheDemo.niche_key == niche_key)
    )
    demo = result.scalar_one_or_none()
    if demo is None:
        return None
    if demo.created_at and demo.created_at < _utcnow() - timedelta(days=NICHE_DEMO_TTL_DAYS):
        return None
    return demo


async def save_demo(
    session: AsyncSession, niche_key: str, demo_text: str, sources: list[str]
) -> NicheDemo:
    result = await session.execute(
        select(NicheDemo).where(NicheDemo.niche_key == niche_key)
    )
    demo = result.scalar_one_or_none()
    if demo is None:
        demo = NicheDemo(niche_key=niche_key)
        session.add(demo)
    demo.demo_text = demo_text
    demo.sources_json = json.dumps(sources, ensure_ascii=False)
    demo.created_at = _utcnow()
    await session.commit()
    return demo


# --------------------------------------------------------------------------- #
# Daily counters (капы) и настройки
# --------------------------------------------------------------------------- #

async def try_consume(
    session: AsyncSession, name: str, cap: int, amount: int = 1
) -> bool:
    """Атомарно потребляет `amount` из дневного капа. False = кап исчерпан.

    UPSERT + условный UPDATE одним стейтментом каждый: конкурентные корутины
    (воркер и колбэк админа) не могут ни задвоить строку дня, ни оба пройти
    проверку на последнем слоте.
    """
    day = _today()
    await session.execute(
        sqlite_insert(DailyCounter)
        .values(day=day, name=name, count=0)
        .on_conflict_do_nothing(index_elements=["day", "name"])
    )
    result = await session.execute(
        update(DailyCounter)
        .where(
            DailyCounter.day == day,
            DailyCounter.name == name,
            DailyCounter.count + amount <= cap,
        )
        .values(count=DailyCounter.count + amount)
    )
    await session.commit()
    return result.rowcount > 0


async def get_counter(session: AsyncSession, name: str) -> int:
    result = await session.execute(
        select(DailyCounter.count).where(
            DailyCounter.day == _today(), DailyCounter.name == name
        )
    )
    value = result.scalar_one_or_none()
    return value or 0


async def get_setting(session: AsyncSession, key: str, default: str = "") -> str:
    setting = await session.get(Setting, key)
    return setting.value if setting else default


async def set_setting(session: AsyncSession, key: str, value: str) -> None:
    setting = await session.get(Setting, key)
    if setting is None:
        session.add(Setting(key=key, value=value))
    else:
        setting.value = value
    await session.commit()
