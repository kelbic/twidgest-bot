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
