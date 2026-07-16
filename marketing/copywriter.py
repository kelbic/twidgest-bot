"""Черновики первых сообщений и фоллоу-апов. Один LLM-вызов на черновик.

Жёсткие правила (антиспам-эвристики Telegram + честность):
- БЕЗ ссылок в первом сообщении — проверяется в коде после генерации;
- без обещаний цифр роста, «уникальных возможностей», давления;
- персонализация по реальным фактам из канала лида (hook/метрики).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime

from marketing.llm import MarketingLLM
from marketing.models import Lead

logger = logging.getLogger(__name__)

DRAFT_MIN_LEN = 200   # ТЗ: 350–600 знаков; допускаем небольшой недолёт LLM
DRAFT_MAX_LEN = 800
_LINK_RE = re.compile(r"https?://|www\.|t\.me/", re.I)

DRAFT_SYSTEM = """Ты пишешь короткое первое сообщение админу Telegram-канала от лица
разработчика-одиночки. Продукт: бот, который сам ведёт тематический канал —
находит виральные посты в X/VK по нише, фильтрует и переписывает на русский.
Пиши по-русски, 350–600 знаков, БЕЗ ссылок, максимум один эмодзи.
Начни с hook из входа: это должна быть боль, а не констатация — если кадэнс
канала падает (decay < 0.6), мягко и с эмпатией отрази тренд («вижу, весной
выходило по 5 постов в неделю, сейчас по одному — тяжело тянуть руками?»),
никогда не формулируй это как упрёк или аудит. Один факт о продукте.
Представься честно: «я сделал бота, который...». Предложи прислать готовый
демо-дайджест по их теме. Закончи коротким вопросом.
Никакого напора, никаких обещаний роста, никаких «уникальных возможностей».
Тон: разработчик пишет коллеге, не менеджер по продажам.
Верни только текст сообщения."""

FOLLOWUP_SYSTEM = """Ты пишешь ЕДИНСТВЕННЫЙ мягкий фоллоу-ап админу Telegram-канала,
которому 4 дня назад отправили первое сообщение (текст ниже) и не получили
ответа. По-русски, 150–300 знаков, БЕЗ ссылок, без эмодзи.
Никакого давления и обид: короткое напоминание + готовность прислать
демо-дайджест по теме канала. Если человеку неинтересно — это нормально,
дай это понять. Верни только текст сообщения."""

REWRITE_SYSTEM = """Ты правишь черновик первого сообщения админу Telegram-канала
по указаниям автора. Сохрани правила: по-русски, 350–600 знаков, БЕЗ ссылок,
максимум один эмодзи, без обещаний роста и давления, честный тон
разработчика-одиночки. Верни только исправленный текст сообщения."""


def _fact_block(lead: Lead, now: datetime | None = None) -> str:
    now = now or datetime.utcnow()
    facts = [f"Канал: @{lead.channel_username}"]
    if lead.title:
        facts.append(f"Название: {lead.title}")
    if lead.niche:
        facts.append(f"Ниша: {lead.niche}")
    if lead.subscribers:
        facts.append(f"Подписчиков: {lead.subscribers}")
    if lead.recent_ppw is not None and lead.older_ppw is not None:
        facts.append(
            f"Кадэнс: было {lead.older_ppw:.1f} пост/нед, стало {lead.recent_ppw:.1f}"
            + (f" (decay {lead.decay:.2f})" if lead.decay is not None else "")
        )
    if lead.last_post_at:
        days = (now - lead.last_post_at).total_seconds() / 86400
        facts.append(f"Последний пост: {days:.0f} дн назад")
    if lead.hook:
        facts.append(f"hook: {lead.hook}")
    if lead.description:
        facts.append(f"Описание канала: {lead.description[:300]}")
    return "\n".join(facts)


def _validate_draft(text: str | None, max_len: int = DRAFT_MAX_LEN) -> str | None:
    """Ссылки запрещены, длина в рамках. Невалидно → None."""
    if not text:
        return None
    clean = text.strip().strip('"')
    if not clean or len(clean) > max_len or len(clean) < 50:
        return None
    if _LINK_RE.search(clean):
        logger.warning("copywriter: draft contained a link, rejected")
        return None
    return clean


async def build_draft(llm: MarketingLLM, lead: Lead) -> str | None:
    """Черновик первого сообщения. Одна попытка + один повтор при невалидности."""
    user = _fact_block(lead)
    for attempt in (1, 2):
        raw = await llm.call(DRAFT_SYSTEM, user, max_tokens=600, temperature=0.5)
        draft = _validate_draft(raw)
        if draft:
            return draft
        if raw is None:
            return None  # кап/сбой LLM — повтор не поможет
        user = _fact_block(lead) + "\n\nВАЖНО: в прошлый раз ты нарушил правила (ссылка или длина). Строго без ссылок, 350–600 знаков."
    return None


async def build_followup(llm: MarketingLLM, lead: Lead) -> str | None:
    user = (
        _fact_block(lead)
        + "\n\nПервое сообщение (без ответа):\n"
        + (lead.draft_text or "(текст не сохранился)")
    )
    raw = await llm.call(FOLLOWUP_SYSTEM, user, max_tokens=300, temperature=0.5)
    return _validate_draft(raw, max_len=400)


async def rewrite_draft(
    llm: MarketingLLM, lead: Lead, instructions: str
) -> str | None:
    user = (
        f"Черновик:\n{lead.draft_text or '(пусто)'}\n\n"
        f"Указания автора: {instructions}\n\n"
        f"Факты для персонализации:\n{_fact_block(lead)}"
    )
    raw = await llm.call(REWRITE_SYSTEM, user, max_tokens=600, temperature=0.4)
    return _validate_draft(raw)
