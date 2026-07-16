"""Общие фикстуры. Прод-грабли: config.Config() исполняется на импорте в ряде
модулей и требует полный набор env-переменных — задаём безопасные заглушки
ДО любых импортов тестируемого кода."""
from __future__ import annotations

import os

_ENV_DEFAULTS = {
    "TELEGRAM_BOT_TOKEN": "test:token",
    "ADMIN_USER_ID": "1",
    "TWITTER_API_KEY": "test-twitter-key",
    "OPENROUTER_API_KEY": "test-openrouter-key",
    "MARKETING_BOT_TOKEN": "test:marketing-token",
    "MARKETING_DB_PATH": ":memory:",
}

for _name, _value in _ENV_DEFAULTS.items():
    os.environ.setdefault(_name, _value)
