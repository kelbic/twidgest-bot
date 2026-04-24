"""15 готовых темплейтов для популярных ниш канала.

Каждый темплейт = (id, name, description, niche, default_sources, niche_prompt_overlay).
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
# Каталог темплейтов
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
            "karpathy", "AndrewYNg", "elonmusk", "demishassabis", "DrJimFan",
        ],
        suggested_min_likes=500,
        suggested_min_retweets=50,
    ),
    "longevity": ChannelTemplate(
        id="longevity",
        name="Longevity & Biohacking",
        emoji="🧬",
        description="Наука о долголетии, биохакинг, исследования здоровья",
        niche="longevity",
        default_sources=[
            "PeterAttiaMD", "hubermanlab", "DavidSinclair", "foundmyfitness",
            "MattKaeberlein", "bryan_johnson", "SatchinPanda", "lifespan_io",
            "BuckInstitute",
        ],
        suggested_min_likes=200,
        suggested_min_retweets=20,
    ),
    "crypto": ChannelTemplate(
        id="crypto",
        name="Crypto & Web3",
        emoji="💰",
        description="Криптовалюты, DeFi, NFT, блокчейн-новости",
        niche="crypto",
        default_sources=[
            "VitalikButerin", "cz_binance", "saylor", "aantonop",
            "ethereumJoseph", "balajis", "naval", "elonmusk",
            "CoinDesk", "DecryptMedia",
        ],
        suggested_min_likes=500,
        suggested_min_retweets=50,
    ),
    "startups": ChannelTemplate(
        id="startups",
        name="Startups & VC",
        emoji="🚀",
        description="Стартапы, венчурные инвестиции, продуктовые инсайты",
        niche="startups",
        default_sources=[
            "paulg", "naval", "garrytan", "patrickc", "levie",
            "jasonlk", "harryhurst", "lennysan", "shaanvp", "elonmusk",
        ],
        suggested_min_likes=300,
        suggested_min_retweets=30,
    ),
    "science": ChannelTemplate(
        id="science",
        name="Science & Research",
        emoji="🔬",
        description="Научные открытия, исследования, popular science",
        niche="science",
        default_sources=[
            "NatureNews", "ScienceMagazine", "newscientist", "smithsonianmag",
            "QuantaMagazine", "NASA", "CERN", "physorg_com",
        ],
        suggested_min_likes=500,
        suggested_min_retweets=100,
    ),
    "space": ChannelTemplate(
        id="space",
        name="Space & Astronomy",
        emoji="🚀",
        description="Космические исследования, SpaceX, NASA, астрономия",
        niche="science",
        default_sources=[
            "NASA", "SpaceX", "elonmusk", "Astro_Behnken", "Space_Station",
            "NASAJPL", "NASAHubble", "NASAWebb", "blueorigin",
        ],
        suggested_min_likes=500,
        suggested_min_retweets=50,
    ),
    "nba": ChannelTemplate(
        id="nba",
        name="NBA Basketball",
        emoji="🏀",
        description="NBA новости, переходы, статистика, игры",
        niche="sports",
        default_sources=[
            "NBA", "wojespn", "ShamsCharania", "ESPNNBA", "NBAonTNT",
            "BleacherReport", "TheAthletic",
        ],
        suggested_min_likes=500,
        suggested_min_retweets=100,
    ),
    "soccer": ChannelTemplate(
        id="soccer",
        name="World Soccer",
        emoji="⚽",
        description="Мировой футбол, переходы, лиги, Champions League",
        niche="sports",
        default_sources=[
            "FabrizioRomano", "DiMarzio", "ESPNFC", "BBCSport", "OptaJoe",
            "ChampionsLeague", "premierleague", "LaLigaEN",
        ],
        suggested_min_likes=1000,
        suggested_min_retweets=100,
    ),
    "f1": ChannelTemplate(
        id="f1",
        name="Formula 1",
        emoji="🏎",
        description="F1 гонки, команды, технические новости",
        niche="sports",
        default_sources=[
            "F1", "ScuderiaFerrari", "MercedesAMGF1", "redbullracing",
            "McLarenF1", "AstonMartinF1", "WilliamsRacing",
        ],
        suggested_min_likes=300,
        suggested_min_retweets=30,
    ),
    "gaming": ChannelTemplate(
        id="gaming",
        name="Gaming News",
        emoji="🎮",
        description="Игровая индустрия, релизы, киберспорт",
        niche="gaming",
        default_sources=[
            "IGN", "GameSpot", "Polygon", "Kotaku", "PlayStation", "Xbox",
            "Nintendo", "Steam", "Geoff_Keighley",
        ],
        suggested_min_likes=300,
        suggested_min_retweets=30,
    ),
    "design": ChannelTemplate(
        id="design",
        name="Design & UX",
        emoji="🎨",
        description="Продуктовый дизайн, UX, типографика, brand",
        niche="design",
        default_sources=[
            "figma", "behance", "dribbble", "nngroup", "uxdesignmastery",
            "DesignerNews", "smashingmag",
        ],
        suggested_min_likes=200,
        suggested_min_retweets=20,
    ),
    "movies": ChannelTemplate(
        id="movies",
        name="Movies & TV",
        emoji="🎬",
        description="Кино, сериалы, трейлеры, обзоры",
        niche="entertainment",
        default_sources=[
            "Variety", "THR", "DEADLINE", "Collider", "RottenTomatoes",
            "IMDb", "A24",
        ],
        suggested_min_likes=500,
        suggested_min_retweets=50,
    ),
    "marketing": ChannelTemplate(
        id="marketing",
        name="Marketing & Growth",
        emoji="📈",
        description="Маркетинг, growth-хаки, продуктовый менеджмент",
        niche="business",
        default_sources=[
            "GaryVee", "Julian", "AlexHormozi", "shaanvp", "lennysan",
            "rrhoover", "noahkagan",
        ],
        suggested_min_likes=300,
        suggested_min_retweets=30,
    ),
    "philosophy": ChannelTemplate(
        id="philosophy",
        name="Philosophy & Ideas",
        emoji="💭",
        description="Философия, культура мышления, эссе",
        niche="ideas",
        default_sources=[
            "naval", "DavidDeutschOxf", "robinhanson", "TylerCowen",
            "morganhousel", "paulg",
        ],
        suggested_min_likes=300,
        suggested_min_retweets=50,
    ),
    "tesla-spacex": ChannelTemplate(
        id="tesla-spacex",
        name="Tesla & SpaceX",
        emoji="⚡",
        description="Tesla, SpaceX, Neuralink, X — экосистема Маска",
        niche="tech_ai",
        default_sources=[
            "elonmusk", "Tesla", "SpaceX", "neuralink", "boringcompany",
            "WholeMarsBlog", "Teslaconomics",
        ],
        suggested_min_likes=500,
        suggested_min_retweets=50,
    ),
}


def get_template(template_id: str) -> ChannelTemplate | None:
    return TEMPLATES.get(template_id)


def list_templates() -> list[ChannelTemplate]:
    return list(TEMPLATES.values())
