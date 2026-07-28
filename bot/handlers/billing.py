"""Биллинг слот-модели: канал = слот, активация PRICE_STARS⭐ на SLOT_DAYS дней.

/upgrade — список каналов юзера со статусами и кнопками оплаты.
Оплата Telegram Stars (XTR), payload "slot:<channel_id>".
Продление — от конца текущей оплаты/триала (extension_base), не от «сейчас».
Legacy-ветка "sub:<tier>" оставлена для старых неоплаченных инвойсов.
"""
from __future__ import annotations

import html
import json
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
from datetime import timedelta

from sqlalchemy import select

from config import Config
from core.plan import (
    PRICE_STARS,
    SLOT_DAYS,
    _ADMIN_ID,
    channel_status,
    extension_base,
)
from db.models import Channel, Payment
from db.repositories.billing import get_user_payments, record_payment
from db.session import session_maker

logger = logging.getLogger(__name__)
router = Router(name="billing")

_cfg = Config()

def rub_visible() -> bool:
    """Показывать ли рублёвые цены: есть боевой токен провайдера."""
    return bool(_cfg.payment_provider_token)


def price_display() -> str:
    """'1490 ₽ (или 999⭐)' при рублях, иначе '999⭐' — для любых текстов."""
    if rub_visible():
        return f"{_cfg.price_rub} ₽ (или {PRICE_STARS}⭐)"
    return f"{PRICE_STARS}⭐"


def payment_amount_display(p: Payment) -> str:
    """'999⭐' для Stars, '1490 ₽' для рублей (amount в копейках)."""
    currency = getattr(p, "currency", None) or "XTR"
    if currency == "XTR":
        return f"{p.amount_stars}⭐"
    if currency == "RUB":
        return f"{p.amount_stars // 100} ₽"
    return f"{p.amount_stars / 100:.2f} {currency}"

_STATUS_LINE = {
    "admin": "🛡 служебный канал (без оплаты)",
    "paid": "🟢 оплачен до {until:%d.%m.%Y}",
    "trial": "🎁 триал до {until:%d.%m.%Y}",
    "inactive": "🔴 неактивен — публикации остановлены",
}


def _status_text(channel) -> str:
    st = channel_status(channel)
    if st == "paid":
        return _STATUS_LINE[st].format(until=channel.paid_until)
    if st == "trial":
        return _STATUS_LINE[st].format(until=channel.trial_until)
    return _STATUS_LINE[st]


@router.message(Command("upgrade"))
async def cmd_upgrade(message: Message) -> None:
    if message.from_user is None:
        return
    async with session_maker()() as session:
        result = await session.execute(
            select(Channel).where(Channel.user_id == message.from_user.id)
        )
        channels = list(result.scalars().all())

    if not channels:
        await message.answer(
            "У тебя пока нет каналов. Напиши тему одним сообщением — "
            "создам канал с проверенными источниками (первый канал "
            f"получает 🎁 триал 7 дней, дальше {PRICE_STARS}⭐ за 30 дней)."
        )
        return

    rub_enabled = rub_visible()
    price_line = (
        f"💳 <b>Оплата каналов</b> — {_cfg.price_rub} ₽ (или {PRICE_STARS}⭐) "
        f"за {SLOT_DAYS} дней автопостинга на канал\n"
        if rub_enabled
        else f"💳 <b>Оплата каналов</b> — {PRICE_STARS}⭐ за {SLOT_DAYS} дней "
        f"автопостинга на канал\n"
    )
    lines = [price_line]
    buttons: list[list[InlineKeyboardButton]] = []
    for ch in channels:
        lines.append(f"<b>{html.escape(ch.title or '')}</b> (id={ch.id})\n  {_status_text(ch)}")
        st = channel_status(ch)
        if st == "admin":
            continue
        verb = "Продлить" if st in ("paid", "trial") else "Активировать"
        if rub_enabled:
            # Цена — первой: половинную кнопку Telegram режет с конца,
            # и «— 1490 ₽» после названия канала терялось.
            buttons.append([
                InlineKeyboardButton(
                    text=f"💳 {_cfg.price_rub} ₽ · {ch.title[:12]}",
                    callback_data=f"payrub:{ch.id}",
                ),
                InlineKeyboardButton(
                    text=f"⭐ {PRICE_STARS}",
                    callback_data=f"payslot:{ch.id}",
                ),
            ])
        else:
            buttons.append([InlineKeyboardButton(
                text=f"💳 {verb} «{ch.title[:24]}» — {PRICE_STARS}⭐",
                callback_data=f"payslot:{ch.id}",
            )])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None
    lines.append(
        f"\nПродление добавляет {SLOT_DAYS} дней к текущей дате окончания."
    )
    await message.answer("\n".join(lines), reply_markup=kb)


async def _own_channel_from_callback(callback: CallbackQuery) -> Channel | None:
    """Достаёт канал из callback_data 'pay*:<id>' с проверкой владельца."""
    try:
        channel_id = int((callback.data or "").split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Некорректный канал", show_alert=True)
        return None

    async with session_maker()() as session:
        result = await session.execute(
            select(Channel).where(
                Channel.id == channel_id,
                Channel.user_id == callback.from_user.id,
            )
        )
        channel = result.scalar_one_or_none()

    if channel is None:
        await callback.answer("Канал не найден или не твой", show_alert=True)
        return None
    return channel


def _receipt_provider_data() -> str | None:
    """Чек 54-ФЗ для ЮKassa. None, если фискализация выключена.

    E-mail покупателя Telegram подставляет сам при need_email +
    send_email_to_provider, поэтому в чеке только позиция.
    """
    if not _cfg.payment_receipt:
        return None
    return json.dumps({
        "receipt": {
            "items": [{
                "description": f"Автопостинг канала, {SLOT_DAYS} дней",
                "quantity": "1.00",
                "amount": {
                    "value": f"{_cfg.price_rub}.00",
                    "currency": "RUB",
                },
                "vat_code": _cfg.payment_vat_code,
                "payment_mode": "full_payment",
                "payment_subject": "service",
            }],
        },
    }, ensure_ascii=False)


def _invoice_texts(channel: Channel) -> tuple[str, str]:
    return (
        f"Канал «{channel.title[:28]}» — {SLOT_DAYS} дней",
        f"Автопостинг для канала «{channel.title[:60]}»: сбор твитов, "
        f"AI-отбор, перевод и публикация. {SLOT_DAYS} дней с момента "
        f"окончания текущего периода.",
    )


@router.callback_query(F.data.startswith("payslot:"))
async def cb_pay_slot(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.data is None:
        return
    channel = await _own_channel_from_callback(callback)
    if channel is None:
        return

    await callback.answer()
    title, description = _invoice_texts(channel)
    await callback.bot.send_invoice(
        chat_id=callback.from_user.id,
        title=title,
        description=description,
        payload=f"slot:{channel.id}",
        currency="XTR",
        prices=[LabeledPrice(label=f"{SLOT_DAYS} дней автопостинга", amount=PRICE_STARS)],
    )


@router.callback_query(F.data.startswith("payrub:"))
async def cb_pay_rub(callback: CallbackQuery) -> None:
    """Рублёвый инвойс через провайдера Telegram Payments (BotFather)."""
    if callback.from_user is None or callback.data is None:
        return
    if not _cfg.payment_provider_token:
        await callback.answer(
            "Оплата картой (ЮKassa) уже подключается — пока можно "
            "оплатить Stars ⭐", show_alert=True
        )
        return
    channel = await _own_channel_from_callback(callback)
    if channel is None:
        return

    await callback.answer()
    title, description = _invoice_texts(channel)
    await callback.bot.send_invoice(
        chat_id=callback.from_user.id,
        title=title,
        description=description,
        payload=f"slot:{channel.id}",
        provider_token=_cfg.payment_provider_token,
        currency="RUB",
        # Суммы в Telegram Payments — в минимальных единицах (копейки)
        prices=[LabeledPrice(
            label=f"{SLOT_DAYS} дней автопостинга",
            amount=_cfg.price_rub * 100,
        )],
        # С чеком e-mail обязателен: ЮKassa шлёт на него фискальный документ
        need_email=_cfg.payment_need_email or _cfg.payment_receipt,
        send_email_to_provider=_cfg.payment_need_email or _cfg.payment_receipt,
        provider_data=_receipt_provider_data(),
    )


@router.pre_checkout_query()
async def on_pre_checkout(pcq: PreCheckoutQuery) -> None:
    ok = pcq.invoice_payload.startswith(("slot:", "sub:"))
    await pcq.answer(ok=ok, error_message=None if ok else "Устаревший инвойс")


@router.message(F.successful_payment)
async def on_successful_payment(message: Message) -> None:
    if message.from_user is None or message.successful_payment is None:
        return
    sp = message.successful_payment
    payload = sp.invoice_payload or ""
    uid = message.from_user.id

    if payload.startswith("slot:"):
        try:
            channel_id = int(payload.split(":", 1)[1])
        except (ValueError, IndexError):
            logger.error("payment: bad slot payload %r from %d", payload, uid)
            return
        async with session_maker()() as session:
            result = await session.execute(
                select(Channel).where(
                    Channel.id == channel_id, Channel.user_id == uid
                )
            )
            channel = result.scalar_one_or_none()
            if channel is None:
                logger.error(
                    "payment: slot %d not found for payer %d (charge %s)",
                    channel_id, uid, sp.telegram_payment_charge_id,
                )
                await message.answer(
                    "⚠️ Платёж получен, но канал не найден. Напиши @kelbic — "
                    "разберёмся и продлим вручную."
                )
                return
            new_until = extension_base(channel) + timedelta(days=SLOT_DAYS)
            channel.paid_until = new_until
            channel.archived_at = None  # оплата воскрешает из архива
            await session.commit()
            await record_payment(
                session, uid, sp.total_amount, f"slot:{channel_id}",
                sp.telegram_payment_charge_id, currency=sp.currency or "XTR",
            )
        logger.info(
            "payment: slot %d paid by %d (%s %s), active until %s",
            channel_id, uid, sp.total_amount, sp.currency, new_until,
        )
        await message.answer(
            f"✅ Оплата получена! Канал <b>«{html.escape(channel.title or '')}»</b> активен "
            f"до <b>{new_until:%d.%m.%Y}</b>.\n\n"
            f"Статусы всех каналов: /upgrade"
        )
        return

    # Legacy "sub:<tier>" — старые инвойсы тарифной сетки
    if payload.startswith("sub:"):
        logger.warning("payment: legacy tier payload %r from %d", payload, uid)
        async with session_maker()() as session:
            await record_payment(
                session, uid, sp.total_amount, payload,
                sp.telegram_payment_charge_id, currency=sp.currency or "XTR",
            )
        await message.answer(
            "✅ Платёж получен по старому тарифу. Мы перешли на оплату "
            "по каналам — напиши @kelbic, переведём оплату на нужный канал."
        )
        return

    logger.error("payment: unknown payload %r from %d", payload, uid)


async def check_rub_provider(bot) -> tuple[bool, str]:
    """Гоняет боевые параметры инвойса через провайдера, не проводя платёж.

    createInvoiceLink валидирует provider_token, валюту, сумму и чек так же,
    как реальный инвойс, но ссылку никто не получает. Возврат: (ок, детали).
    """
    link = await bot.create_invoice_link(
        title=f"Проверка оплаты — {SLOT_DAYS} дней",
        description="Тестовый инвойс, никому не отправляется.",
        payload="paycheck",
        provider_token=_cfg.payment_provider_token,
        currency="RUB",
        prices=[LabeledPrice(
            label=f"{SLOT_DAYS} дней автопостинга",
            amount=_cfg.price_rub * 100,
        )],
        need_email=_cfg.payment_need_email or _cfg.payment_receipt,
        send_email_to_provider=_cfg.payment_need_email or _cfg.payment_receipt,
        provider_data=_receipt_provider_data(),
    )
    return True, link


@router.message(Command("paycheck"))
async def cmd_paycheck(message: Message) -> None:
    """Админ: проверка рублёвого провайдера без платежа и без адресата.

    createInvoiceLink гоняет параметры через ЮKassa так же, как реальный
    инвойс, но ссылку никто не получает и деньги не списываются.
    """
    if message.from_user is None or message.from_user.id != _ADMIN_ID:
        return
    token = _cfg.payment_provider_token
    if not token:
        await message.answer("PAYMENT_PROVIDER_TOKEN пуст — рублей нет.")
        return
    shape = token.split(":")[1] if token.count(":") >= 2 else "?"
    head = (
        f"Токен: длина {len(token)}, режим <b>{shape}</b> "
        f"(ждём LIVE или TEST)\nЧек: {'on' if _cfg.payment_receipt else 'off'}\n\n"
    )
    try:
        _, link = await check_rub_provider(message.bot)
    except Exception as exc:  # noqa: BLE001 — текст ошибки и есть диагноз
        logger.warning("paycheck failed: %s", exc)
        await message.answer(head + f"❌ Провайдер отверг инвойс:\n<code>{html.escape(str(exc))}</code>")
        return
    await message.answer(head + f"✅ Провайдер принял инвойс.\nСсылка (можно оплатить): {link}")


@router.message(Command("payments"))
async def cmd_payments(message: Message) -> None:
    if message.from_user is None:
        return
    async with session_maker()() as session:
        payments = await get_user_payments(session, message.from_user.id)
    if not payments:
        await message.answer("Платежей пока не было. Оплата каналов: /upgrade")
        return
    lines = ["🧾 <b>Последние платежи:</b>\n"]
    for p in payments:
        lines.append(
            f"  {p.created_at:%d.%m.%Y} — {payment_amount_display(p)} ({p.tier})"
        )
    await message.answer("\n".join(lines))
