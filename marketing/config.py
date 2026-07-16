"""Конфигурация marketing-бота. Читает тот же .env, что и прод.

Дневные капы (LLM, демо, карточки) имеют ЖЁСТКИЕ потолки в коде:
env-переменная может кап только уменьшить, но не поднять выше потолка.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

# Жёсткие потолки, не переопределяются конфигом (правило ТЗ: бюджеты капятся
# в коде). Значения выше дневной нормы из раздела 9 ТЗ с запасом ×2.
HARD_MAX_DAILY_LLM_CALLS = 200
HARD_MAX_DAILY_DEMOS = 10
HARD_MAX_DAILY_CARDS = 20


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def _capped_int(name: str, default: int, hard_max: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(0, min(value, hard_max))


@dataclass
class MarketingConfig:
    bot_token: str = field(default_factory=lambda: _require("MARKETING_BOT_TOKEN"))
    admin_user_id: int = field(default_factory=lambda: int(_require("ADMIN_USER_ID")))

    openrouter_api_key: str = field(default_factory=lambda: _require("OPENROUTER_API_KEY"))
    # Дешёвая модель: квалификация и черновики не требуют Sonnet
    openrouter_model: str = field(
        default_factory=lambda: os.getenv(
            "MARKETING_LLM_MODEL",
            os.getenv("OPENROUTER_MODEL_DEFAULT", "meta-llama/llama-3.3-70b-instruct"),
        )
    )

    twitter_api_key: str = field(default_factory=lambda: _require("TWITTER_API_KEY"))

    db_path: str = field(default_factory=lambda: os.getenv("MARKETING_DB_PATH", "marketing.db"))

    daily_llm_cap: int = field(
        default_factory=lambda: _capped_int("MARKETING_DAILY_LLM_CAP", 100, HARD_MAX_DAILY_LLM_CALLS)
    )
    daily_demo_cap: int = field(
        default_factory=lambda: _capped_int("MARKETING_DAILY_DEMO_CAP", 5, HARD_MAX_DAILY_DEMOS)
    )
    daily_cards_cap: int = field(
        default_factory=lambda: _capped_int("MARKETING_DAILY_CARDS_CAP", 10, HARD_MAX_DAILY_CARDS)
    )

    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))

    @property
    def database_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.db_path}"
