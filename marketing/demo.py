"""Демо-дайджест по нише лида: кэш в niche_demos (TTL 7 дней) + генерация.

Генерация — деньги (twitterapi.io): suggest_sources → prevalidate_candidates
→ топ-3 автора → дайджест из уже полученных твитов. Дневной кап демо
проверяется здесь, в коде. Кэш по нише обязателен: 50 лидов в нише «AI» =
1 демо.
"""
from __future__ import annotations

import json
import logging
import re

from core.llm_client import DigestTweet
from core.twitter_cache import TwitterCache
from prompts import build_digest_prompt
from workers.source_scout import prevalidate_candidates

from marketing import repo
from marketing.db import session_maker
from marketing.llm import MarketingLLM

logger = logging.getLogger(__name__)

COUNTER_DEMO = "demo_generations"

# Параметры превалидации из ТЗ
SUGGEST_COUNT = 12
MIN_LIKES = 20
MIN_RETWEETS = 0
TOP_AUTHORS = 3
MAX_DIGEST_TWEETS = 8

_USERNAME_RE = re.compile(r"[a-z0-9_]{1,32}")

# Свободная ниша лида → код ниши прод-промпта (fallback general)
_NICHE_CODE_MARKERS: list[tuple[tuple[str, ...], str]] = [
    (("ai", "ии", "нейросет", "llm", "tech", "технолог", "стартап", "startup"), "tech_ai"),
    (("crypto", "крипт", "defi", "блокчейн", "bitcoin", "битко"), "crypto"),
    (("спорт", "sport", "f1", "формул", "футбол", "football", "хокке", "ufc"), "sports"),
    (("longevity", "долголети", "биохак", "здоров"), "longevity"),
]


def normalize_niche(niche: str) -> str:
    """Свободная строка → ключ кэша: lowercase, схлопнутые пробелы."""
    return re.sub(r"\s+", " ", niche.strip().lower())


def map_niche_code(niche: str) -> str:
    lower = niche.lower()
    for markers, code in _NICHE_CODE_MARKERS:
        if any(m in lower for m in markers):
            return code
    return "general"


def _build_digest_user_prompt(tweets: list[DigestTweet]) -> str:
    # Паттерн workers/publisher._build_digest_with_prompt (приватная функция
    # прода — скопирован формат, не импорт)
    blocks: list[str] = []
    for i, tw in enumerate(tweets, start=1):
        blocks.append(
            f"[Твит #{i}]\n"
            f"Автор: @{tw.username}\n"
            f"URL: {tw.url}\n"
            f"Лайки: {tw.likes}, Ретвиты: {tw.retweets}\n"
            f"Текст: {tw.text}"
        )
    return (
        f"Вот {len(tweets)} твитов за последний период. "
        "Составь дайджест по формату из системного промпта. "
        "Выбирай лучшие 3–5 пунктов.\n\n"
        + "\n\n---\n\n".join(blocks)
    )


async def get_or_create_demo(
    niche: str,
    llm: MarketingLLM,
    cache: TwitterCache,
    daily_demo_cap: int,
) -> tuple[str | None, str]:
    """Возвращает (demo_text | None, note для карточки)."""
    key = normalize_niche(niche)
    if not key:
        return None, "ниша не указана — демо не по чему генерировать"

    async with session_maker()() as session:
        cached = await repo.get_fresh_demo(session, key)
    if cached is not None:
        return cached.demo_text, "из кэша по нише"

    async with session_maker()() as session:
        allowed = await repo.try_consume(session, COUNTER_DEMO, daily_demo_cap)
    if not allowed:
        return None, f"дневной кап демо ({daily_demo_cap}) исчерпан — попробуй завтра"

    suggested = await llm.suggest_sources(niche, count=SUGGEST_COUNT)
    if not suggested:
        return None, llm.last_refusal or "LLM не подобрала источники по нише"

    seen: set[str] = set()
    candidates: list[tuple[str, str]] = []
    for s in suggested:
        u = (s.get("username") or "").lstrip("@").strip().lower()
        if not u or u in seen or not _USERNAME_RE.fullmatch(u):
            continue
        seen.add(u)
        candidates.append((u, (s.get("reason") or "").strip()))
    if not candidates:
        return None, "кандидаты-источники не прошли валидацию"

    stats = await prevalidate_candidates(candidates, MIN_LIKES, MIN_RETWEETS, cache)
    top = stats[:TOP_AUTHORS]
    if not top:
        return None, "ни один источник не прошёл превалидацию по реальным твитам"

    # Твиты уже в кэше после превалидации — повторный get_tweets бесплатный
    digest_tweets: list[DigestTweet] = []
    for cand in top:
        tweets = await cache.get_tweets(cand.username, limit=20)
        texty = [
            t for t in tweets
            if t.text and len(t.text.strip()) >= 40 and not t.is_reply
        ]
        texty.sort(key=lambda t: t.engagement, reverse=True)
        for t in texty[:3]:
            digest_tweets.append(DigestTweet(
                username=t.username, text=t.text, url=t.url,
                likes=t.likes, retweets=t.retweets,
            ))
    digest_tweets = digest_tweets[:MAX_DIGEST_TWEETS]
    if len(digest_tweets) < 2:
        return None, "слишком мало пригодных твитов для демо"

    system_prompt = build_digest_prompt(map_niche_code(niche), legal_rf=True)
    demo_text = await llm.call(
        system_prompt, _build_digest_user_prompt(digest_tweets), max_tokens=1500
    )
    clean = (demo_text or "").strip()
    if not clean or clean.upper().startswith("SKIP"):
        return None, llm.last_refusal or "LLM не собрала дайджест (SKIP/пусто)"

    sources = [c.username for c in top]
    async with session_maker()() as session:
        await repo.save_demo(session, key, clean, sources)
    logger.info("demo: generated for niche %r from %s", key, sources)
    return clean, "сгенерировано по нише, проверь перед отправкой"
