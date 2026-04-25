"""SQLAlchemy-модели для multi-tenant SaaS с поддержкой Channel."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    tg_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tg_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tier: Mapped[str] = mapped_column(String(16), default="free", nullable=False)
    tier_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # Старые relationships оставляем для совместимости (миграция в следующей итерации)
    sources: Mapped[list["Source"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    targets: Mapped[list["Target"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    settings: Mapped["UserSettings | None"] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    # Новое
    channels: Mapped[list["Channel"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Channel(Base):
    """Канал = тема + список источников + target + настройки.

    Юзер может иметь несколько Channel (например, 'AI новости' и 'крикет').
    Каждый Channel постит в свой target в своём режиме.
    """

    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.tg_user_id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(255))  # человеческое название "AI Daily"
    niche: Mapped[str] = mapped_column(String(32), default="general")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    template_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 'cricket', 'ai-news' или null если AI-generated

    # Куда постить
    target_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    target_chat_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mode: Mapped[str] = mapped_column(String(16), default="digest")  # single | digest

    # Фильтры (могут переопределять глобальные UserSettings)
    min_likes: Mapped[int] = mapped_column(Integer, default=200)
    min_retweets: Mapped[int] = mapped_column(Integer, default=20)
    skip_replies: Mapped[bool] = mapped_column(Boolean, default=True)

    # Расписание
    digest_interval_hours: Mapped[int] = mapped_column(Integer, default=12)
    digest_max_tweets: Mapped[int] = mapped_column(Integer, default=7)

    # Статус
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="channels")
    channel_sources: Mapped[list["ChannelSource"]] = relationship(
        back_populates="channel", cascade="all, delete-orphan"
    )


class ChannelSource(Base):
    """Источник (X-аккаунт) для конкретного канала."""

    __tablename__ = "channel_sources"
    __table_args__ = (
        UniqueConstraint("channel_id", "twitter_username", name="uq_channel_source"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("channels.id", ondelete="CASCADE"), index=True
    )
    twitter_username: Mapped[str] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    channel: Mapped["Channel"] = relationship(back_populates="channel_sources")


# --------------------------------------------------------------------------- #
# Старые модели оставляем для обратной совместимости
# --------------------------------------------------------------------------- #


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        UniqueConstraint("user_id", "twitter_username", name="uq_user_source"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.tg_user_id", ondelete="CASCADE"), index=True
    )
    twitter_username: Mapped[str] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="sources")


class Target(Base):
    __tablename__ = "targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.tg_user_id", ondelete="CASCADE"), index=True
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chat_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mode: Mapped[str] = mapped_column(String(16), default="single")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="targets")


class UserSettings(Base):
    __tablename__ = "user_settings"

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.tg_user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    min_likes: Mapped[int] = mapped_column(Integer, default=200)
    min_retweets: Mapped[int] = mapped_column(Integer, default=20)
    skip_replies: Mapped[bool] = mapped_column(Boolean, default=True)
    digest_interval_hours: Mapped[int] = mapped_column(Integer, default=12)
    digest_max_tweets: Mapped[int] = mapped_column(Integer, default=7)
    niche: Mapped[str] = mapped_column(String(32), default="generic")
    custom_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship(back_populates="settings")


class ProcessedTweet(Base):
    __tablename__ = "processed_tweets"
    __table_args__ = (
        UniqueConstraint("user_id", "tweet_id", name="uq_user_tweet"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    tweet_id: Mapped[str] = mapped_column(String(32), index=True)
    twitter_username: Mapped[str] = mapped_column(String(32))
    processed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class DigestQueueItem(Base):
    __tablename__ = "digest_queue"
    __table_args__ = (
        UniqueConstraint("channel_id", "tweet_id", name="uq_channel_queue_tweet"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    channel_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    tweet_id: Mapped[str] = mapped_column(String(32))
    twitter_username: Mapped[str] = mapped_column(String(32))
    text: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(String(512))
    likes: Mapped[int] = mapped_column(Integer, default=0)
    retweets: Mapped[int] = mapped_column(Integer, default=0)
    queued_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class PostLog(Base):
    __tablename__ = "post_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    target_id: Mapped[int] = mapped_column(Integer)
    posted_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )
    is_digest: Mapped[bool] = mapped_column(Boolean, default=False)


class DigestLog(Base):
    __tablename__ = "digest_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    target_id: Mapped[int] = mapped_column(Integer)
    posted_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    tweet_count: Mapped[int] = mapped_column(Integer)


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("telegram_payment_charge_id", name="uq_payment_charge_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    amount_stars: Mapped[int] = mapped_column(Integer)
    tier: Mapped[str] = mapped_column(String(16))
    telegram_payment_charge_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class HealthNotification(Base):
    """Когда последний раз уведомляли юзера о проблемах с каналом.

    Используется в channel_health воркере, чтобы не спамить уведомлениями.
    Один канал — максимум одно уведомление в 7 дней.
    """

    __tablename__ = "health_notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[int] = mapped_column(Integer, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    reason: Mapped[str] = mapped_column(String(64))  # 'high_rejection_rate' / 'no_sources_active' / etc
    sent_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class RejectionLog(Base):
    """Лог отказов LLM по каждому твиту.

    Заполняется когда rewrite_tweet или build_digest вернули None из-за
    safety/value фильтра. Используется для health-диагностики каналов.
    """

    __tablename__ = "rejection_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[int] = mapped_column(Integer, index=True)
    tweet_id: Mapped[str] = mapped_column(String(32))
    twitter_username: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(String(32))  # 'skip' / 'meta' / 'short' / 'low_engagement'
    rejected_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )

