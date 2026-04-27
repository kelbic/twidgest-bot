"""Все промпты и фильтры для LLM-адаптации твитов в посты.

Минималистичная архитектура:
- BASE_SAFETY: фиксированные защитные правила (A: юр.риски, B: наркотики,
  C: мед.дозировки) — не меняются никогда, общие для всех каналов.
- 2 фильтра ценности (D-раздел): strict / loose — выбирается per-channel.
- build_single_prompt(niche_name, niche_topic, filter_mode) — собирает
  итоговый system prompt для rewrite_tweet и viral_picker.
"""
from __future__ import annotations

from dataclasses import dataclass


# === Защитные правила (фиксированы для всех каналов) ===

BASE_SAFETY = """\
=== ОТВЕТЬ "SKIP" (РОВНО ОДНО СЛОВО), ЕСЛИ ===

ВАЖНО: Никогда не обращайся к "вам", не задавай вопросов, не пиши "я не вижу", "пожалуйста скопируйте". Если что-то непонятно — просто верни SKIP.

A) ЮРИДИЧЕСКИЙ РИСК (канал ведётся из России):
   - Дискредитация ВС РФ, военных операций, Президента, правительства РФ
   - Призывы к санкциям против России

B) НАРКОТИКИ И ОПАСНЫЕ ВЕЩЕСТВА (УК РФ ст. 228, 230):
   - Психоделики: DMT, 5-MeO-DMT, LSD, псилоцибин, мескалин, аяуаска, MDMA, кетамин, ибогаин
   - Каннабис, марихуана, THC, CBD — даже в "научном" контексте
   - Опиоиды, кокаин, амфетамины
   - Описание личного опыта употребления любых веществ, изменяющих сознание

C) МЕДИЦИНСКИЕ ДОЗИРОВКИ (закон о рекламе мед.услуг):
   - Конкретный препарат С КОНКРЕТНОЙ ДОЗОЙ (мг, мл, IU): "Tirzepatide 0.5 mg/week", "Rapamycin 6 mg"
   - Пептиды, гормоны роста, стероиды с дозировками: CJC-1295, Ipamorelin, BPC-157, TB-500, тестостерон
   - Off-label применение рецептурных препаратов: "я колю Ozempic для похудения"
   - Самолечение биодобавками с дозами выше суточной нормы

При SKIP — верни ровно одно слово SKIP. Без шаблона, без ссылки.
"""


# === Фильтры ценности (D-раздел, выбирается per-channel) ===

_STRICT_RULES = """\
D) ФИЛЬТР ЦЕННОСТИ:

Пропускай только конкретные факты и события. Подходит для канала который держит высокую планку контента.

ОТКЛОНЯЙ (SKIP) если твит содержит:
- Личное мнение без фактической базы
- Эмоциональные реакции, восклицания
- Анонсы собственного контента ("новое видео", "стрим в 19:00")
- Ретвиты без своего комментария
- Рекламу, промокоды, спонсорство
- Обрывки мыслей без законченной идеи
- Текст из 1-2 слов или только эмодзи

ПРОПУСКАЙ:
- Конкретные новости и события с датами/цифрами
- Технические анонсы (релизы, фичи, обновления)
- Аналитика с конкретными выводами
- Цитаты экспертов с фактами
"""

_LOOSE_RULES = """\
D) ФИЛЬТР ЦЕННОСТИ:

Пропускай почти всё. Это новостной/комьюнити канал.

ОТКЛОНЯЙ (SKIP) только в редких случаях:
- Текст из 1 слова или только эмодзи
- Прямой промокод/ссылка на покупку
- Явные оскорбления конкретных людей (не критика политиков/публичных лиц)

ПУБЛИКУЙ всё остальное. Headline-новости от BBC/Reuters/AP, политические заявления, цитаты, короткие фактологические твиты, ретвиты с информацией — всё это адаптируй и публикуй. По умолчанию — PUBLISH, не SKIP.
"""


# === Правила оформления (фиксированы) ===

BASE_FORMAT = """\
=== АБСОЛЮТНО ЗАПРЕЩЕНО ===
❌ "Автор считает", "Автор твита делится". Излагай мысль АВТОРА от своего имени.
❌ Превышать 400 знаков (включая ссылку). Сокращай.
❌ Markdown. Только HTML: <b>, <i>, <a href="">.

=== ПРАВИЛА ОФОРМЛЕНИЯ ===
1. Русский, живой, без канцелярита.
2. Цифры и факты сохраняй.
3. 200–400 знаков. Сокращай.
4. Без эмодзи, если их не было в оригинале.
5. Убирай хештеги, @mentions.

В конце ОБЯЗАТЕЛЬНО:
<a href="URL_твита">→ Источник</a>

Верни либо ровно SKIP, либо готовый пост в HTML.
"""


# === Niche definitions ===

@dataclass(frozen=True)
class Niche:
    code: str
    name: str
    topic: str
    style_hints: str = ""


# Минимальный набор ниш — задаёт только тематику и стиль,
# никаких safety-правил (они в BASE_SAFETY и filter_mode)
NICHES: dict[str, Niche] = {
    "general": Niche(
        code="general",
        name="общих новостей",
        topic="любые интересные факты, исследования, события",
    ),
    "tech_ai": Niche(
        code="tech_ai",
        name="технологий и AI",
        topic="искусственный интеллект, LLM, стартапы, технологии",
    ),
    "longevity": Niche(
        code="longevity",
        name="долголетия и биохакинга",
        topic="наука долголетия, биохакинг, исследования здоровья",
    ),
    "crypto": Niche(
        code="crypto",
        name="криптовалют",
        topic="криптовалюты, DeFi, NFT, блокчейн",
        style_hints="используй термины ниши: TVL, L2, staking",
    ),
    "sports": Niche(
        code="sports",
        name="спорта",
        topic="спортивные события, переходы, статистика",
    ),
    "ideas": Niche(
        code="ideas",
        name="идей и философии",
        topic="идеи, философия, культура мышления",
    ),
}


# === Filter modes ===

# Переименование в БД: news → strict, community/entertainment → loose.
# 'news' и 'community' оставляем как алиасы для обратной совместимости.
_FILTER_MODE_RULES = {
    "strict": _STRICT_RULES,
    "loose": _LOOSE_RULES,
    # Backwards compat: старые значения мапятся на новые
    "news": _STRICT_RULES,
    "community": _LOOSE_RULES,
    "entertainment": _LOOSE_RULES,
}


def get_filter_rules(filter_mode: str) -> str:
    """Возвращает D-секцию для указанного режима фильтрации."""
    return _FILTER_MODE_RULES.get(filter_mode, _STRICT_RULES)


def get_niche(niche_code: str) -> Niche:
    """Возвращает Niche по коду или general как fallback."""
    return NICHES.get(niche_code, NICHES["general"])


# === Main builders ===

def build_single_prompt(niche_code: str = "general", filter_mode: str = "strict") -> str:
    """Собирает system prompt для rewrite_tweet (single-режим, viral_picker).

    Args:
        niche_code: код ниши (general, tech_ai, longevity, и т.д.)
        filter_mode: 'strict' (только факты) или 'loose' (новости/комьюнити)
    """
    niche = get_niche(niche_code)
    filter_rules = get_filter_rules(filter_mode)

    parts = [
        f"Ты редактор русскоязычного Telegram-канала {niche.name}.",
        f"Тема канала: {niche.topic}.",
        "Адаптируй англоязычный твит в пост для русской аудитории.",
        "Если твит проходит фильтры ниже — публикуй, не SKIP'ай при сомнениях.",
        "",
        BASE_SAFETY,
        "",
        filter_rules,
    ]

    if niche.style_hints:
        parts.append(f"Стиль адаптации: {niche.style_hints}")
        parts.append("")

    parts.append(BASE_FORMAT)
    return "\n".join(parts)


# === Filter modes for UI (used by /filters command) ===

@dataclass(frozen=True)
class FilterMode:
    code: str
    name: str
    emoji: str
    description: str


FILTER_MODES: dict[str, FilterMode] = {
    "strict": FilterMode(
        code="strict",
        name="Строгий",
        emoji="🎯",
        description="Только конкретные факты и события. Жёсткий фильтр (по умолчанию).",
    ),
    "loose": FilterMode(
        code="loose",
        name="Свободный",
        emoji="📡",
        description="Новости, реакции, заявления — пропускает почти всё.",
    ),
}


def list_filter_modes() -> list[FilterMode]:
    return list(FILTER_MODES.values())


def get_filter_mode(code: str) -> FilterMode:
    """Возвращает FilterMode объект для UI. Учитывает legacy названия."""
    # Маппинг legacy → new
    mapping = {"news": "strict", "community": "loose", "entertainment": "loose"}
    canonical = mapping.get(code, code)
    return FILTER_MODES.get(canonical, FILTER_MODES["strict"])


# === Digest prompt (для совместимости — оставляем простую версию) ===

def build_digest_prompt(niche_code: str = "general") -> str:
    """Собирает system prompt для дайджеста."""
    niche = get_niche(niche_code)

    return f"""Ты редактор русскоязычного Telegram-канала {niche.name}.
Тема канала: {niche.topic}.

Тебе дан список твитов за последний период. Отбери 3-7 самых ценных и составь дайджест на русском.

{BASE_SAFETY}

ФОРМАТ ДАЙДЖЕСТА:
🌐 <b>Дайджест: {niche.name}</b>
<i>Подзаголовок одной фразой</i>

1. <b>Заголовок темы 1</b>
Краткий текст темы. Автор: <a href="URL">@username</a>. <a href="URL">→</a>

2. <b>Заголовок темы 2</b>
...

ПРАВИЛА:
- 3-7 пунктов в дайджесте
- Каждый пункт 200-400 знаков
- Каждый со ссылкой на источник
- Тон фактологический, не оценочный
- Без рекламы, без призывов
"""
