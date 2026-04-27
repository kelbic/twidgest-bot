"""Admin-команды. Доступны только владельцу бота (ADMIN_USER_ID из .env)."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from aiogram import Bot, Router
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from db.repositories.admin import (
    get_all_user_ids,
    get_global_stats,
    get_user_full,
)
from db.repositories.billing import activate_tier, downgrade_to_free
from db.session import session_maker
from tiers import TIERS, Tier, get_limits

logger = logging.getLogger(__name__)
router = Router(name="admin")


HELP_TEXT = (
    "Admin-команды\n\n"
    "/admin grant USER_ID TIER DAYS - выдать тариф\n"
    "  Пример: /admin grant 265715923 pro 30\n\n"
    "/admin revoke USER_ID - даунгрейд в Free\n"
    "/admin user USER_ID - профиль юзера\n"
    "/admin channels - все каналы всех юзеров\n"
    "/admin delete_channel CHANNEL_ID - удалить канал (любого юзера)\n"
    "/admin notify USER_ID TEXT - отправить личное сообщение юзеру\n"
    "/admin setfilter CHANNEL_ID PRESET - сменить filter_preset любого канала\n"
    "/admin stats - общая статистика\n"
    "/admin broadcast TEXT - рассылка всем"
)


@router.message(Command("admin"))
async def cmd_admin(message: Message, command: CommandObject) -> None:
    args = (command.args or "").split()
    if not args:
        await message.answer(HELP_TEXT)
        return

    sub = args[0].lower()
    rest = args[1:]

    if sub == "grant":
        await _admin_grant(message, rest)
    elif sub == "revoke":
        await _admin_revoke(message, rest)
    elif sub == "user":
        await _admin_user(message, rest)
    elif sub == "stats":
        await _admin_stats(message)
    elif sub == "broadcast":
        await _admin_broadcast(message, rest)
    elif sub == "channels":
        await _admin_channels(message)
    elif sub in ("delete_channel", "deletechannel"):
        await _admin_delete_channel(message, rest)
    elif sub == "notify":
        await _admin_notify(message, rest)
    elif sub == "setfilter":
        await _admin_setfilter(message, rest)
    else:
        await message.answer(f"Unknown subcommand: {sub}\n\n{HELP_TEXT}")


async def _admin_grant(message: Message, args: list) -> None:
    if len(args) != 3:
        await message.answer("Usage: /admin grant USER_ID TIER DAYS")
        return
    try:
        user_id = int(args[0])
        tier_str = args[1].lower()
        days = int(args[2])
    except ValueError:
        await message.answer("user_id and days must be integers.")
        return
    try:
        tier = Tier(tier_str)
    except ValueError:
        valid = ", ".join(t.value for t in Tier)
        await message.answer(f"Unknown tier. Valid: {valid}")
        return
    if days < 1 or days > 3650:
        await message.answer("Days must be between 1 and 3650.")
        return

    async with session_maker()() as session:
        user = await get_user_full(session, user_id)
        if user is None:
            await message.answer(
                f"User {user_id} not found. They must /start first."
            )
            return
        new_expiry = await activate_tier(
            session,
            user_id=user_id,
            tier=tier,
            duration_days=days,
            extend_existing=False,
        )

    await message.answer(
        f"Granted:\nUser: {user_id}\nTier: {TIERS[tier].name}\n"
        f"Until: {new_expiry.strftime('%d.%m.%Y %H:%M UTC')}"
    )
    logger.info(
        "Admin granted: user=%s tier=%s days=%d", user_id, tier.value, days
    )


async def _admin_revoke(message: Message, args: list) -> None:
    if len(args) != 1:
        await message.answer("Usage: /admin revoke USER_ID")
        return
    try:
        user_id = int(args[0])
    except ValueError:
        await message.answer("user_id must be int.")
        return
    async with session_maker()() as session:
        user = await get_user_full(session, user_id)
        if user is None:
            await message.answer(f"User {user_id} not found.")
            return
        await downgrade_to_free(session, user_id)
    await message.answer(f"User {user_id} downgraded to Free.")
    logger.info("Admin revoked tier from user %s", user_id)


async def _admin_user(message: Message, args: list) -> None:
    if len(args) != 1:
        await message.answer("Usage: /admin user USER_ID")
        return
    try:
        user_id = int(args[0])
    except ValueError:
        await message.answer("user_id must be int.")
        return

    async with session_maker()() as session:
        user = await get_user_full(session, user_id)
        if user is None:
            await message.answer(f"User {user_id} not found.")
            return

    now = datetime.utcnow()
    is_active = (
        user.tier != "free"
        and user.tier_expires_at is not None
        and user.tier_expires_at > now
    )
    effective = user.tier if is_active else "free"
    limits = get_limits(effective)
    expiry_str = (
        user.tier_expires_at.strftime("%d.%m.%Y %H:%M")
        if user.tier_expires_at
        else "-"
    )
    sources_list = (
        "\n".join(f"  - @{s.twitter_username}" for s in user.sources[:10])
        or "  (none)"
    )
    targets_list = (
        "\n".join(
            f"  - {t.chat_title or t.chat_id} ({t.mode}, "
            f"{'active' if t.is_active else 'inactive'})"
            for t in user.targets[:10]
        )
        or "  (none)"
    )

    await message.answer(
        f"User {user_id}\n"
        f"Username: @{user.tg_username or '-'}\n"
        f"Created: {user.created_at.strftime('%d.%m.%Y')}\n"
        f"Blocked: {'yes' if user.is_blocked else 'no'}\n\n"
        f"Tier (DB): {user.tier}\n"
        f"Expires: {expiry_str}\n"
        f"Effective: {limits.name}\n\n"
        f"Sources ({len(user.sources)}/{limits.max_sources}):\n{sources_list}\n\n"
        f"Targets ({len(user.targets)}/{limits.max_targets}):\n{targets_list}"
    )


async def _admin_stats(message: Message) -> None:
    async with session_maker()() as session:
        stats = await get_global_stats(session)

    await message.answer(
        f"TwidgestBot stats\n\n"
        f"Users:\n"
        f"  Total: {stats['total_users']}\n"
        f"  Paid (all-time): {stats['paid_users_total']}\n"
        f"  Active paid: {stats['active_paid_users']}\n"
        f"  New 7d: {stats['new_users_7d']}\n\n"
        f"Content:\n"
        f"  Sources: {stats['total_sources']}\n"
        f"  Targets: {stats['total_targets']}\n"
        f"  Posts 24h: {stats['posts_24h']}\n"
        f"  Digests 24h: {stats['digests_24h']}\n\n"
        f"Revenue:\n"
        f"  30d: {stats['revenue_30d_stars']} stars\n"
        f"  Total: {stats['revenue_total_stars']} stars"
    )


async def _admin_broadcast(message: Message, args: list) -> None:
    if not args:
        await message.answer("Usage: /admin broadcast TEXT")
        return
    text = " ".join(args)
    async with session_maker()() as session:
        user_ids = await get_all_user_ids(session)
    if not user_ids:
        await message.answer("No users to broadcast.")
        return

    await message.answer(f"Starting broadcast to {len(user_ids)} users...")

    sent = 0
    failed = 0
    blocked = 0
    bot: Bot = message.bot
    for uid in user_ids:
        try:
            await bot.send_message(uid, text, disable_web_page_preview=True)
            sent += 1
        except TelegramForbiddenError:
            blocked += 1
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after)
            try:
                await bot.send_message(uid, text, disable_web_page_preview=True)
                sent += 1
            except Exception:
                failed += 1
        except Exception:
            logger.exception("Broadcast to %s failed", uid)
            failed += 1
        await asyncio.sleep(0.05)

    await message.answer(
        f"Broadcast done:\n"
        f"  Sent: {sent}\n"
        f"  Blocked bot: {blocked}\n"
        f"  Failed: {failed}"
    )


async def _admin_channels(message: Message) -> None:
    """Показать все активные каналы всех юзеров с их статусом."""
    from datetime import datetime, timedelta
    from sqlalchemy import select, func as sa_func
    from sqlalchemy.orm import selectinload
    from db.models import Channel, PostLog, RejectionLog, User
    from db.session import session_maker

    async with session_maker()() as session:
        # Все каналы с привязанным target
        result = await session.execute(
            select(Channel)
            .join(User, Channel.user_id == User.tg_user_id)
            .options(selectinload(Channel.user), selectinload(Channel.channel_sources))
            .order_by(Channel.created_at.desc())
            .limit(50)
        )
        channels = list(result.scalars().all())

        # last_post_at для каждого
        last_posts_q = await session.execute(
            select(PostLog.target_id, sa_func.max(PostLog.posted_at))
            .group_by(PostLog.target_id)
        )
        last_posts = {row[0]: row[1] for row in last_posts_q.all()}

        # rejection counts за последние 24ч
        cutoff = datetime.utcnow() - timedelta(hours=24)
        rej_q = await session.execute(
            select(RejectionLog.channel_id, sa_func.count(RejectionLog.id))
            .where(RejectionLog.rejected_at > cutoff)
            .group_by(RejectionLog.channel_id)
        )
        rejections = {row[0]: row[1] for row in rej_q.all()}

    if not channels:
        await message.answer("No channels yet.")
        return

    now = datetime.utcnow()
    lines = [f"All channels ({len(channels)} shown):"]
    for ch in channels:
        username = ch.user.tg_username or str(ch.user_id)
        last = last_posts.get(ch.id)
        if last:
            delta_h = int((now - last).total_seconds() // 3600)
            last_str = f"{delta_h}h ago"
        else:
            last_str = "never"

        rej = rejections.get(ch.id, 0)
        target = "BOUND" if ch.target_chat_id else "no_target"

        lines.append(
            f"#{ch.id} @{username} | {ch.title[:35]}\n"
            f"   {ch.mode}/{ch.niche} | sources={len(ch.channel_sources)} | "
            f"last_post={last_str} | rej_24h={rej} | {target}"
        )

    text = "\n".join(lines)
    # Telegram limit 4096
    if len(text) > 4000:
        text = text[:3900] + "\n\n...truncated"
    await message.answer(f"<pre>{text}</pre>")


async def _admin_delete_channel(message: Message, args: list) -> None:
    """Удалить канал любого юзера (админская функция)."""
    if len(args) != 1:
        await message.answer("Usage: /admin delete_channel CHANNEL_ID")
        return
    try:
        channel_id = int(args[0])
    except ValueError:
        await message.answer("channel_id must be int.")
        return

    from sqlalchemy import select, delete as sa_delete
    from db.models import (
        Channel, ChannelSource, DigestQueueItem,
        RejectionLog, HealthNotification,
    )
    from db.session import session_maker

    async with session_maker()() as session:
        # Получаем канал чтобы знать owner
        result = await session.execute(
            select(Channel).where(Channel.id == channel_id)
        )
        channel = result.scalar_one_or_none()
        if channel is None:
            await message.answer(f"Channel {channel_id} not found.")
            return

        owner_id = channel.user_id
        title = channel.title

        # Удаляем все связанные данные
        await session.execute(
            sa_delete(DigestQueueItem).where(DigestQueueItem.channel_id == channel_id)
        )
        await session.execute(
            sa_delete(RejectionLog).where(RejectionLog.channel_id == channel_id)
        )
        await session.execute(
            sa_delete(HealthNotification).where(HealthNotification.channel_id == channel_id)
        )
        await session.execute(
            sa_delete(ChannelSource).where(ChannelSource.channel_id == channel_id)
        )
        await session.delete(channel)
        await session.commit()

    await message.answer(
        f"Deleted channel #{channel_id} ({title}) of user {owner_id}.\n"
        f"Cleaned queue, rejections, notifications, sources."
    )
    logger.info(
        "Admin deleted channel %d (%s) of user %d",
        channel_id, title, owner_id,
    )


async def _admin_notify(message: Message, args: list) -> None:
    """Отправить личное сообщение конкретному юзеру."""
    if len(args) < 2:
        await message.answer("Usage: /admin notify USER_ID TEXT")
        return
    try:
        user_id = int(args[0])
    except ValueError:
        await message.answer("user_id must be int.")
        return
    text = " ".join(args[1:])
    if not text:
        await message.answer("Empty message text.")
        return

    bot: Bot = message.bot
    try:
        await bot.send_message(user_id, text, disable_web_page_preview=True)
        await message.answer(f"Sent to user {user_id}.")
        logger.info("Admin sent notify to user %d: %s", user_id, text[:80])
    except TelegramForbiddenError:
        await message.answer(f"User {user_id} blocked the bot.")
    except Exception as exc:
        await message.answer(f"Failed to send: {exc}")
        logger.exception("Admin notify failed")



async def _admin_setfilter(message: Message, args: list) -> None:
    """Сменить filter_preset любого канала (admin override)."""
    if len(args) != 2:
        await message.answer("Usage: /admin setfilter CHANNEL_ID PRESET")
        return
    try:
        channel_id = int(args[0])
    except ValueError:
        await message.answer("channel_id must be int.")
        return

    preset_code = args[1].lower().strip()
    from prompts import FILTER_MODES as PRESETS, get_filter_mode as get_preset
    if preset_code not in PRESETS:
        valid = ", ".join(PRESETS.keys())
        await message.answer(f"Unknown preset. Valid: {valid}")
        return

    from sqlalchemy import select, update as sa_update
    from db.models import Channel
    from db.session import session_maker

    async with session_maker()() as session:
        result = await session.execute(
            select(Channel).where(Channel.id == channel_id)
        )
        channel = result.scalar_one_or_none()
        if channel is None:
            await message.answer(f"Channel {channel_id} not found.")
            return

        await session.execute(
            sa_update(Channel)
            .where(Channel.id == channel_id)
            .values(filter_preset=preset_code)
        )
        await session.commit()

    preset = get_preset(preset_code)
    await message.answer(
        f"Channel #{channel_id} ({channel.title}) filter changed to "
        f"{preset.emoji} {preset.name} (owner: {channel.user_id})."
    )
    logger.info(
        "Admin set filter on channel %d (owner %d) to %s",
        channel_id, channel.user_id, preset_code,
    )
