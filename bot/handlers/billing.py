"""Биллинг слот-модели: канал = слот, активация PRICE_STARS⭐ на SLOT_DAYS дней.

/upgrade — список каналов юзера со статусами и кнопками оплаты.
Оплата Telegram Stars (XTR), payload "slot:<channel_id>".
Продление — от конца текущей оплаты/триала (extension_base), не от «сейчас».
Legacy-ветка "sub:<tier>" оставлена для старых неоплаченных инвойсов.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)
from datetime import datetime, timedelta

from sqlalchemy import select

from config import Config
from core.plan import (
    PRICE_STARS,
    SLOT_DAYS,
    _ADMIN_ID,
    channel_status,
    extension_base,
)
from core.yookassa import YooKassaClient, YooKassaError
from db.models import Channel, Payment, RubPayment
from db.repositories.billing import get_user_payments, record_payment
from db.session import session_maker

logger = logging.getLogger(__name__)
router = Router(name="billing")

_cfg = Config()

# Минимум рублёвого счёта в Telegram Payments (currencies.json, RUB)
MIN_RUB = 60

# Куда возвращать плательщика после оплаты в банке; main.py уточняет
# реальным username на старте
BOT_USERNAME = "TwidgestBot"

_SBP_POLL_INTERVAL = 5          # сек между опросами статуса свежего платежа
_SBP_POLL_TIMEOUT = 15 * 60     # дальше полагаемся на кнопку «проверить»

# Один зачёт на платёж: поллер и кнопка «проверить» могут узнать об оплате
# одновременно, без лока оба продлили бы канал
_sbp_claim_lock = asyncio.Lock()


def rub_visible() -> bool:
    """Показывать ли рублёвые цены: есть боевой токен провайдера."""
    return bool(_cfg.payment_provider_token)


def sbp_visible() -> bool:
    """Показывать ли кнопку СБП: есть ключи прямого API ЮKassa."""
    return bool(_cfg.yookassa_shop_id and _cfg.yookassa_secret_key)


def _yk() -> YooKassaClient:
    return YooKassaClient(_cfg.yookassa_shop_id, _cfg.yookassa_secret_key)


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
            if sbp_visible():
                buttons.append([InlineKeyboardButton(
                    text=f"⚡ По СБП (через банк) · {ch.title[:14]}",
                    callback_data=f"paysbp:{ch.id}",
                )])
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


def _contact_flags() -> tuple[bool, bool]:
    """(need_email, need_phone) — контакт покупателя для чека 54-ФЗ.

    Плательщику это лишний шаг, поэтому спрашиваем только когда чек
    действительно наш, и по умолчанию телефоном: Telegram подставляет
    номер аккаунта одним тапом, а e-mail надо набирать руками.
    """
    if not (_cfg.payment_receipt or _cfg.payment_need_email):
        return False, False
    if _cfg.payment_contact == "email":
        return True, False
    if _cfg.payment_contact == "none":
        return False, False
    return False, True


def _receipt_provider_data(amount_rub: int | None = None) -> str | None:
    """Чек 54-ФЗ для ЮKassa. None, если фискализация выключена.

    Контакт покупателя (телефон или e-mail) Telegram подставляет сам —
    см. _contact_flags, поэтому в чеке только позиция. Сумма позиции
    обязана совпадать с суммой инвойса — отсюда параметр amount_rub.
    """
    if not _cfg.payment_receipt:
        return None
    return json.dumps({
        "receipt": {
            "items": [{
                "description": f"Автопостинг канала, {SLOT_DAYS} дней",
                "quantity": "1.00",
                "amount": {
                    "value": f"{amount_rub or _cfg.price_rub}.00",
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
    _need_email, _need_phone = _contact_flags()
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
        # С чеком нужен контакт: на него ЮKassa шлёт фискальный документ
        need_email=_need_email,
        send_email_to_provider=_need_email,
        need_phone_number=_need_phone,
        send_phone_number_to_provider=_need_phone,
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
        channel, new_until = await _apply_slot_payment(
            uid, channel_id, sp.total_amount,
            sp.currency or "XTR", sp.telegram_payment_charge_id,
        )
        if channel is None:
            await message.answer(
                "⚠️ Платёж получен, но канал не найден. Напиши @kelbic — "
                "разберёмся и продлим вручную."
            )
            return
        await message.answer(_paid_text(channel, new_until))
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


async def _apply_slot_payment(
    uid: int, channel_id: int, amount_minor: int, currency: str, charge_id: str,
) -> tuple[Channel | None, datetime | None]:
    """Зачисляет оплату слота: продление, воскрешение из архива, запись.

    Общий финал обоих путей — Telegram Payments (successful_payment) и
    прямого API ЮKassa (СБП). amount_minor — в минимальных единицах
    (Stars или копейки). (None, None) = канал не найден/чужой.
    """
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
                channel_id, uid, charge_id,
            )
            return None, None
        new_until = extension_base(channel) + timedelta(days=SLOT_DAYS)
        channel.paid_until = new_until
        channel.archived_at = None  # оплата воскрешает из архива
        await session.commit()
        await record_payment(
            session, uid, amount_minor, f"slot:{channel_id}",
            charge_id, currency=currency,
        )
    logger.info(
        "payment: slot %d paid by %d (%s %s), active until %s",
        channel_id, uid, amount_minor, currency, new_until,
    )
    return channel, new_until


def _paid_text(channel: Channel, new_until: datetime) -> str:
    return (
        f"✅ Оплата получена! Канал <b>«{html.escape(channel.title or '')}»</b> активен "
        f"до <b>{new_until:%d.%m.%Y}</b>.\n\n"
        f"Статусы всех каналов: /upgrade"
    )


# ---------------------------------------------------------------------------
# СБП через прямой API ЮKassa: в нативной телеграм-форме СБП не бывает.
# Чек НЕ шлём: магазин на самозанятом, фискализация 54-ФЗ не применяется —
# чек формирует «Мой налог». Если ЮKassa вдруг потребует чек, создание
# платежа упадёт с внятной ошибкой (видно в /paycheck sbp), тогда и решаем.
# ---------------------------------------------------------------------------

async def _start_sbp_payment(
    bot, uid: int, channel: Channel, amount_rub: int,
) -> None:
    """Создаёт СБП-платёж, шлёт ссылку на оплату, запускает поллер."""
    try:
        payment = await _yk().create_sbp_payment(
            amount_rub=amount_rub,
            description=f"Twidgest: «{channel.title[:60]}», {SLOT_DAYS} дней",
            return_url=f"https://t.me/{BOT_USERNAME}",
            metadata={"tg_user_id": str(uid), "channel_id": str(channel.id)},
        )
        yk_id = payment["id"]
        pay_url = payment["confirmation"]["confirmation_url"]
    except (YooKassaError, KeyError) as exc:
        logger.warning("sbp: create failed for %d: %s", uid, exc)
        await bot.send_message(
            uid,
            "❌ Не получилось создать СБП-платёж. Попробуй ещё раз чуть "
            "позже или оплати картой — кнопка 💳 в /upgrade.",
        )
        return

    async with session_maker()() as session:
        session.add(RubPayment(
            yk_payment_id=yk_id, user_id=uid,
            channel_id=channel.id, amount_rub=amount_rub,
        ))
        await session.commit()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"⚡ Оплатить {amount_rub} ₽ по СБП", url=pay_url)],
        [InlineKeyboardButton(
            text="🔄 Я оплатил — проверить",
            callback_data=f"sbpcheck:{yk_id}",
        )],
    ])
    await bot.send_message(
        uid,
        f"Счёт по СБП на <b>{amount_rub} ₽</b> — канал "
        f"«{html.escape(channel.title or '')}».\n\n"
        "По кнопке откроется выбор банка, оплата подтверждается в приложении "
        "банка. Как только деньги придут, я напишу сюда сам — обычно это "
        "меньше минуты.",
        reply_markup=kb,
    )
    logger.info("sbp: payment %s created (%d RUB, user %d)", yk_id, amount_rub, uid)
    asyncio.create_task(_poll_sbp_payment(bot, yk_id))


async def _settle_sbp_payment(bot, yk_id: str, yk_status: str) -> bool:
    """Финализирует платёж по статусу из ЮKassa. True = больше не pending.

    Клейм статуса — под локом: продлить канал по одному платежу можно
    ровно один раз, кто успел (поллер или кнопка) — тот и зачёл.
    """
    if yk_status not in ("succeeded", "canceled"):
        return False

    async with _sbp_claim_lock:
        async with session_maker()() as session:
            result = await session.execute(
                select(RubPayment).where(RubPayment.yk_payment_id == yk_id)
            )
            rp = result.scalar_one_or_none()
            if rp is None or rp.status != "pending":
                return True  # уже зачтён/отменён другой веткой
            rp.status = yk_status
            await session.commit()

    if yk_status == "canceled":
        logger.info("sbp: payment %s canceled", yk_id)
        await bot.send_message(
            rp.user_id,
            "СБП-платёж отменился (не оплачен вовремя или отклонён банком). "
            "Можно попробовать снова: /upgrade",
        )
        return True

    channel, new_until = await _apply_slot_payment(
        rp.user_id, rp.channel_id, rp.amount_rub * 100, "RUB", f"yk:{yk_id}",
    )
    if channel is None:
        await bot.send_message(
            rp.user_id,
            "⚠️ Платёж получен, но канал не найден. Напиши @kelbic — "
            "разберёмся и продлим вручную.",
        )
        return True
    await bot.send_message(rp.user_id, _paid_text(channel, new_until))
    return True


async def _poll_sbp_payment(
    bot, yk_id: str,
    interval: int = _SBP_POLL_INTERVAL,
    timeout: int = _SBP_POLL_TIMEOUT,
) -> None:
    """Опрашивает статус платежа, пока тот не решится или не выйдет время.

    После таймаута платёж остаётся pending — у юзера есть кнопка
    «проверить», а рестарт бота дочитает хвосты (resume_pending_sbp).
    """
    waited = 0
    while waited < timeout:
        await asyncio.sleep(interval)
        waited += interval
        try:
            payment = await _yk().get_payment(yk_id)
        except YooKassaError as exc:
            logger.warning("sbp: poll %s failed: %s", yk_id, exc)
            continue
        if await _settle_sbp_payment(bot, yk_id, payment.get("status", "")):
            return
    logger.info("sbp: payment %s still pending after %ds, poll stopped", yk_id, timeout)


async def resume_pending_sbp(bot) -> int:
    """Дочитывает pending-платежи после рестарта (сутки — глубже нет смысла:
    неоплаченный СБП-счёт ЮKassa отменяет сама). Возвращает число хвостов."""
    cutoff = datetime.utcnow() - timedelta(hours=24)
    async with session_maker()() as session:
        result = await session.execute(
            select(RubPayment).where(
                RubPayment.status == "pending",
                RubPayment.created_at >= cutoff,
            )
        )
        pending = list(result.scalars().all())
    for rp in pending:
        asyncio.create_task(
            _poll_sbp_payment(bot, rp.yk_payment_id, interval=30, timeout=3600)
        )
    return len(pending)


async def check_sbp_provider() -> str:
    """Самопроверка ключа API: /me отвечает только на живой ключ.

    Возвращает сводку магазина — статус, СБП в способах, фискализация.
    """
    me = await _yk().me()
    methods = me.get("payment_methods") or []
    return (
        f"shop {me.get('account_id')}, status {me.get('status')}, "
        f"sbp {'on' if 'sbp' in methods else 'NOT in ' + str(methods)}, "
        f"fiscalization {me.get('fiscalization_enabled', '?')}"
    )


@router.callback_query(F.data.startswith("paysbp:"))
async def cb_pay_sbp(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.data is None:
        return
    if not sbp_visible():
        await callback.answer(
            "СБП сейчас недоступен — можно оплатить картой 💳 или Stars ⭐",
            show_alert=True,
        )
        return
    channel = await _own_channel_from_callback(callback)
    if channel is None:
        return
    await callback.answer()
    await _start_sbp_payment(
        callback.bot, callback.from_user.id, channel, _cfg.price_rub
    )


@router.callback_query(F.data.startswith("sbpcheck:"))
async def cb_sbp_check(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.data is None:
        return
    yk_id = callback.data.split(":", 1)[1]
    async with session_maker()() as session:
        result = await session.execute(
            select(RubPayment).where(
                RubPayment.yk_payment_id == yk_id,
                RubPayment.user_id == callback.from_user.id,
            )
        )
        rp = result.scalar_one_or_none()
    if rp is None:
        await callback.answer("Платёж не найден", show_alert=True)
        return
    if rp.status == "succeeded":
        await callback.answer("Уже зачислено ✅")
        return
    if rp.status == "canceled":
        await callback.answer("Платёж был отменён — создай новый в /upgrade", show_alert=True)
        return
    try:
        payment = await _yk().get_payment(yk_id)
    except YooKassaError as exc:
        logger.warning("sbp: check %s failed: %s", yk_id, exc)
        await callback.answer("ЮKassa не ответила, попробуй через минуту", show_alert=True)
        return
    settled = await _settle_sbp_payment(
        callback.bot, yk_id, payment.get("status", "")
    )
    if settled:
        await callback.answer()
    else:
        await callback.answer(
            "Оплата ещё не пришла. Если платил только что — подожди "
            "полминуты и нажми ещё раз.", show_alert=True,
        )


async def check_rub_provider(bot) -> str:
    """Гоняет боевые параметры инвойса через провайдера, не проводя платёж.

    createInvoiceLink валидирует provider_token, валюту, сумму и чек так же,
    как реальный инвойс. Ссылку наружу не отдаём — payload у неё не «slot:»,
    так что оплата по ней всё равно упрётся в pre-checkout. Бросает при отказе.
    """
    _need_email, _need_phone = _contact_flags()
    return await bot.create_invoice_link(
        title=f"Проверка оплаты — {SLOT_DAYS} дней",
        description="Тестовый инвойс, никому не отправляется.",
        payload="paycheck",
        provider_token=_cfg.payment_provider_token,
        currency="RUB",
        prices=[LabeledPrice(
            label=f"{SLOT_DAYS} дней автопостинга",
            amount=_cfg.price_rub * 100,
        )],
        need_email=_need_email,
        send_email_to_provider=_need_email,
        need_phone_number=_need_phone,
        send_phone_number_to_provider=_need_phone,
        provider_data=_receipt_provider_data(),
    )


async def _send_test_invoice(message: Message, amount_rub: int) -> None:
    """Настоящий рублёвый инвойс админу на произвольную сумму.

    Нужен для e2e-проверки: служебные каналы владельца кнопок оплаты
    не получают, а пройти весь путь до чека и продления надо.
    """
    async with session_maker()() as session:
        result = await session.execute(
            select(Channel).where(Channel.user_id == message.from_user.id)
        )
        channel = result.scalars().first()
    if channel is None:
        await message.answer("Нет ни одного канала — не к чему привязать платёж.")
        return
    _need_email, _need_phone = _contact_flags()
    try:
        await message.bot.send_invoice(
            chat_id=message.from_user.id,
            title=f"Проверка оплаты — {amount_rub} ₽",
            description=(
                f"Тестовый платёж по каналу «{channel.title[:40]}». "
                f"Проходит через ЮKassa как обычная покупка."
            ),
            payload=f"slot:{channel.id}",
            provider_token=_cfg.payment_provider_token,
            currency="RUB",
            prices=[LabeledPrice(label="Проверка оплаты", amount=amount_rub * 100)],
            need_email=_need_email,
            send_email_to_provider=_need_email,
            need_phone_number=_need_phone,
            send_phone_number_to_provider=_need_phone,
            provider_data=_receipt_provider_data(amount_rub),
        )
    except Exception as exc:  # noqa: BLE001 — молчаливый отказ хуже текста ошибки
        logger.warning("test invoice %d RUB failed: %s", amount_rub, exc)
        await message.answer(
            f"❌ Telegram отклонил счёт:\n<code>{html.escape(str(exc))}</code>"
        )
        return
    await message.answer(
        f"Отправил инвойс на {amount_rub} ₽ по каналу «{html.escape(channel.title or '')}». "
        f"Оплати — проверим весь путь: списание, чек на e-mail и запись в /payments."
    )


async def _paycheck_sbp(message: Message, amount_arg: str) -> None:
    """`/paycheck sbp` — сводка магазина по ключу API (платежа нет).
    `/paycheck sbp 15` — настоящий СБП-счёт себе на 15 ₽ (минимум ЮKassa
    — 1 ₽, телеграмного порога в 60 ₽ здесь нет)."""
    if not sbp_visible():
        await message.answer(
            "YOOKASSA_SHOP_ID / YOOKASSA_SECRET_KEY не заданы — СБП выключен."
        )
        return
    if amount_arg:
        if not amount_arg.isdigit() or not 1 <= int(amount_arg) <= 10000:
            await message.answer("Сумма — целое число рублей от 1 до 10000.")
            return
        async with session_maker()() as session:
            result = await session.execute(
                select(Channel).where(Channel.user_id == message.from_user.id)
            )
            channel = result.scalars().first()
        if channel is None:
            await message.answer("Нет ни одного канала — не к чему привязать платёж.")
            return
        await _start_sbp_payment(
            message.bot, message.from_user.id, channel, int(amount_arg)
        )
        return
    try:
        summary = await check_sbp_provider()
    except Exception as exc:  # noqa: BLE001 — текст ошибки и есть диагноз
        await message.answer(
            f"❌ Ключ API не прошёл проверку:\n<code>{html.escape(str(exc))}</code>"
        )
        return
    await message.answer(
        f"✅ Ключ API живой: <code>{html.escape(summary)}</code>\n"
        "Сквозная проверка: <code>/paycheck sbp 15</code> — придёт "
        "настоящий СБП-счёт по первому каналу."
    )


@router.message(Command("paycheck"))
async def cmd_paycheck(message: Message, command: CommandObject) -> None:
    """Админ: проверка рублёвого провайдера.

    Без аргументов — валидация параметров через createInvoiceLink: ЮKassa
    проверяет токен, сумму и чек, но платежа нет. С суммой (`/paycheck 10`) —
    настоящий инвойс себе для сквозной проверки.
    """
    if message.from_user is None or message.from_user.id != _ADMIN_ID:
        return
    token = _cfg.payment_provider_token
    if not token:
        await message.answer("PAYMENT_PROVIDER_TOKEN пуст — рублей нет.")
        return

    arg = (command.args or "").strip().lower()
    # «spb» и кириллица — живые опечатки с первого же прогона (СПБ ≠ СБП)
    head, _, rest = arg.partition(" ")
    if head in ("sbp", "spb", "сбп", "спб"):
        await _paycheck_sbp(message, rest.strip())
        return
    if arg:
        # Нижняя граница — от Telegram: рублёвые счета меньше ~60 ₽ он
        # заворачивает с CURRENCY_TOTAL_AMOUNT_INVALID
        if not arg.isdigit() or not MIN_RUB <= int(arg) <= 10000:
            await message.answer(
                f"Сумма — целое число рублей от {MIN_RUB} до 10000 "
                f"(меньше {MIN_RUB} ₽ Telegram не пропускает)."
            )
            return
        await _send_test_invoice(message, int(arg))
        return
    shape = token.split(":")[1] if token.count(":") >= 2 else "?"
    head = (
        f"Токен: длина {len(token)}, режим <b>{shape}</b> "
        f"(ждём LIVE или TEST)\nЧек: {'on' if _cfg.payment_receipt else 'off'}\n\n"
    )
    try:
        await check_rub_provider(message.bot)
    except Exception as exc:  # noqa: BLE001 — текст ошибки и есть диагноз
        logger.warning("paycheck failed: %s", exc)
        await message.answer(head + f"❌ Провайдер отверг инвойс:\n<code>{html.escape(str(exc))}</code>")
        return
    # Ссылку наружу не даём: её payload не «slot:», платёж по ней отклонится
    # на pre-checkout — выглядит рабочей, а ведёт в тупик.
    await message.answer(
        head + "✅ Провайдер принял параметры (платёж не создавался).\n"
        f"Сквозная проверка: <code>/paycheck {MIN_RUB}</code> — придёт "
        "настоящий счёт по первому каналу."
    )


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
