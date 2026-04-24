"""Default engagement thresholds per niche.

Based on observation: mega-accounts (@elonmusk, news networks) get 1000s of likes,
beauty/hobby niches often have 5-50 likes. Fixed min_likes=200 discards hobby content.
"""
from __future__ import annotations

# (min_likes, min_retweets) per niche code
NICHE_ENGAGEMENT_DEFAULTS: dict[str, tuple[int, int]] = {
    # Mainstream, high-engagement
    "tech_ai": (300, 30),
    "crypto": (300, 30),
    "startups": (200, 20),

    # News / science
    "science": (200, 20),

    # Sports — varies but usually big accounts
    "sports": (500, 50),

    # Business / design / ideas
    "business": (150, 15),
    "ideas": (200, 30),
    "design": (100, 10),

    # Entertainment / gaming
    "entertainment": (200, 20),
    "gaming": (150, 15),

    # Health — smaller science community
    "longevity": (50, 5),

    # Generic fallback for AI-generated / unknown niches
    "general": (30, 3),
}


def get_engagement_defaults(niche: str) -> tuple[int, int]:
    """Returns (min_likes, min_retweets) for the given niche code."""
    return NICHE_ENGAGEMENT_DEFAULTS.get(niche, NICHE_ENGAGEMENT_DEFAULTS["general"])
