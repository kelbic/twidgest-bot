"""Ревьювер-ранкер кандидатов для viral_picker.

Один батч-вызов дешёвой LLM на цикл канала: оценивает всех топ-кандидатов
сразу по интересности и помечает явный мусор (engagement-bait, реклама).

Философия: это РАНКЕР, а не ещё один фильтр. Каналы уже молчали из-за
избыточной фильтрации, поэтому:
- junk=True — только для очевидного мусора; всё спорное проходит дальше
  и просто получает низкий interest;
- при любом сбое (LLM недоступна, битый JSON) возвращаем None — вызывающий
  код ОБЯЗАН fail-open на исходный порядок по engagement.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Текст твита в промпте режем, чтобы батч из 5 кандидатов оставался дешёвым
MAX_TEXT_CHARS = 400

RANKER_SYSTEM = """Ты выпускающий редактор русскоязычного тематического Telegram-канала.
Тебе дают список твитов-кандидатов на публикацию. Для КАЖДОГО оцени:

1. interest (1-10) — насколько пост интересен подписчикам канала по его теме:
   новизна, конкретика (числа, факты, события), значимость. 8-10 — настоящая
   новость или инсайт; 4-7 — нормальный контент; 1-3 — вода, банальность,
   пустая реакция, оффтоп для темы канала.

2. junk (true/false) — true ТОЛЬКО для очевидного мусора:
   - engagement-bait: «ретвитни если...», «отметь друга», giveaway, follow-for-follow
   - реклама, реферальные/промо-ссылки, продажа курсов
   - бессодержательные реакции («wow», «this», «lol», одни эмодзи)
   Если сомневаешься — junk=false. Спорный или скучный контент НЕ мусор,
   просто ставь ему низкий interest.

3. why — причина в 2-4 словах, по-русски.

Верни СТРОГО JSON-массив без markdown и комментариев:
[{"id": 1, "interest": 7, "junk": false, "why": "конкретные цифры"}]
Ровно по одному объекту на каждый входной id."""


@dataclass
class Verdict:
    interest: int
    junk: bool
    why: str


def _strip_fences(raw: str) -> str:
    clean = raw.strip()
    if clean.startswith("```"):
        parts = clean.split("```")
        if len(parts) >= 2:
            clean = parts[1]
        clean = clean.removeprefix("json").strip()
    return clean


async def rank_candidates(llm, channel, items) -> dict[int, Verdict] | None:
    """Оценивает кандидатов одним вызовом LLM.

    Args:
        llm: OpenRouterClient (дешёвая модель — llm_default)
        channel: Channel (title/niche/description дают контекст темы)
        items: list[DigestQueueItem]

    Returns:
        {queue_item_id: Verdict} или None при сбое.
        None означает «ранжирование недоступно» — вызывающий код должен
        работать в исходном порядке (fail-open), а не молчать.
    """
    if not items:
        return {}

    topic_bits = [channel.title or "", channel.niche or ""]
    if channel.description:
        topic_bits.append(channel.description[:200])
    topic = " / ".join(b for b in topic_bits if b)

    blocks: list[str] = []
    for it in items:
        text = (it.text or "").strip()
        if len(text) > MAX_TEXT_CHARS:
            text = text[:MAX_TEXT_CHARS] + "…"
        blocks.append(
            f"[id={it.id}] @{it.twitter_username} "
            f"(лайки: {it.likes}, ретвиты: {it.retweets})\n{text}"
        )
    user_prompt = (
        f"Тема канала: {topic}\n\n"
        f"Кандидаты ({len(items)} шт.):\n\n" + "\n\n---\n\n".join(blocks)
    )

    raw = await llm._call_with_retry(
        RANKER_SYSTEM, user_prompt, max_tokens=600, temperature=0.1
    )
    if not raw:
        return None

    try:
        data = json.loads(_strip_fences(raw))
    except Exception:
        logger.warning("Ranker returned non-JSON: %s", raw[:200])
        return None
    if not isinstance(data, list):
        logger.warning("Ranker JSON is not a list: %s", raw[:200])
        return None

    valid_ids = {it.id for it in items}
    out: dict[int, Verdict] = {}
    for row in data:
        if not isinstance(row, dict):
            continue
        try:
            rid = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        if rid not in valid_ids:
            continue
        try:
            interest = max(1, min(10, int(row.get("interest", 5))))
        except (TypeError, ValueError):
            interest = 5
        out[rid] = Verdict(
            interest=interest,
            junk=bool(row.get("junk", False)),
            why=str(row.get("why", "")).strip()[:64],
        )

    if not out:
        logger.warning("Ranker parsed 0 valid verdicts from: %s", raw[:200])
        return None
    return out
