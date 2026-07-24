"""Глобальная конфигурация SaaS-бота."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


@dataclass
class Config:
    telegram_bot_token: str = field(default_factory=lambda: _require("TELEGRAM_BOT_TOKEN"))
    admin_user_id: int = field(default_factory=lambda: int(_require("ADMIN_USER_ID")))

    twitter_api_key: str = field(default_factory=lambda: _require("TWITTER_API_KEY"))

    openrouter_api_key: str = field(default_factory=lambda: _require("OPENROUTER_API_KEY"))
    openrouter_model_default: str = field(
        default_factory=lambda: os.getenv(
            "OPENROUTER_MODEL_DEFAULT", "meta-llama/llama-3.3-70b-instruct"
        )
    )
    openrouter_model_pro: str = field(
        default_factory=lambda: os.getenv(
            "OPENROUTER_MODEL_PRO", "anthropic/claude-sonnet-4"
        )
    )

    database_url: str = field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL", "sqlite+aiosqlite:///./twidgest.db"
        )
    )

    collect_interval_minutes: int = field(
        default_factory=lambda: int(os.getenv("COLLECT_INTERVAL_MINUTES", "30"))
    )

    # Ключ только из .env: репо публичный, захардкоженный дефолт = утечка.
    # Пустое значение безопасно — все вызовы проверяют ключ на истинность
    # и просто пропускают подбор картинок.
    unsplash_access_key: str = field(
        default_factory=lambda: os.getenv("UNSPLASH_ACCESS_KEY", "")
    )

    vk_access_token: str = field(
        default_factory=lambda: os.getenv("VK_ACCESS_TOKEN", "")
    )

    # Оплата рублями через Telegram Payments: токен провайдера из BotFather
    # (@BotFather → Bot Settings → Payments → ЮKassa/Robokassa/...).
    # Пусто = рублёвой кнопки нет, оплата только Stars.
    payment_provider_token: str = field(
        default_factory=lambda: os.getenv("PAYMENT_PROVIDER_TOKEN", "")
    )
    # Цена слота (30 дней на канал) в рублях
    price_rub: int = field(default_factory=lambda: int(os.getenv("PRICE_RUB", "1490")))
    # 1 = запрашивать e-mail и передавать провайдеру (нужно для чеков 54-ФЗ,
    # если у провайдера включена фискализация; самозанятым не требуется)
    payment_need_email: bool = field(
        default_factory=lambda: os.getenv("PAYMENT_NEED_EMAIL", "0") == "1"
    )

    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
