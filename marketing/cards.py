"""Рендер HIL-карточек лидов и inline-клавиатур. Parse mode — HTML."""
from __future__ import annotations

import html
from datetime import datetime

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from marketing.models import Lead


def _esc(value: object) -> str:
    return html.escape(str(value)) if value is not None else ""


def _days_ago(dt: datetime | None, now: datetime | None = None) -> str:
    if dt is None:
        return "?"
    now = now or datetime.utcnow()
    days = (now - dt).total_seconds() / 86400
    if days < 1:
        return "сегодня"
    return f"{days:.0f} дн назад"


def build_card(lead: Lead, now: datetime | None = None) -> str:
    now = now or datetime.utcnow()
    header = f"🎯 @{_esc(lead.channel_username)}"
    if lead.title:
        header += f" — «{_esc(lead.title)}»"
    meta_bits = []
    if lead.niche:
        meta_bits.append(_esc(lead.niche))
    if lead.subscribers is not None:
        meta_bits.append(f"{lead.subscribers} подп.")
    if meta_bits:
        header += f" ({', '.join(meta_bits)})"

    lines = [header]

    if lead.score is not None:
        lines.append(f"<b>score {lead.score}</b>: {_esc(lead.score_reason or '')}")
    elif lead.status == "enriched":
        lines.append("⚠️ обогатить не удалось (нет превью) — оцени руками")

    metric_bits = []
    if lead.posts_per_week is not None:
        metric_bits.append(f"≈{lead.posts_per_week:.1f} поста/нед")
    if lead.last_post_at is not None:
        metric_bits.append(f"последний {_days_ago(lead.last_post_at, now)}")
    if lead.decay is not None:
        metric_bits.append(f"decay {lead.decay:.2f}")
    if lead.hook:
        metric_bits.append(f"hook: «{_esc(lead.hook)}»")
    if metric_bits:
        lines.append(" · ".join(metric_bits))

    if lead.contact:
        lines.append(f"👤 контакт: {_esc(lead.contact)}")
    if lead.notes:
        lines.append(f"🗒 {_esc(lead.notes)}")

    lines.append("")
    if lead.draft_text:
        lines.append(f"📝 <b>Черновик:</b>\n{_esc(lead.draft_text)}")
    else:
        lines.append("📝 Черновика нет — нажми 🔁, чтобы сгенерировать")

    lines.append("")
    if lead.demo_text:
        lines.append("🎁 Демо по нише: готово (сгенерировано по нише, проверь перед отправкой)")
    else:
        lines.append("🎁 Демо по нише: не сгенерировано")

    lines.append(f"\n<i>id {lead.id} · статус {lead.status}</i>")
    return "\n".join(lines)


def card_keyboard(lead: Lead) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="✅ Одобрить", callback_data=f"mk:appr:{lead.id}"),
            InlineKeyboardButton(text="✏️ Править", callback_data=f"mk:edit:{lead.id}"),
        ],
        [
            InlineKeyboardButton(text="🔁 Другой черновик", callback_data=f"mk:regen:{lead.id}"),
        ],
        [
            InlineKeyboardButton(text="❌ Скип", callback_data=f"mk:skip:{lead.id}"),
            InlineKeyboardButton(text="🚫 Blacklist", callback_data=f"mk:bl:{lead.id}"),
        ],
    ]
    if not lead.demo_text:
        rows.insert(1, [InlineKeyboardButton(
            text="🎁 Сгенерировать демо", callback_data=f"mk:demo:{lead.id}"
        )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def sent_keyboard(lead_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📤 Отправлено", callback_data=f"mk:sent:{lead_id}"),
    ]])


def followup_keyboard(lead_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📤 Фоллоу-ап отправлен", callback_data=f"mk:fusent:{lead_id}"
        )],
        [
            InlineKeyboardButton(text="❌ Тишина → dead", callback_data=f"mk:skip:{lead_id}"),
            InlineKeyboardButton(text="🚫 Blacklist", callback_data=f"mk:bl:{lead_id}"),
        ],
    ])
