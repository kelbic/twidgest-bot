"""Описание тарифов. Единый источник правды для всех проверок лимитов."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Tier(str, Enum):
    FREE = "free"
    STARTER = "starter"
    PRO = "pro"
    AGENCY = "agency"


@dataclass(frozen=True)
class TierLimits:
    """Лимиты и параметры одного тарифа."""

    name: str
    price_stars: int                # 0 для free
    max_sources: int                # сколько X-аккаунтов можно мониторить
    max_targets: int                # сколько каналов/чатов
    max_posts_per_day: int          # квота постов в сутки на пользователя
    digest_min_interval_hours: int  # минимальный интервал между дайджестами
    can_use_digest_mode: bool       # включён ли digest-режим
    can_use_custom_prompt: bool     # можно ли задать свой промпт
    use_pro_llm: bool               # GPT/Claude вместо Llama


TIERS: dict[Tier, TierLimits] = {
    Tier.FREE: TierLimits(
        name="Free",
        price_stars=0,
        max_sources=3,
        max_targets=1,
        max_posts_per_day=10,  # 4 digest + до 6 single
        digest_min_interval_hours=6,
        can_use_digest_mode=True,  # разрешим digest на Free
        can_use_custom_prompt=False,
        use_pro_llm=False,
    ),
    Tier.STARTER: TierLimits(
        name="Starter",
        price_stars=99,
        max_sources=10,
        max_targets=2,
        max_posts_per_day=20,
        digest_min_interval_hours=6,
        can_use_digest_mode=True,
        can_use_custom_prompt=False,
        use_pro_llm=False,
    ),
    Tier.PRO: TierLimits(
        name="Pro",
        price_stars=299,
        max_sources=30,
        max_targets=5,
        max_posts_per_day=200,
        digest_min_interval_hours=1,
        can_use_digest_mode=True,
        can_use_custom_prompt=True,
        use_pro_llm=True,
    ),
    Tier.AGENCY: TierLimits(
        name="Agency",
        price_stars=999,
        max_sources=100,
        max_targets=20,
        max_posts_per_day=2000,
        digest_min_interval_hours=1,
        can_use_digest_mode=True,
        can_use_custom_prompt=True,
        use_pro_llm=True,
    ),
}


def get_limits(tier: Tier | str) -> TierLimits:
    if isinstance(tier, str):
        tier = Tier(tier)
    return TIERS[tier]
