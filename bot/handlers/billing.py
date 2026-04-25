"""Telegram Stars billing: /upgrade, обработка платежей, /payments."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

from db.repositories.billing import (
    activate_tier,
    get_user_payments,
    record_payment,
)
from db.repositories.users import get_or_create_user, is_tier_active
from db.session import session_maker
from tiers import TIERS, Tier, get_limits

logger = logging.getLogger(__name__)
router = Router(name="billing")

SUBSCRIPTION_DAYS = 30


def _tier_emoji(tier: Tier) -> str:
    return {
        Tier.FREE: "🆓",
        Tier.STARTER: "⭐",
        Tier.PRO: "💎",
        Tier.AGENCY: "🏢",
    }[tier]


def _build_upgrade_keyboard() -> InlineKeyboardMarkup:
    """Инлайн-клавиатура с кнопками покупки тарифов."""
    rows: list[list[InlineKeyboardButton]] = []
    for tier in [Tier.STARTER, Tier.PRO, Tier.AGENCY]:
        limits = TIERS[tier]
        button = InlineKeyboardButton(
            text=f"{_tier_emoji(tier)} {limits.name} — {limits.price_stars}⭐/мес",
            callback_data=f"buy:{tier.value}",
        )
        rows.append([button])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("upgrade"))
async def cmd_upgrade(message: Message) -> None:
    if message.from_user is None:
        return
    async with session_maker()() as session:
        user = await get_or_create_user(
            session, message.from_user.id, message.from_user.username
        )
        active = await is_tier_active(user)

    lines = ["💎 <b>Тарифы TwidgestBot</b>\n"]
    for tier in [Tier.FREE, Tier.STARTER, Tier.PRO, Tier.AGENCY]:
        limits = TIERS[tier]
        is_current = (user.tier == tier.value and active) or (
            tier == Tier.FREE and not active
        )
        marker = " ← <b>текущий</b>" if is_current else ""
        price = "бесплатно" if limits.price_stars == 0 else f"{limits.price_stars}⭐/мес"
        lines.append(
            f"{_tier_emoji(tier)} <b>{limits.name}</b> — {price}{marker}\n"
            f"  • {limits.max_sources} источников, {limits.max_targets} канал(ов)\n"
            f"  • до {limits.max_posts_per_day} постов/день\n"
            f"  • Digest-режим: {'✅' if limits.can_use_digest_mode else '❌'}, "
            f"Pro-LLM (Claude): {'✅' if limits.use_pro_llm else '❌'}\n"
        )
    lines.append("\nВыбери тариф для покупки за Telegram Stars:")
    await message.answer(
        "\n".join(lines), reply_markup=_build_upgrade_keyboard()
    )


@router.callback_query(F.data.startswith("buy:"))
async def cb_buy_tier(call: CallbackQuery) -> None:
    if call.data is None or call.from_user is None or call.message is None:
        return
    tier_value = call.data.split(":", 1)[1]
    try:
        tier = Tier(tier_value)
    except ValueError:
        await call.answer("Неизвестный тариф", show_alert=True)
        return

    if tier == Tier.FREE:
        await call.answer("Free уже у тебя", show_alert=True)
        return

    limits = TIERS[tier]
    # Отправляем invoice. Currency XTR = Telegram Stars.
    # Для подписочной модели передаём subscription_period в секундах.
    try:
        await call.message.bot.send_invoice(
            chat_id=call.from_user.id,
            title=f"TwidgestBot {limits.name}",
            description=(
                f"Доступ к тарифу {limits.name} на 30 дней. "
                f"{limits.max_sources} источников, "
                f"до {limits.max_posts_per_day} постов/день."
            ),
            payload=f"sub:{tier.value}",
            currency="XTR",
            prices=[LabeledPrice(label=f"{limits.name} (30 дней)", amount=limits.price_stars)],
            # Подписочная модель: 30 дней = 2592000 секунд
        )
        await call.answer()
    except Exception as exc:
        logger.exception("Failed to send invoice")
        await call.answer(f"Ошибка: {exc}", show_alert=True)


@router.pre_checkout_query()
async def on_pre_checkout(query: PreCheckoutQuery) -> None:
    """Telegram спрашивает разрешение на проведение платежа.
    Отвечаем сразу ok=True, иначе платёж не пройдёт.
    Здесь могла бы быть последняя валидация (ещё доступен ли тариф и т.п.)."""
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def on_successful_payment(message: Message) -> None:
    """Платёж прошёл — активируем тариф и пишем благодарность."""
    if message.from_user is None or message.successful_payment is None:
        return

    payment = message.successful_payment
    payload = payment.invoice_payload  # "sub:starter" / "sub:pro" / ...

    if not payload.startswith("sub:"):
        logger.error("Unknown payload: %s", payload)
        await message.answer(
            "⚠️ Платёж получен, но я не смог определить тариф. "
            "Напиши в поддержку с этим chat_id и временем."
        )
        return

    try:
        tier = Tier(payload.split(":", 1)[1])
    except ValueError:
        logger.error("Unknown tier in payload: %s", payload)
        await message.answer("⚠️ Неизвестный тариф в платеже. Напиши в поддержку.")
        return

    async with session_maker()() as session:
        # Активируем тариф
        new_expiry = await activate_tier(
            session,
            user_id=message.from_user.id,
            tier=tier,
            duration_days=SUBSCRIPTION_DAYS,
        )
        # Записываем платёж
        await record_payment(
            session,
            user_id=message.from_user.id,
            amount_stars=payment.total_amount,
            tier=tier,
            telegram_payment_charge_id=payment.telegram_payment_charge_id,
        )

    limits = TIERS[tier]
    await message.answer(
        f"✅ <b>Спасибо!</b> Тариф <b>{limits.name}</b> активирован.\n\n"
        f"Действует до: <b>{new_expiry.strftime('%d.%m.%Y')}</b>\n"
        f"Источников: до <b>{limits.max_sources}</b>\n"
        f"Постов/день: до <b>{limits.max_posts_per_day}</b>\n"
        f"Digest-режим: {'✅' if limits.can_use_digest_mode else '❌'}\n\n"
        f"Покупка разовая. Через 30 дней нужно будет купить заново через /upgrade. "
        f"Управление: /payments"
    )

    logger.info(
        "User %s purchased %s for %d stars (charge_id=%s)",
        message.from_user.id,
        tier.value,
        payment.total_amount,
        payment.telegram_payment_charge_id,
    )


@router.message(Command("payments"))
async def cmd_payments(message: Message) -> None:
    if message.from_user is None:
        return
    async with session_maker()() as session:
        user = await get_or_create_user(
            session, message.from_user.id, message.from_user.username
        )
        active = await is_tier_active(user)
        payments = await get_user_payments(session, user.tg_user_id, limit=10)

    lines = ["💳 <b>Подписка и платежи</b>\n"]
    if user.tier == "free" or not active:
        lines.append("Тариф: <b>Free</b>")
        if user.tier_expires_at and not active:
            lines.append(
                f"Прошлый тариф истёк: {user.tier_expires_at.strftime('%d.%m.%Y')}"
            )
    else:
        lines.append(f"Тариф: <b>{get_limits(user.tier).name}</b>")
        if user.tier_expires_at:
            lines.append(f"Действует до: {user.tier_expires_at.strftime('%d.%m.%Y')}")

    if payments:
        lines.append("\n<b>Последние платежи:</b>")
        for p in payments:
            lines.append(
                f"  • {p.created_at.strftime('%d.%m.%Y')} — "
                f"{p.amount_stars}⭐ за {p.tier}"
            )
    else:
        lines.append("\n<i>Платежей пока нет.</i>")

    lines.append("\n/upgrade — управление подпиской")
    await message.answer("\n".join(lines))
