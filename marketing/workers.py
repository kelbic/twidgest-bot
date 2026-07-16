"""Фоновые циклы marketing-бота.

- run_enrich_cycle: каждый час, батч ≤5 лидов status=new → обогащение по
  t.me/s/ → квалификация (эвристики + один батч-вызов LLM) → авто-пуш
  карточек админу (не более дневного капа).
- run_crm_cycle: ежедневно 10:00 МСК (07:00 UTC) — фоллоу-апы и сводка.

Ошибки не роняют батч: per-lead try/except, как в прод-воркерах.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from aiogram import Bot

from core.twitter_cache import TwitterCache

from marketing import cards, copywriter, repo
from marketing.config import MarketingConfig
from marketing.db import session_maker
from marketing.enrich import TelegramPreviewClient, compute_metrics
from marketing.llm import MarketingLLM
from marketing.models import Lead
from marketing.qualify import (
    MIN_QUALIFY_SCORE,
    combine_score,
    llm_qualify_batch,
    score_lead_heuristics,
)

logger = logging.getLogger(__name__)

ENRICH_BATCH = 5
COUNTER_CARDS = "cards_pushed"
FOLLOWUP_AFTER_DAYS = 4
MAX_FOLLOWUPS = 1


@dataclass
class Deps:
    """Зависимости процесса: по одному инстансу на процесс (см. ТЗ)."""

    cfg: MarketingConfig
    llm: MarketingLLM
    cache: TwitterCache
    preview: TelegramPreviewClient


async def _is_paused() -> bool:
    async with session_maker()() as session:
        return (await repo.get_setting(session, "paused", "0")) == "1"


# --------------------------------------------------------------------------- #
# Enrichment + qualification
# --------------------------------------------------------------------------- #

async def run_enrich_cycle(bot: Bot, deps: Deps) -> None:
    if await _is_paused():
        logger.info("enrich: paused, skipping cycle")
        return

    async with session_maker()() as session:
        batch = await repo.get_new_leads_batch(session, ENRICH_BATCH)
        lead_ids = [lead.id for lead in batch]
    if lead_ids:
        logger.info("enrich: cycle started, %d leads", len(lead_ids))

    qualify_inputs: list[dict] = []
    for lead_id in lead_ids:
        try:
            data = await _enrich_lead(deps, lead_id)
            if data:
                qualify_inputs.append(data)
        except Exception:
            logger.exception("enrich: lead %d failed", lead_id)

    if qualify_inputs:
        try:
            await _qualify_batch(deps, qualify_inputs)
        except Exception:
            logger.exception("qualify: batch failed, leaving leads as-is")

    try:
        await push_cards(bot, deps)
    except Exception:
        logger.exception("cards: push failed")


async def _enrich_lead(deps: Deps, lead_id: int) -> dict | None:
    preview = None
    async with session_maker()() as session:
        lead = await repo.get_lead(session, lead_id)
        if lead is None or lead.status != "new":
            return None
        preview = await deps.preview.fetch_preview(lead.channel_username)

        lead.enriched_at = datetime.utcnow()
        if preview.title:
            lead.title = preview.title
        if preview.description:
            lead.description = preview.description
        if preview.subscribers is not None:
            lead.subscribers = preview.subscribers
        if preview.contact:
            lead.contact = preview.contact

        if not preview.available:
            # Приватный / без превью: пустые метрики, пометка «оцени руками».
            await repo.set_status(
                session, lead, "enriched", reason="preview_unavailable", commit=False
            )
            await repo.log_event(session, lead.id, "enrich_failed", "no preview")
            await session.commit()
            logger.info("enrich: @%s — no preview, manual review", lead.channel_username)
            return None

        metrics = compute_metrics(preview.post_dates)
        lead.posts_per_week = metrics.posts_per_week
        lead.gap_cv = metrics.gap_cv
        lead.decay = metrics.decay
        lead.recent_ppw = metrics.recent_ppw
        lead.older_ppw = metrics.older_ppw
        lead.last_post_at = metrics.last_post_at
        await repo.set_status(session, lead, "enriched", commit=False)
        await repo.log_event(
            session, lead.id, "enrich_samples",
            json.dumps(preview.post_texts[:5], ensure_ascii=False),
        )
        await session.commit()

        metrics_line = (
            f"{metrics.posts_per_week or 0:.1f} пост/нед, "
            f"decay={metrics.decay if metrics.decay is not None else 'n/a'}, "
            f"подписчиков {lead.subscribers or '?'}"
        )
        return {
            "lead_id": lead.id,
            "username": lead.channel_username,
            "description": lead.description,
            "samples": preview.post_texts[:5],
            "metrics_line": metrics_line,
            "metrics": metrics,
        }


async def _qualify_batch(deps: Deps, inputs: list[dict]) -> None:
    """Эвристики + один батч-вызов LLM. LLM упал → fail-open (только эвристики)."""
    verdicts = await llm_qualify_batch(deps.llm, inputs)

    async with session_maker()() as session:
        for item in inputs:
            lead = await repo.get_lead(session, item["lead_id"])
            if lead is None:
                continue
            heur = score_lead_heuristics(
                item["metrics"], lead.subscribers, lead.niche, lead.contact
            )
            llm_v = verdicts.get(lead.channel_username)
            if llm_v:
                if llm_v.niche_guess and not lead.niche:
                    lead.niche = llm_v.niche_guess
                    # Ниша могла появиться только сейчас — пересчитаем эвристики
                    heur = score_lead_heuristics(
                        item["metrics"], lead.subscribers, lead.niche, lead.contact
                    )
                lead.hook = llm_v.hook
                lead.score = combine_score(heur.score, llm_v.fit)
            else:
                lead.score = heur.score
            lead.score_reason = "; ".join(heur.reasons)[:1000]

            new_status = "dead" if lead.score < MIN_QUALIFY_SCORE else "qualified"
            await repo.set_status(
                session, lead, new_status,
                reason=f"score={lead.score}", commit=False,
            )
            await session.commit()
            logger.info(
                "qualify: @%s → score %d, %s",
                lead.channel_username, lead.score, new_status,
            )


# --------------------------------------------------------------------------- #
# Авто-пуш карточек
# --------------------------------------------------------------------------- #

async def push_cards(bot: Bot, deps: Deps) -> int:
    """Пушит админу карточки новых qualified/enriched лидов в рамках капа."""
    pushed = 0
    async with session_maker()() as session:
        pending = await repo.get_unpushed_cards(session, limit=deps.cfg.daily_cards_cap)
        lead_ids = [lead.id for lead in pending]

    for lead_id in lead_ids:
        async with session_maker()() as session:
            allowed = await repo.try_consume(
                session, COUNTER_CARDS, deps.cfg.daily_cards_cap
            )
        if not allowed:
            logger.info("cards: daily cap reached, rest stays in /queue")
            break
        try:
            await send_card(bot, deps, lead_id)
            pushed += 1
        except Exception:
            logger.exception("cards: failed to push lead %d", lead_id)
    return pushed


async def send_card(bot: Bot, deps: Deps, lead_id: int, mark_pushed: bool = True) -> None:
    """Готовит черновик (если нет) и шлёт карточку админу."""
    async with session_maker()() as session:
        lead = await repo.get_lead(session, lead_id)
        if lead is None:
            return
        if not lead.draft_text:
            draft = await copywriter.build_draft(deps.llm, lead)
            if draft:
                lead.draft_text = draft
                await repo.log_event(session, lead.id, "draft_generated")
        # Демо подтягиваем из кэша по нише (без генерации — она по кнопке)
        if not lead.demo_text and lead.niche:
            from marketing.demo import normalize_niche
            cached = await repo.get_fresh_demo(session, normalize_niche(lead.niche))
            if cached:
                lead.demo_text = cached.demo_text
        if mark_pushed:
            lead.card_pushed_at = datetime.utcnow()
        await repo.log_event(session, lead.id, "card_pushed")
        await session.commit()

        await bot.send_message(
            deps.cfg.admin_user_id,
            cards.build_card(lead),
            reply_markup=cards.card_keyboard(lead),
        )


# --------------------------------------------------------------------------- #
# CRM: фоллоу-апы + дневная сводка (10:00 МСК = 07:00 UTC)
# --------------------------------------------------------------------------- #

async def run_crm_cycle(bot: Bot, deps: Deps) -> None:
    if await _is_paused():
        logger.info("crm: paused, skipping cycle")
        return

    async with session_maker()() as session:
        due = await repo.get_due_followups(session)
        due_ids = [(lead.id, lead.followups_sent) for lead in due]

    for lead_id, followups_sent in due_ids:
        try:
            if followups_sent >= MAX_FOLLOWUPS:
                # Один фоллоу-ап уже был, снова тишина → dead
                async with session_maker()() as session:
                    lead = await repo.get_lead(session, lead_id)
                    if lead is None:
                        continue
                    lead.next_followup_at = None
                    await repo.set_status(session, lead, "dead", reason="no reply after followup")
                    await bot.send_message(
                        deps.cfg.admin_user_id,
                        f"💤 @{lead.channel_username}: тишина после фоллоу-апа → dead",
                    )
                continue
            await _send_followup_card(bot, deps, lead_id)
        except Exception:
            logger.exception("crm: followup for lead %d failed", lead_id)

    try:
        await _send_daily_summary(bot, deps)
    except Exception:
        logger.exception("crm: summary failed")


async def _send_followup_card(bot: Bot, deps: Deps, lead_id: int) -> None:
    async with session_maker()() as session:
        lead = await repo.get_lead(session, lead_id)
        if lead is None:
            return
        followup = await copywriter.build_followup(deps.llm, lead)
        await repo.log_event(
            session, lead.id, "followup_drafted",
            json.dumps({"text": followup}, ensure_ascii=False) if followup else None,
        )
        await session.commit()

        text = (
            f"⏰ Пора фоллоу-апить @{lead.channel_username} "
            f"(первое сообщение {_fmt_days(lead.contacted_at)})\n\n"
            f"📝 Черновик фоллоу-апа:\n{followup or '(LLM не собрала — напиши руками)'}"
        )
        await bot.send_message(
            deps.cfg.admin_user_id, text,
            reply_markup=cards.followup_keyboard(lead.id),
        )


def _fmt_days(dt: datetime | None) -> str:
    if dt is None:
        return "неизвестно когда"
    days = (datetime.utcnow() - dt).total_seconds() / 86400
    return f"{days:.0f} дн назад"


async def _send_daily_summary(bot: Bot, deps: Deps) -> None:
    async with session_maker()() as session:
        counts = await repo.count_by_status(session)
        llm_used = await repo.get_counter(session, "llm_calls")
        demo_used = await repo.get_counter(session, "demo_generations")
    queue = counts.get("qualified", 0)
    fresh = counts.get("new", 0)
    if not counts:
        return  # пустая база — нечего сводить
    funnel = " · ".join(f"{s}: {counts.get(s, 0)}" for s in
                        ("new", "qualified", "contacted", "replied", "trial", "paid"))
    await bot.send_message(
        deps.cfg.admin_user_id,
        f"📊 Утренняя сводка\n{funnel}\n"
        f"В очереди карточек: {queue}, необогащённых: {fresh}\n"
        f"Вчера/сегодня капы: LLM {llm_used}/{deps.cfg.daily_llm_cap}, "
        f"демо {demo_used}/{deps.cfg.daily_demo_cap}\n"
        f"/queue — следующая карточка",
    )
