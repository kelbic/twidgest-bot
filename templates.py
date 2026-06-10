"""Готовые темплейты каналов — 5 штук, прошедших аудит источников.

Каждый источник проверен скаут-превалидацией (tools/audit_templates.py):
живость, доля текста, частота, тематичность, пороги шаблона.
Аудит от 2026-06-10; прогонять повторно раз в месяц — источники протухают.
Каталог сознательно мал: основной путь создания канала — «текст как тема»
(AI-подбор с превалидацией), шаблоны — для тех, у кого темы нет.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ChannelTemplate:
    id: str
    name: str  # отображаемое имя
    emoji: str
    description: str  # короткое описание для UI
    niche: str  # категория ниши для подбора промпта
    default_sources: list[str] = field(default_factory=list)
    suggested_min_likes: int = 200
    suggested_min_retweets: int = 20
    digest_interval_hours: int = 12


# --------------------------------------------------------------------------- #
# Каталог темплейтов (аудит 2026-06-10: все источники живы и по теме)
# --------------------------------------------------------------------------- #
TEMPLATES: dict[str, ChannelTemplate] = {
    "ai-news": ChannelTemplate(
        id="ai-news",
        name="AI & Tech News",
        emoji="🤖",
        description="Свежие новости о ИИ, LLM, OpenAI, Anthropic, Google DeepMind",
        niche="tech_ai",
        default_sources=[
            "OpenAI", "AnthropicAI", "GoogleDeepMind", "sama", "ylecun",
            "karpathy", "AndrewYNg", "demishassabis", "DrJimFan",
        ],
        suggested_min_likes=500,
        suggested_min_retweets=50,
    ),
    "space": ChannelTemplate(
        id="space",
        name="Space & Astronomy",
        emoji="🚀",
        description="Космические исследования, SpaceX, NASA, астрономия",
        niche="science",
        default_sources=[
            "NASA", "SpaceX", "Space_Station",
            "NASAJPL", "NASAHubble", "NASAWebb", "blueorigin",
        ],
        suggested_min_likes=500,
        suggested_min_retweets=50,
    ),
    "gaming": ChannelTemplate(
        id="gaming",
        name="Gaming News",
        emoji="🎮",
        description="Игровая индустрия, релизы, киберспорт",
        niche="gaming",
        default_sources=[
            "IGN", "GameSpot", "Kotaku", "PlayStation", "Xbox",
            "Nintendo", "Steam",
        ],
        suggested_min_likes=300,
        suggested_min_retweets=30,
    ),
    "movies": ChannelTemplate(
        id="movies",
        name="Movies & TV",
        emoji="🎬",
        description="Кино, сериалы, трейлеры, обзоры",
        niche="entertainment",
        default_sources=[
            "Variety", "THR", "DEADLINE", "RottenTomatoes",
            "IMDb", "A24",
        ],
        suggested_min_likes=500,
        suggested_min_retweets=50,
    ),
    "f1": ChannelTemplate(
        id="f1",
        name="Formula 1",
        emoji="🏎",
        description="F1 гонки, команды, технические новости",
        niche="sports",
        default_sources=[
            "F1", "ScuderiaFerrari", "MercedesAMGF1", "redbullracing",
            "McLarenF1", "AstonMartinF1",
        ],
        suggested_min_likes=300,
        suggested_min_retweets=30,
    ),
}


def get_template(template_id: str) -> ChannelTemplate | None:
    return TEMPLATES.get(template_id)


def list_templates() -> list[ChannelTemplate]:
    return list(TEMPLATES.values())
