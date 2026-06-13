"""Default engagement thresholds per niche.

Based on observation: mega-accounts (@elonmusk, news networks) get 1000s of likes,
beauty/hobby niches often have 5-50 likes. Fixed min_likes=200 discards hobby content.
"""
from __future__ import annotations

# (min_likes, min_retweets) per niche code.
#
# ПРИНЦИП (июнь 2026, по живым данным): дефолт должен ПРОПУСКАТЬ поток, а не
# душить его — качество добирает ранкер, а планку вверх пользователь двигает
# сам через /setthreshold, если канал слишком шумный. Прежние значения
# (tech_ai 300/30, sports 500/50) были подтверждённо завышены: рабочий AI-канал
# живёт на 5/2 и постит ежечасно. Поэтому почти всё унифицировано в 10/2.
# Единственное исключение — sports: аккаунты реально крупные, 10 лайков там
# это бот-уровень, держим умеренно выше.
_SOFT = (10, 2)
NICHE_ENGAGEMENT_DEFAULTS: dict[str, tuple[int, int]] = {
    "tech_ai": _SOFT,
    "crypto": _SOFT,
    "startups": _SOFT,
    "science": _SOFT,
    "sports": (50, 5),
    "business": _SOFT,
    "ideas": _SOFT,
    "design": _SOFT,
    "entertainment": _SOFT,
    "gaming": _SOFT,
    "longevity": _SOFT,
    "general": _SOFT,
}


def get_engagement_defaults(niche: str) -> tuple[int, int]:
    """Returns (min_likes, min_retweets) for the given niche code."""
    return NICHE_ENGAGEMENT_DEFAULTS.get(niche, NICHE_ENGAGEMENT_DEFAULTS["general"])
