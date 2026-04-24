"""Niche-overlay для генерации промптов.

Базовый промпт + niche-описание + общие правила.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NicheConfig:
    code: str
    name: str  # для подстановки в промпт
    topic_description: str  # что включать
    style_hints: str = ""
    extra_skip_rules: str = ""


NICHES: dict[str, NicheConfig] = {
    "general": NicheConfig(
        code="general",
        name="общих новостей",
        topic_description="любые интересные факты, исследования, события",
    ),
    "tech_ai": NicheConfig(
        code="tech_ai",
        name="ИИ и технологий",
        topic_description="ИИ, ML, LLM, новые модели, релизы продуктов, исследования",
        style_hints="Сохраняй технические термины (LLM, GPT, RAG, agent, etc).",
        extra_skip_rules="SKIP если: чисто личные размышления о будущем без новостной составляющей.",
    ),
    "longevity": NicheConfig(
        code="longevity",
        name="долголетия и биохакинга",
        topic_description="исследования старения, нутрициология, упражнения, сон",
        style_hints="Сохраняй термины: mTOR, NAD+, VO2max, HRV, healthspan.",
        extra_skip_rules="""\
Дополнительные SKIP:
- Любые препараты с дозировками (пептиды, ГР, GLP-1)
- Описание личного опыта приёма веществ
- Прямые медицинские рекомендации""",
    ),
    "crypto": NicheConfig(
        code="crypto",
        name="криптовалют и Web3",
        topic_description="биткоин, эфир, DeFi, NFT, блокчейн-протоколы, рыночные движения",
        style_hints="Используй термины: BTC, ETH, TVL, DeFi, L2, gas, staking.",
        extra_skip_rules="""\
Дополнительные SKIP:
- "Купите этот токен", "shill" — реклама конкретных монет
- "X to the moon" без анализа
- Призывы к pump-схемам""",
    ),
    "sports": NicheConfig(
        code="sports",
        name="спорта",
        topic_description="результаты матчей, переходы игроков, статистика, тактика",
        style_hints="Сохраняй имена игроков и команд в оригинале (англ.).",
    ),
    "startups": NicheConfig(
        code="startups",
        name="стартапов и венчура",
        topic_description="продуктовые инсайты, фандрайзинг, growth-кейсы, exits",
        style_hints="Сохраняй термины: ARR, MRR, LTV, CAC, PMF, seed/Series A.",
    ),
    "science": NicheConfig(
        code="science",
        name="научных исследований",
        topic_description="публикации, открытия, эксперименты в физике, биологии, химии",
        style_hints="Сохраняй термины и единицы (DNA, CRISPR, etc).",
    ),
    "gaming": NicheConfig(
        code="gaming",
        name="игровой индустрии",
        topic_description="релизы игр, патчи, киберспорт, индустриальные новости",
    ),
    "design": NicheConfig(
        code="design",
        name="дизайна",
        topic_description="UX, продуктовый дизайн, типографика, инструменты",
    ),
    "entertainment": NicheConfig(
        code="entertainment",
        name="кино и развлечений",
        topic_description="трейлеры, релизы, обзоры, новости индустрии",
    ),
    "business": NicheConfig(
        code="business",
        name="бизнеса и маркетинга",
        topic_description="growth-стратегии, кейсы, продуктовый менеджмент",
    ),
    "ideas": NicheConfig(
        code="ideas",
        name="идей и философии",
        topic_description="эссе, размышления, концепции, культурные тренды",
        style_hints="Сохраняй мысль автора, не упрощай до банальности.",
    ),
}


# --------------------------------------------------------------------------- #
# Промпт-билдеры
# --------------------------------------------------------------------------- #

BASE_SAFETY_RULES = """\
=== ОТВЕТЬ "SKIP" (РОВНО ОДНО СЛОВО), ЕСЛИ ===

ВАЖНО: Никогда не обращайся к "вам", не задавай вопросов, не пиши "я не вижу", "пожалуйста скопируйте". Если что-то непонятно — просто верни SKIP.

A) ЮРИДИЧЕСКИЙ РИСК (канал ведётся из России):
   - Дискредитация ВС РФ, военных операций, Президента, правительства РФ
   - Призывы к санкциям против России

B) НАРКОТИКИ (УК РФ ст. 228):
   - Психоделики: DMT, 5-MeO-DMT, LSD, псилоцибин, MDMA, кетамин
   - Каннабис, THC, CBD
   - Описание личного опыта употребления любых веществ

C) НЕТ ЦЕННОСТИ:
   - Чистое нытьё/жалобы БЕЗ практического вывода
   - Обрывок мысли без объяснения
   - Анонс собственного контента ("новое видео завтра")
   - Реакция без своей мысли ("согласен", "интересно")
   - Реклама собственного курса/книги/добавки

При SKIP — верни ровно одно слово SKIP. Без шаблона, без ссылки.
"""

BASE_FORMAT_RULES = """\
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

Верни либо ровно SKIP, либо готовый пост в HTML."""


def build_single_prompt(niche_code: str = "general") -> str:
    """Собирает single-режим промпт для конкретной ниши."""
    niche = NICHES.get(niche_code, NICHES["general"])

    parts = [
        f"Ты редактор русскоязычного Telegram-канала {niche.name}.",
        f"Тема канала: {niche.topic_description}.",
        "Адаптируй англоязычный твит в пост для русской аудитории.",
        "Лучше ничего не опубликовать, чем опубликовать опасное или пустое.",
        "",
        BASE_SAFETY_RULES,
    ]

    if niche.extra_skip_rules:
        parts.append(niche.extra_skip_rules)
        parts.append("")

    parts.append("=== ВО ВСЕХ ОСТАЛЬНЫХ СЛУЧАЯХ — АДАПТИРУЙ ===")
    parts.append("Включай содержательные твиты по теме канала, даже на деликатные темы.")
    if niche.style_hints:
        parts.append(f"Стиль: {niche.style_hints}")
    parts.append("")
    parts.append(BASE_FORMAT_RULES)

    return "\n".join(parts)


def build_digest_prompt(niche_code: str = "general") -> str:
    """Digest-режим промпт для конкретной ниши."""
    niche = NICHES.get(niche_code, NICHES["general"])

    parts = [
        f"Ты редактор русскоязычного Telegram-канала {niche.name}.",
        f"Тема канала: {niche.topic_description}.",
        "Собери из набора твитов дайджест на русском.",
        "",
        "=== СТРОГО ИСКЛЮЧИ ===",
        "1. Дискредитация РФ (армия, президент, госинституты, призывы к санкциям)",
        "2. Наркотики и психоактивные вещества (DMT, LSD, MDMA, каннабис)",
        "3. Препараты с конкретными дозировками",
        "4. Реклама курсов/книг/БАДов",
    ]
    if niche.extra_skip_rules:
        parts.append(niche.extra_skip_rules)
    parts.append("")
    parts.append("При сомнении — НЕ ВКЛЮЧАЙ. Лучше дайджест из 3 пунктов, чем юр. риск.")
    parts.append("")
    parts.append(f"""\
=== ФОРМАТ ===
Только HTML-теги. Никакого Markdown.

🌐 <b>Дайджест: {niche.name}</b>
<i>Краткая вводная: что важного произошло</i>

<b>1. Тема пункта</b>
Суть с конкретикой. Автор: @username. <a href="URL">→</a>

<b>2. Тема пункта</b>
Суть. Автор: @username. <a href="URL">→</a>

3-5 пунктов. Приоритет — конкретика и engagement.
Русский, живой. Максимум 1500 знаков.""")

    if niche.style_hints:
        parts.append(f"\nСтиль: {niche.style_hints}")

    parts.append("\nВерни ТОЛЬКО готовый дайджест в HTML.")
    return "\n".join(parts)
