"""Кумулятивные счётчики себестоимости (с момента старта процесса).

Цель: понять реальную цену канала в сутки перед фиксацией лимитов тарифа.
Кумулятивные итоги печатаются в конце каждого цикла collector/viral_picker;
дневная стоимость = разница строк cost-totals за 24 часа:
  tw_api_calls  -> запросы twitterapi.io (умножить на цену запроса тарифа)
  tw_cache_hits -> сэкономленные запросы (для оценки эффективности кэша)
  llm_calls     -> вызовы OpenRouter
  llm_chars_*   -> символы in/out (токены ~ chars/2.5 для RU, /4 для EN)
Кумулятив, а не пер-цикл: джобы могут перекрываться, сбросы дали бы кашу.
"""
from __future__ import annotations

from collections import defaultdict

_totals: dict[str, int] = defaultdict(int)


def inc(key: str, n: int = 1) -> None:
    _totals[key] += n


def snapshot() -> dict[str, int]:
    """Копия счётчиков для персистентных снапшотов (/costs)."""
    return dict(_totals)


def totals_line() -> str:
    if not _totals:
        return "no-data"
    return " ".join(f"{k}={v}" for k, v in sorted(_totals.items()))
