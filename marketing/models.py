"""SQLAlchemy-модели marketing.db. Свой Base — никакой связи с прод-схемой."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# Воронка. Порядок важен для /stats.
FUNNEL_STATUSES = [
    "new", "enriched", "qualified", "approved",
    "contacted", "replied", "trial", "paid",
]
TERMINAL_STATUSES = ["dead", "optout"]
ALL_STATUSES = FUNNEL_STATUSES + TERMINAL_STATUSES
# Статусы, куда админ двигает лид руками через /mark
MANUAL_MARK_STATUSES = ["replied", "trial", "paid", "dead"]


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    subscribers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    niche: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="manual", nullable=False)
    contact: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="new", nullable=False, index=True)

    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    hook: Mapped[str | None] = mapped_column(Text, nullable=True)

    posts_per_week: Mapped[float | None] = mapped_column(Float, nullable=True)
    gap_cv: Mapped[float | None] = mapped_column(Float, nullable=True)
    # ГЛАВНЫЙ сигнал: тренд кадэнса recent_ppw/older_ppw. < 0.6 = выгорание.
    decay: Mapped[float | None] = mapped_column(Float, nullable=True)
    recent_ppw: Mapped[float | None] = mapped_column(Float, nullable=True)
    older_ppw: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_post_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    demo_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    draft_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    contacted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_followup_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    followups_sent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    card_pushed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class LeadEvent(Base):
    """Журнал: что и когда происходило с лидом."""

    __tablename__ = "lead_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id"), index=True, nullable=False)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class NicheDemo(Base):
    """Кэш демо-дайджестов ПО НИШЕ (не по лиду). TTL 7 дней."""

    __tablename__ = "niche_demos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    niche_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    demo_text: Mapped[str] = mapped_column(Text, nullable=False)
    sources_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Blacklist(Base):
    __tablename__ = "blacklist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    reason: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class DailyCounter(Base):
    """Персистентные дневные счётчики капов (LLM, демо, карточки).

    Переживают рестарт процесса — иначе рестарт обнулял бы бюджет.
    """

    __tablename__ = "daily_counters"
    __table_args__ = (UniqueConstraint("day", "name", name="uq_day_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    day: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD UTC
    name: Mapped[str] = mapped_column(String(32), nullable=False)
    count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Setting(Base):
    """KV-настройки процесса (например, paused=1)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(256), nullable=False, default="")
