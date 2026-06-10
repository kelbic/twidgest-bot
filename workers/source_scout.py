"""Скаут источников: подбирает и проверяет новые X-аккаунты для канала.

Запускается ТОЛЬКО по запросу (кнопка в health-алерте или /scout) — поиск
и превалидация стоят денег (twitterapi.io + LLM). Никогда не меняет
источники сам: результат — карточка владельцу с кнопками (HIL).

Три уровня проверки кандидата:
1. ФОРМА, в коде (prevalidate_candidates, переиспользуется в
   /createchannel ai): доля текстовых твитов, активность (< 21 дня тишины),
   частота постинга, пороги engagement канала.
2. ТЕМА, один батч-вызов LLM (apply_topic_relevance): какая доля реальных
   твитов кандидата относится к теме канала. Закрывает кейс «Илон Маск»:
   форма идеальна, 99 тв/нед, но про тему канала — малая доля.
   topic_share < MIN_TOPIC_SHARE → кандидат выпадает; иначе отдача
   умножается на долю — «даст ~N постов/нед» означает посты ПО ТЕМЕ.
   Fail-open: LLM упала / битый JSON → кандидаты идут без поправки.
3. ЧЕЛОВЕК (HIL): карточка с метриками, добавление только кнопкой.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

from core.llm_client import OpenRouterClient
from core.twitter_cache import TwitterCache
from db.models import Channel

logger = logging.getLogger(__name__)

# Сколько кандидатов просим у LLM (до превалидации)
LLM_SUGGEST_COUNT = 12
# Минимум твитов у живого аккаунта (меньше — мёртвый/закрытый/выдуманный)
MIN_TWEETS = 5
# Минимальная доля "текстовых" твитов (>= 40 знаков, не reply)
MIN_TEXT_SHARE = 0.35
# Минимум твитов из выборки, которые прошли бы фильтры канала
MIN_PASSING = 2
# Последний твит старше этого — автор неактивен, в новостной канал не годится
MAX_SILENCE_DAYS = 21
# Минимальная доля твитов ПО ТЕМЕ канала (LLM-оценка); ниже — кандидат выпадает
MIN_TOPIC_SHARE = 0.4
# Частота ниже этой помечается «⚠️ редко» в карточке (но не отсеивается)
RARE_TWEETS_PER_WEEK = 3.0
# Сколько лучших кандидатов показываем владельцу
TOP_N = 5
# Параллельность fetch'ей при превалидации
PROBE_CONCURRENCY = 3
# Сколько текстов кандидата отправляем LLM на оценку темы (и длина каждого)
TOPIC_SAMPLE_TEXTS = 8
TOPIC_SAMPLE_CHARS = 200

_USERNAME_RE = re.compile(r"[a-z0-9_]{1,32}")

_TOPIC_SYSTEM = """Ты оцениваешь авторов X (Twitter) как источники для тематического Telegram-канала.
Тебе дают тему канала и для каждого автора — выдержки из его реальных последних твитов.
Для КАЖДОГО автора оцени, какая ДОЛЯ его твитов относится к теме канала (0-100).
Считай относящимися и смежные твиты (новости индустрии, события вокруг темы).
Личное, политика, другие индустрии, мемы без связи с темой — не относятся.

Верни СТРОГО JSON-объект без markdown и комментариев:
{"username1": 85, "username2": 20}
Ровно по одному ключу на каждого автора из входа."""


@dataclass
class CandidateStats:
    """Кандидат, прошедший превалидацию."""

    username: str            # lowercase, без @
    reason: str              # объяснение, почему релевантен теме
    total: int               # всего твитов в выборке
    text_share: float        # доля текстовых твитов (0..1)
    median_likes: int        # медиана лайков по текстовым твитам
    passing: int             # сколько твитов прошло бы фильтры канала
    tweets_per_week: float   # частота постинга автора
    est_posts_per_week: float  # отдача каналу (после topic_share — по теме)
    topic_share: float | None = None   # доля твитов по теме (LLM), 0..1
    sample_texts: list[str] = field(default_factory=list)  # для LLM, в БД не идёт

    def stats_line(self) -> str:
        """Человекочитаемая строка метрик — хранится в ScoutSuggestion.stats."""
        rare = " ⚠️ пишет редко" if self.tweets_per_week < RARE_TWEETS_PER_WEEK else ""
        topic = (
            f" · по теме {int(self.topic_share * 100)}%"
            if self.topic_share is not None else ""
        )
        return (
            f"текстовых {int(self.text_share * 100)}% · ❤ медиана {self.median_likes} · "
            f"фильтр {self.passing}/{self.total}{topic} · ≈{self.tweets_per_week:.0f} тв/нед — "
            f"даст ~{self.est_posts_per_week:.1f} поста/нед{rare}"
        )


def _build_topic(channel: Channel) -> str:
    bits = [channel.title or "", channel.niche or ""]
    if channel.description:
        bits.append(channel.description[:300])
    return " / ".join(b for b in bits if b)


async def prevalidate_candidates(
    candidates: list[tuple[str, str]],
    min_likes: int,
    min_retweets: int,
    cache: TwitterCache,
) -> list[CandidateStats]:
    """Проверка ФОРМЫ по реальным последним твитам (без LLM).

    candidates — [(username_lowercase, reason)], уже без дублей и без
    уже подключённых. Возвращает прошедших гейты, отсортированных по
    est_posts_per_week, с sample_texts для последующей проверки темы.
    """
    sem = asyncio.Semaphore(PROBE_CONCURRENCY)
    results: list[CandidateStats] = []
    now = datetime.utcnow()

    async def _probe(username: str, reason: str) -> None:
        async with sem:
            try:
                tweets = await cache.get_tweets(username, limit=20)
            except Exception:
                logger.exception("scout: fetch failed for @%s", username)
                return

        if len(tweets) < MIN_TWEETS:
            logger.info(
                "scout: @%s dropped — only %d tweets (dead/private/hallucinated)",
                username, len(tweets),
            )
            return

        dates = sorted(t.parsed_created_at for t in tweets)
        newest, oldest = dates[-1], dates[0]

        silence_days = (now - newest).days
        if silence_days > MAX_SILENCE_DAYS:
            logger.info(
                "scout: @%s dropped — last tweet %d days ago (inactive)",
                username, silence_days,
            )
            return

        span_days = max((newest - oldest).total_seconds() / 86400.0, 0.05)
        tweets_per_week = min((len(tweets) - 1) / span_days * 7.0, 99.0)

        texty = [
            t for t in tweets
            if t.text and len(t.text.strip()) >= 40 and not t.is_reply
        ]
        text_share = len(texty) / len(tweets)
        if text_share < MIN_TEXT_SHARE:
            logger.info(
                "scout: @%s dropped — text share %.0f%% (media-heavy)",
                username, text_share * 100,
            )
            return

        passing = sum(
            1 for t in texty
            if t.likes >= min_likes and t.retweets >= min_retweets
        )
        if passing < MIN_PASSING:
            logger.info(
                "scout: @%s dropped — only %d tweets pass thresholds "
                "(min_likes=%d, min_retweets=%d)",
                username, passing, min_likes, min_retweets,
            )
            return

        likes_sorted = sorted(t.likes for t in texty)
        median_likes = likes_sorted[len(likes_sorted) // 2]
        est_posts_per_week = passing / len(tweets) * tweets_per_week

        samples = [
            t.text.strip()[:TOPIC_SAMPLE_CHARS]
            for t in texty[:TOPIC_SAMPLE_TEXTS]
        ]

        results.append(CandidateStats(
            username=username,
            reason=reason[:200],
            total=len(tweets),
            text_share=text_share,
            median_likes=median_likes,
            passing=passing,
            tweets_per_week=tweets_per_week,
            est_posts_per_week=est_posts_per_week,
            sample_texts=samples,
        ))

    await asyncio.gather(*(_probe(u, r) for u, r in candidates))
    results.sort(key=lambda c: (c.est_posts_per_week, c.median_likes), reverse=True)
    return results


def _strip_fences(raw: str) -> str:
    clean = raw.strip()
    if clean.startswith("```"):
        parts = clean.split("```")
        if len(parts) >= 2:
            clean = parts[1]
        clean = clean.removeprefix("json").strip()
    return clean


async def apply_topic_relevance(
    llm: OpenRouterClient,
    topic: str,
    candidates: list[CandidateStats],
) -> list[CandidateStats]:
    """Проверка ТЕМЫ: один батч-вызов LLM на всех кандидатов.

    topic_share < MIN_TOPIC_SHARE → кандидат выпадает (кейс «Маск»:
    форма идеальна, тема — малая доля). Иначе отдача умножается на долю.
    Fail-open: сбой LLM / битый JSON / автор не в ответе → кандидат идёт
    без поправки (topic_share=None), карточка покажет метрики без «по теме».
    """
    if not candidates:
        return candidates

    blocks = []
    for c in candidates:
        joined = "\n".join(f"- {t}" for t in c.sample_texts) or "- (нет текстов)"
        blocks.append(f"@{c.username}:\n{joined}")
    user_prompt = (
        f"Тема канала: {topic}\n\n"
        f"Авторы и их твиты:\n\n" + "\n\n".join(blocks)
    )

    raw = await llm._call_with_retry(
        _TOPIC_SYSTEM, user_prompt, max_tokens=300, temperature=0.1
    )
    if not raw:
        logger.warning("scout: topic relevance LLM failed, fail-open")
        return candidates

    try:
        data = json.loads(_strip_fences(raw))
        assert isinstance(data, dict)
    except Exception:
        logger.warning("scout: topic relevance non-JSON: %s", raw[:200])
        return candidates

    shares = {str(k).lstrip("@").lower(): v for k, v in data.items()}
    kept: list[CandidateStats] = []
    for c in candidates:
        v = shares.get(c.username)
        try:
            share = max(0.0, min(100.0, float(v))) / 100.0
        except (TypeError, ValueError):
            kept.append(c)  # fail-open per-candidate
            continue
        if share < MIN_TOPIC_SHARE:
            logger.info(
                "scout: @%s dropped — only %.0f%% of tweets on topic",
                c.username, share * 100,
            )
            continue
        c.topic_share = share
        c.est_posts_per_week = c.est_posts_per_week * share
        kept.append(c)

    kept.sort(key=lambda c: (c.est_posts_per_week, c.median_likes), reverse=True)
    return kept


async def discover_sources(
    channel: Channel,
    llm: OpenRouterClient,
    cache: TwitterCache,
) -> list[CandidateStats]:
    """Полный цикл скаута: LLM-подбор → форма (код) → тема (LLM) → топ-N."""
    existing = {
        s.twitter_username.lower().lstrip("@")
        for s in channel.channel_sources
    }

    topic = _build_topic(channel)
    suggested = await llm.suggest_sources(topic, count=LLM_SUGGEST_COUNT) or []
    logger.info(
        "scout: channel %d, LLM suggested %d accounts", channel.id, len(suggested)
    )

    seen: set[str] = set()
    candidates: list[tuple[str, str]] = []
    for s in suggested:
        u = (s.get("username") or "").lstrip("@").strip().lower()
        if not u or u in existing or u in seen:
            continue
        if not _USERNAME_RE.fullmatch(u):
            continue
        if u.startswith("vk:"):
            continue
        seen.add(u)
        candidates.append((u, (s.get("reason") or "").strip()))

    if not candidates:
        return []

    eff_min_likes = (
        0 if channel.filter_preset == "unfiltered" else channel.min_likes
    )
    eff_min_retweets = (
        0 if channel.filter_preset == "unfiltered" else channel.min_retweets
    )

    results = await prevalidate_candidates(
        candidates, eff_min_likes, eff_min_retweets, cache
    )
    results = await apply_topic_relevance(llm, topic, results)
    top = results[:TOP_N]
    logger.info(
        "scout: channel %d, %d candidates survived all gates: %s",
        channel.id, len(top),
        [(c.username, f"{c.est_posts_per_week:.1f}/wk") for c in top],
    )
    return top
