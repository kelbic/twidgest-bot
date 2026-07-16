"""Квалификация лидов: эвристики (без LLM) + один батч-вызов LLM.

Модель покупателя: покупает только ВЫГОРЕВШИЙ админ — канал ещё жив
(не бросил, значит болит), но кадэнс валится (усилие проигрывает).
Признак — не разброс интервалов, а ТРЕНД: decay = recent_ppw/older_ppw < 0.6.

Высокий gap_cv сам по себе — не боль: событийные ниши (F1-канал взрывается
в уик-энд гонки) дают высокий cv при здоровом паттерне. Поэтому бонус за
«постит человек» начисляется только при падающем decay.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime

from marketing.enrich import CadenceMetrics
from marketing.llm import MarketingLLM

logger = logging.getLogger(__name__)

# Скор ниже — лид автоматически dead
MIN_QUALIFY_SCORE = 40
# База, поверх которой суммируются вклады эвристик
BASE_SCORE = 30

ALIVE_MAX_DAYS = 7      # последний пост не старше — канал «жив»
DEAD_MIN_DAYS = 14      # старше — «мёртв», админ уже бросил
BURNOUT_DECAY = 0.6     # decay ниже — кадэнс валится, выгорание
STABLE_DECAY_LO = 0.8   # 0.8–1.2 — стабильный ручной (сорт (а)/(б))
STABLE_DECAY_HI = 1.2

TARGET_SUBS_LO = 200
TARGET_SUBS_HI = 5000
HUGE_SUBS = 20000

HUMAN_GAP_CV = 1.2

# Сильные для Twidgest ниши (Twitter-покрытие 90%+), ru+en маркеры
STRONG_NICHE_MARKERS = (
    "ai", "ии", "нейросет", "llm", "machine learning", "мл",
    "crypto", "крипт", "defi", "блокчейн", "bitcoin", "битко",
    "спорт", "sport", "f1", "формул", "футбол", "football", "хокке", "ufc",
    "наук", "science", "космос", "space", "физик", "биолог",
    "longevity", "долголети", "биохак",
    "гейминг", "gaming", "игр", "киберспорт", "esports",
    "стартап", "startup", "венчур", "vc", "tech", "технолог",
)

# Узкие русские региональные ниши — Twitter их не покрывает
REGIONAL_NICHE_MARKERS = (
    "регион", "област", "край", "края", "крае", "райо", "город", "губерн",
    "мэри", "подслушано", "афиша", "недвижимост", "жкх", "барахолк", "объявлен",
)


@dataclass
class HeuristicVerdict:
    score: int
    reasons: list[str]


def _match(niche: str, markers: tuple[str, ...]) -> bool:
    lower = niche.lower()
    return any(m in lower for m in markers)


def score_lead_heuristics(
    metrics: CadenceMetrics,
    subscribers: int | None,
    niche: str | None,
    contact: str | None,
    now: datetime | None = None,
) -> HeuristicVerdict:
    """Эвристический скор 0–100 + человекочитаемые причины для карточки."""
    now = now or datetime.utcnow()
    score = BASE_SCORE
    reasons: list[str] = []

    days_silent: float | None = None
    if metrics.last_post_at is not None:
        days_silent = (now - metrics.last_post_at).total_seconds() / 86400.0

    alive = days_silent is not None and days_silent <= ALIVE_MAX_DAYS
    dead = days_silent is None or days_silent > DEAD_MIN_DAYS

    decay = metrics.decay

    # --- траектория: ГЛАВНЫЙ сигнал ---
    if dead:
        score -= 40
        if days_silent is not None:
            reasons.append(f"мёртв (пост {days_silent:.0f} дн назад) — уже бросил")
        else:
            reasons.append("постов не видно — мёртв или без превью")
    elif alive and decay is not None and decay < BURNOUT_DECAY:
        score += 35
        reasons.append(
            f"жив, кадэнс валится ({metrics.older_ppw:.1f}→{metrics.recent_ppw:.1f} "
            f"пост/нед, decay {decay:.2f}) — выгорающий админ"
        )
        # «Постит человек» — вторичный подтверждающий сигнал,
        # учитывается ТОЛЬКО при падающем decay (см. докстринг модуля)
        if metrics.gap_cv is not None and metrics.gap_cv >= HUMAN_GAP_CV and metrics.varied_hours:
            score += 10
            reasons.append(f"постит человек (cv {metrics.gap_cv:.1f}, разное время)")
    elif alive and decay is not None and STABLE_DECAY_LO <= decay <= STABLE_DECAY_HI:
        score += 10
        reasons.append(f"жив, стабильный ручной (decay {decay:.2f}) — вероятен вежливый отказ")
    elif alive:
        reasons.append(
            f"жив (пост {days_silent:.0f} дн назад)"
            + (f", decay {decay:.2f}" if decay is not None else ", тренд не оценён")
        )
    else:
        reasons.append(f"последний пост {days_silent:.0f} дн назад")

    # --- размер ---
    if subscribers is not None:
        if subscribers < TARGET_SUBS_LO:
            score -= 20
            reasons.append(f"крошечный ({subscribers} подп.)")
        elif subscribers > HUGE_SUBS:
            score -= 30
            reasons.append(f"слишком крупный ({subscribers} подп.) — не наша лига в MVP")
        elif subscribers <= TARGET_SUBS_HI:
            reasons.append(f"размер в цели ({subscribers} подп.)")

    # --- контакт ---
    if contact:
        score += 10
        reasons.append(f"контакт найден ({contact})")

    # --- ниша ---
    if niche:
        if _match(niche, REGIONAL_NICHE_MARKERS):
            score -= 25
            reasons.append("узкая региональная ниша — Twitter не покрывает")
        elif _match(niche, STRONG_NICHE_MARKERS):
            score += 15
            reasons.append("сильная ниша для Twidgest")

    return HeuristicVerdict(score=max(0, min(100, score)), reasons=reasons)


# --------------------------------------------------------------------------- #
# LLM-часть: один батч-вызов до 5 лидов
# --------------------------------------------------------------------------- #

_QUALIFY_SYSTEM = """Ты помогаешь квалифицировать Telegram-каналы как лиды для продукта
Twidgest — бота, который сам ведёт тематический канал (находит виральные посты
в X по нише, фильтрует и переписывает на русский).

Для КАЖДОГО канала по описанию, заголовкам постов и метрикам кадэнса оцени:
- niche_guess: ниша канала, 1-3 слова по-русски;
- fit_0_100: насколько Twidgest покроет эту нишу контентом из X (англоязычный
  Twitter). AI/крипта/спорт/наука/гейминг/стартапы — высоко; узкие русские
  региональные темы — низко;
- hook: конкретная зацепка для персонального первого сообщения админу,
  1 предложение по-русски, по реальным фактам из входа.

ВАЖНО про hook: если кадэнс канала падает (decay < 0.6 — было N постов/нед,
стало меньше), hook должен целиться в БОЛЬ, а не в наблюдение — не «у вас
рваный постинг», а «вижу, весной было по 5 постов в неделю, сейчас по одному —
тяжело тянуть?». Никаких упрёков и тона аудита.

Верни СТРОГО JSON-объект без markdown:
{"username1": {"niche_guess": "...", "fit_0_100": 85, "hook": "..."}, ...}
Ровно по одному ключу на каждый канал из входа."""


@dataclass
class LlmVerdict:
    niche_guess: str | None
    fit: int | None
    hook: str | None


def _strip_fences(raw: str) -> str:
    clean = raw.strip()
    if clean.startswith("```"):
        parts = clean.split("```")
        if len(parts) >= 2:
            clean = parts[1]
        clean = re.sub(r"^json", "", clean).strip()
    return clean


def build_qualify_user_prompt(leads_data: list[dict]) -> str:
    """leads_data: [{username, description, samples, metrics_line}, ...]"""
    blocks = []
    for item in leads_data:
        samples = "\n".join(f"- {s[:200]}" for s in item.get("samples") or []) or "- (текстов нет)"
        blocks.append(
            f"@{item['username']}\n"
            f"Описание: {item.get('description') or '(нет)'}\n"
            f"Метрики: {item.get('metrics_line') or '(нет)'}\n"
            f"Последние посты:\n{samples}"
        )
    return "Каналы:\n\n" + "\n\n".join(blocks)


async def llm_qualify_batch(
    llm: MarketingLLM, leads_data: list[dict]
) -> dict[str, LlmVerdict]:
    """Батч-оценка до 5 лидов одним вызовом. Fail-open: сбой → пустой dict."""
    if not leads_data:
        return {}

    raw = await llm.call(
        _QUALIFY_SYSTEM,
        build_qualify_user_prompt(leads_data),
        max_tokens=1200,
        temperature=0.2,
    )
    if not raw:
        logger.warning("qualify: LLM failed/capped, fail-open (heuristics only)")
        return {}

    try:
        data = json.loads(_strip_fences(raw))
        assert isinstance(data, dict)
    except Exception:
        logger.warning("qualify: LLM returned non-JSON: %s", raw[:200])
        return {}

    verdicts: dict[str, LlmVerdict] = {}
    for key, value in data.items():
        if not isinstance(value, dict):
            continue
        username = str(key).lstrip("@").lower()
        try:
            fit = max(0, min(100, int(value.get("fit_0_100"))))
        except (TypeError, ValueError):
            fit = None
        niche_guess = (str(value.get("niche_guess") or "").strip() or None)
        hook = (str(value.get("hook") or "").strip() or None)
        verdicts[username] = LlmVerdict(niche_guess=niche_guess, fit=fit, hook=hook)
    return verdicts


def combine_score(heuristic_score: int, fit: int | None) -> int:
    """Итоговый скор: эвристики — основа, LLM-fit — поправка на покрытие ниши."""
    if fit is None:
        return heuristic_score
    return max(0, min(100, round(0.75 * heuristic_score + 0.25 * fit)))
