"""Aiogram-хендлеры marketing-бота. Доступ — ТОЛЬКО админу.

Чужим бот молчит (middleware ниже, паттерн bot/middlewares/admin_check.py).
Зависимости (Deps) прокидываются через workflow_data диспетчера.
"""
from __future__ import annotations

import csv
import html
import io
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message, TelegramObject

from marketing import cards, copywriter, demo, repo, workers
from marketing.db import session_maker
from marketing.models import ALL_STATUSES, FUNNEL_STATUSES, MANUAL_MARK_STATUSES
from marketing.workers import Deps

logger = logging.getLogger(__name__)

router = Router(name="marketing")

USERNAME_RE = re.compile(r"^@?([A-Za-z][A-Za-z0-9_]{3,31})$")
FOLLOWUP_DELAY = timedelta(days=4)


class AdminOnlyMiddleware(BaseMiddleware):
    """Молчим для всех, кроме владельца: не выдаём, что бот существует."""

    def __init__(self, admin_user_id: int) -> None:
        self.admin_user_id = admin_user_id

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if user is None or user.id != self.admin_user_id:
            logger.warning(
                "unauthorized access attempt from %s", user.id if user else "unknown"
            )
            return None
        return await handler(event, data)


class ImportStates(StatesGroup):
    waiting_list = State()


class EditStates(StatesGroup):
    waiting_edit = State()


def _esc(value: object) -> str:
    return html.escape(str(value or ""))


# --------------------------------------------------------------------------- #
# Базовые команды
# --------------------------------------------------------------------------- #

HELP_TEXT = """<b>Маркетинг-скаут Twidgest</b> — лиды, карточки, мини-CRM.

<b>Лиды</b>
/addlead @channel [ниша] — добавить канал
/import — список каналов следующим сообщением
/queue — следующая карточка из очереди
/leads [статус] — счётчики и список
/lead &lt;id&gt; — карточка лида

<b>Воронка</b>
/mark &lt;id&gt; replied|trial|paid|dead — двинуть по воронке
/note &lt;id&gt; текст — заметка
/stats — воронка и конверсии
/export — CSV всех лидов

<b>Процесс</b>
/pause, /resume — воркеры (обогащение, фоллоу-апы)

Карточки приходят сами: до 10/день, остальное копится в /queue."""


@router.message(Command("start", "help"))
async def cmd_start(message: Message) -> None:
    await message.answer(HELP_TEXT)


# --------------------------------------------------------------------------- #
# Discovery: /addlead, /import
# --------------------------------------------------------------------------- #

def _parse_lead_line(line: str) -> tuple[str, str | None] | None:
    """'@channel ниша слов' | 'channel,ниша' (CSV) → (username, niche|None)."""
    line = line.strip()
    if not line:
        return None
    if "," in line and " " not in line.split(",", 1)[0]:
        username_part, niche_part = line.split(",", 1)
    else:
        parts = line.split(None, 1)
        username_part = parts[0]
        niche_part = parts[1] if len(parts) > 1 else ""
    m = USERNAME_RE.match(username_part.strip())
    if not m:
        return None
    niche = niche_part.strip() or None
    return m.group(1).lower(), niche


@router.message(Command("addlead"))
async def cmd_addlead(message: Message, command: CommandObject) -> None:
    if not command.args:
        await message.answer("Формат: /addlead @channel [ниша]")
        return
    parsed = _parse_lead_line(command.args)
    if not parsed:
        await message.answer("Не понял username. Формат: /addlead @channel [ниша]")
        return
    username, niche = parsed
    async with session_maker()() as session:
        lead, outcome = await repo.add_lead(session, username, niche, source="manual")
    if outcome == "created":
        await message.answer(
            f"➕ @{_esc(username)} добавлен (id {lead.id}). "
            f"Обогащение — в ближайший цикл (раз в час)."
        )
    elif outcome == "duplicate":
        await message.answer(f"@{_esc(username)} уже есть (id {lead.id}, статус {lead.status}).")
    else:
        await message.answer(f"🚫 @{_esc(username)} в blacklist — не добавляю.")


@router.message(Command("import"))
async def cmd_import(message: Message, state: FSMContext) -> None:
    await state.set_state(ImportStates.waiting_list)
    await message.answer(
        "Пришли список следующим сообщением: по одному <code>@channel [ниша]</code> "
        "на строку, либо CSV <code>username,niche</code>."
    )


@router.message(ImportStates.waiting_list)
async def import_list(message: Message, state: FSMContext) -> None:
    await state.clear()
    lines = (message.text or "").splitlines()
    added, dupes, blocked, bad = [], [], [], []
    async with session_maker()() as session:
        for line in lines:
            if not line.strip():
                continue
            parsed = _parse_lead_line(line)
            if not parsed:
                bad.append(line.strip()[:40])
                continue
            username, niche = parsed
            _, outcome = await repo.add_lead(session, username, niche, source="import")
            {"created": added, "duplicate": dupes, "blacklisted": blocked}[outcome].append(username)
    report = [f"➕ добавлено: {len(added)}"]
    if dupes:
        report.append(f"↩️ дубли: {len(dupes)} ({', '.join(dupes[:5])}…)" if len(dupes) > 5
                      else f"↩️ дубли: {', '.join(dupes)}")
    if blocked:
        report.append(f"🚫 в blacklist: {', '.join(blocked)}")
    if bad:
        report.append(f"⚠️ не распознано: {', '.join(_esc(b) for b in bad)}")
    await message.answer("\n".join(report))


# --------------------------------------------------------------------------- #
# Просмотр: /queue, /leads, /lead
# --------------------------------------------------------------------------- #

@router.message(Command("queue"))
async def cmd_queue(message: Message, deps: Deps) -> None:
    async with session_maker()() as session:
        lead = await repo.get_next_queued_lead(session)
    if lead is None:
        await message.answer("Очередь пуста — все qualified-лиды разобраны.")
        return
    await workers.send_card(message.bot, deps, lead.id, mark_pushed=True)


@router.message(Command("leads"))
async def cmd_leads(message: Message, command: CommandObject) -> None:
    status = (command.args or "").strip().lower() or None
    async with session_maker()() as session:
        counts = await repo.count_by_status(session)
        if status:
            if status not in ALL_STATUSES:
                await message.answer(f"Неизвестный статус. Есть: {', '.join(ALL_STATUSES)}")
                return
            leads = await repo.get_leads_by_status(session, status, limit=25)
            lines = [f"<b>{status}</b> — {counts.get(status, 0)}:"]
            for lead in leads:
                score = f" · score {lead.score}" if lead.score is not None else ""
                lines.append(f"  {lead.id}. @{_esc(lead.channel_username)}{score}")
            await message.answer("\n".join(lines) or "пусто")
            return
    total = sum(counts.values())
    lines = [f"<b>Лиды ({total})</b>"]
    for s in ALL_STATUSES:
        if counts.get(s):
            lines.append(f"  {s}: {counts[s]}")
    lines.append("\n/leads &lt;статус&gt; — список")
    await message.answer("\n".join(lines))


@router.message(Command("lead"))
async def cmd_lead(message: Message, command: CommandObject, deps: Deps) -> None:
    try:
        lead_id = int((command.args or "").strip())
    except ValueError:
        await message.answer("Формат: /lead <id>")
        return
    async with session_maker()() as session:
        lead = await repo.get_lead(session, lead_id)
    if lead is None:
        await message.answer(f"Лида {lead_id} нет.")
        return
    await message.answer(cards.build_card(lead), reply_markup=cards.card_keyboard(lead))


# --------------------------------------------------------------------------- #
# CRM: /mark, /note, /stats, /export, /pause, /resume
# --------------------------------------------------------------------------- #

@router.message(Command("mark"))
async def cmd_mark(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    if len(parts) != 2 or parts[1].lower() not in MANUAL_MARK_STATUSES:
        await message.answer(f"Формат: /mark <id> {'|'.join(MANUAL_MARK_STATUSES)}")
        return
    try:
        lead_id = int(parts[0])
    except ValueError:
        await message.answer("id должен быть числом")
        return
    status = parts[1].lower()
    async with session_maker()() as session:
        lead = await repo.get_lead(session, lead_id)
        if lead is None:
            await message.answer(f"Лида {lead_id} нет.")
            return
        if status == "replied":
            lead.next_followup_at = None  # ответил — фоллоу-ап не нужен
        await repo.set_status(session, lead, status, reason="manual /mark")
    emoji = {"replied": "💬", "trial": "🎉", "paid": "💰", "dead": "💀"}[status]
    await message.answer(f"{emoji} @{_esc(lead.channel_username)} → {status}")


@router.message(Command("note"))
async def cmd_note(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split(None, 1)
    if len(parts) != 2:
        await message.answer("Формат: /note <id> текст")
        return
    try:
        lead_id = int(parts[0])
    except ValueError:
        await message.answer("id должен быть числом")
        return
    async with session_maker()() as session:
        lead = await repo.get_lead(session, lead_id)
        if lead is None:
            await message.answer(f"Лида {lead_id} нет.")
            return
        lead.notes = (f"{lead.notes}\n{parts[1]}" if lead.notes else parts[1])[:2000]
        await repo.log_event(session, lead.id, "note_added", parts[1][:500])
        await session.commit()
    await message.answer("🗒 записал")


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    async with session_maker()() as session:
        counts = await repo.count_by_status(session)
        avg_hours = await repo.avg_response_hours(session)
    lines = ["<b>Воронка</b>"]
    prev_count: int | None = None
    for s in FUNNEL_STATUSES:
        cnt = counts.get(s, 0)
        conv = ""
        if prev_count and prev_count > 0 and s not in ("new",):
            conv = f" ({cnt * 100 // prev_count}%)"
        lines.append(f"  {s}: {cnt}{conv}")
        # Конверсия имеет смысл по цепочке contacted→replied→trial→paid;
        # для верхней части воронки статусы транзитные, база — предыдущий шаг
        if s in ("contacted", "replied", "trial", "paid"):
            prev_count = cnt
        else:
            prev_count = None
    for s in ("dead", "optout"):
        if counts.get(s):
            lines.append(f"  {s}: {counts[s]}")
    if avg_hours is not None:
        lines.append(f"\n⏱ среднее время ответа: {avg_hours:.1f} ч")
    await message.answer("\n".join(lines))


@router.message(Command("export"))
async def cmd_export(message: Message) -> None:
    async with session_maker()() as session:
        leads = await repo.all_leads(session)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "id", "channel_username", "title", "niche", "subscribers", "status",
        "score", "decay", "posts_per_week", "contact", "contacted_at",
        "created_at", "notes",
    ])
    for lead in leads:
        writer.writerow([
            lead.id, lead.channel_username, lead.title or "", lead.niche or "",
            lead.subscribers or "", lead.status, lead.score or "",
            f"{lead.decay:.2f}" if lead.decay is not None else "",
            f"{lead.posts_per_week:.1f}" if lead.posts_per_week is not None else "",
            lead.contact or "", lead.contacted_at or "", lead.created_at or "",
            (lead.notes or "").replace("\n", " | "),
        ])
    data = buf.getvalue().encode("utf-8-sig")  # BOM: чтобы Excel открыл кириллицу
    await message.answer_document(
        BufferedInputFile(data, filename="marketing_leads.csv"),
        caption=f"{len(leads)} лидов",
    )


@router.message(Command("pause"))
async def cmd_pause(message: Message) -> None:
    async with session_maker()() as session:
        await repo.set_setting(session, "paused", "1")
    await message.answer("⏸ Воркеры на паузе (обогащение, фоллоу-апы). /resume — вернуть.")


@router.message(Command("resume"))
async def cmd_resume(message: Message) -> None:
    async with session_maker()() as session:
        await repo.set_setting(session, "paused", "0")
    await message.answer("▶️ Воркеры снова работают.")


# --------------------------------------------------------------------------- #
# Колбэки карточек
# --------------------------------------------------------------------------- #

def _cb_lead_id(callback: CallbackQuery) -> int | None:
    try:
        return int((callback.data or "").split(":")[2])
    except (IndexError, ValueError):
        return None


@router.callback_query(F.data.startswith("mk:appr:"))
async def cb_approve(callback: CallbackQuery) -> None:
    lead_id = _cb_lead_id(callback)
    async with session_maker()() as session:
        lead = await repo.get_lead(session, lead_id) if lead_id else None
        if lead is None:
            await callback.answer("Лид не найден", show_alert=True)
            return
        if not lead.draft_text:
            await callback.answer("Черновика нет — сначала 🔁", show_alert=True)
            return
        await repo.set_status(session, lead, "approved", reason="approved by admin")

        # Финальный текст ОТДЕЛЬНЫМ чистым сообщением — удобно копировать
        await callback.message.answer(lead.draft_text)
        if lead.demo_text:
            await callback.message.answer(lead.demo_text)
        await callback.message.answer(
            f"⬆️ Текст для @{_esc(lead.channel_username)}"
            + (" + демо вторым сообщением" if lead.demo_text else " (демо нет)")
            + ". Как отправишь — жми кнопку.",
            reply_markup=cards.sent_keyboard(lead.id),
        )
    await callback.answer("Одобрено")


@router.callback_query(F.data.startswith("mk:sent:"))
async def cb_sent(callback: CallbackQuery) -> None:
    lead_id = _cb_lead_id(callback)
    async with session_maker()() as session:
        lead = await repo.get_lead(session, lead_id) if lead_id else None
        if lead is None:
            await callback.answer("Лид не найден", show_alert=True)
            return
        now = datetime.utcnow()
        lead.contacted_at = now
        lead.next_followup_at = now + FOLLOWUP_DELAY
        await repo.set_status(session, lead, "contacted", reason="admin sent message")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Статус: contacted, фоллоу-ап через 4 дня")


@router.callback_query(F.data.startswith("mk:fusent:"))
async def cb_followup_sent(callback: CallbackQuery) -> None:
    lead_id = _cb_lead_id(callback)
    async with session_maker()() as session:
        lead = await repo.get_lead(session, lead_id) if lead_id else None
        if lead is None:
            await callback.answer("Лид не найден", show_alert=True)
            return
        lead.followups_sent += 1
        lead.next_followup_at = datetime.utcnow() + FOLLOWUP_DELAY
        await repo.log_event(session, lead.id, "followup_sent")
        await session.commit()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Ок. Снова тишина 4 дня → dead автоматически.")


@router.callback_query(F.data.startswith("mk:skip:"))
async def cb_skip(callback: CallbackQuery) -> None:
    lead_id = _cb_lead_id(callback)
    async with session_maker()() as session:
        lead = await repo.get_lead(session, lead_id) if lead_id else None
        if lead is None:
            await callback.answer("Лид не найден", show_alert=True)
            return
        lead.next_followup_at = None
        await repo.set_status(session, lead, "dead", reason="skipped by admin")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Скип → dead")


@router.callback_query(F.data.startswith("mk:bl:"))
async def cb_blacklist(callback: CallbackQuery) -> None:
    """Opt-out свят: статус optout + blacklist навсегда."""
    lead_id = _cb_lead_id(callback)
    async with session_maker()() as session:
        lead = await repo.get_lead(session, lead_id) if lead_id else None
        if lead is None:
            await callback.answer("Лид не найден", show_alert=True)
            return
        lead.next_followup_at = None
        await repo.blacklist_add(
            session, lead.channel_username, reason="optout/manual", commit=False
        )
        await repo.set_status(session, lead, "optout", reason="blacklisted by admin")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("В blacklist навсегда")


@router.callback_query(F.data.startswith("mk:regen:"))
async def cb_regen(callback: CallbackQuery, deps: Deps) -> None:
    lead_id = _cb_lead_id(callback)
    await callback.answer("Генерирую…")
    async with session_maker()() as session:
        lead = await repo.get_lead(session, lead_id) if lead_id else None
        if lead is None:
            return
        draft = await copywriter.build_draft(deps.llm, lead)
        if not draft:
            note = deps.llm.last_refusal or "LLM не справилась"
            await callback.message.answer(f"⚠️ Черновик не сгенерирован: {note}")
            return
        lead.draft_text = draft
        await repo.log_event(session, lead.id, "draft_regenerated")
        await session.commit()
        await callback.message.answer(
            cards.build_card(lead), reply_markup=cards.card_keyboard(lead)
        )


@router.callback_query(F.data.startswith("mk:demo:"))
async def cb_demo(callback: CallbackQuery, deps: Deps) -> None:
    lead_id = _cb_lead_id(callback)
    async with session_maker()() as session:
        lead = await repo.get_lead(session, lead_id) if lead_id else None
    if lead is None:
        await callback.answer("Лид не найден", show_alert=True)
        return
    if not lead.niche:
        await callback.answer("У лида нет ниши — задай через /note и попроси новый черновик", show_alert=True)
        return
    await callback.answer("Генерирую демо (может занять минуту)…")
    demo_text, note = await demo.get_or_create_demo(
        lead.niche, deps.llm, deps.cache, deps.cfg.daily_demo_cap
    )
    async with session_maker()() as session:
        lead = await repo.get_lead(session, lead_id)
        if demo_text:
            lead.demo_text = demo_text
            await repo.log_event(session, lead.id, "demo_attached", note)
            await session.commit()
            await callback.message.answer(
                cards.build_card(lead), reply_markup=cards.card_keyboard(lead)
            )
        else:
            await callback.message.answer(f"⚠️ Демо не получилось: {note}")


@router.callback_query(F.data.startswith("mk:edit:"))
async def cb_edit(callback: CallbackQuery, state: FSMContext) -> None:
    lead_id = _cb_lead_id(callback)
    if lead_id is None:
        await callback.answer("Лид не найден", show_alert=True)
        return
    await state.set_state(EditStates.waiting_edit)
    await state.update_data(lead_id=lead_id)
    await callback.message.answer(
        "Пришли правки следующим сообщением: указания («короче», «убери про VK») "
        "— перепишет LLM, или готовый текст (200+ знаков) — возьму как есть."
    )
    await callback.answer()


@router.message(EditStates.waiting_edit)
async def edit_draft(message: Message, state: FSMContext, deps: Deps) -> None:
    data = await state.get_data()
    await state.clear()
    lead_id = data.get("lead_id")
    text = (message.text or "").strip()
    if not text:
        await message.answer("Пустое сообщение — правки отменены.")
        return
    async with session_maker()() as session:
        lead = await repo.get_lead(session, lead_id)
        if lead is None:
            await message.answer("Лид пропал.")
            return
        if len(text) >= 200:
            # Похоже на готовый текст — берём как есть (но ссылки запрещены)
            if re.search(r"https?://|t\.me/", text, re.I):
                await message.answer("⚠️ В первом сообщении нельзя ссылки (антиспам TG). Не сохранил.")
                return
            lead.draft_text = text
            await repo.log_event(session, lead.id, "draft_manual_edit")
            await session.commit()
        else:
            new_draft = await copywriter.rewrite_draft(deps.llm, lead, text)
            if not new_draft:
                note = deps.llm.last_refusal or "LLM не справилась, черновик прежний"
                await message.answer(f"⚠️ {note}")
                return
            lead.draft_text = new_draft
            await repo.log_event(session, lead.id, "draft_llm_edit", text[:200])
            await session.commit()
        await message.answer(
            cards.build_card(lead), reply_markup=cards.card_keyboard(lead)
        )
