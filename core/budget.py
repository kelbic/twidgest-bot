"""Дневной LLM-бюджет на канал. In-memory, ключ — (channel_id, дата UTC).

Сброс — сменой даты; рестарт процесса тоже обнуляет (приемлемо: рестарты
делает только админ). Семантика — ДЕГРАДАЦИЯ, не тишина: при исчерпании
вызывающий код ужесточает отбор (только топ-виральность, без ранкера),
канал продолжает публиковать.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from tiers import DAILY_EVAL_BUDGET

_spent: dict[tuple[int, str], int] = defaultdict(int)


def _key(channel_id: int) -> tuple[int, str]:
    return (channel_id, f"{datetime.utcnow():%Y-%m-%d}")


def spend(channel_id: int, n: int = 1) -> bool:
    """Списывает n оценок. False — бюджет исчерпан, списания нет."""
    k = _key(channel_id)
    if _spent[k] + n > DAILY_EVAL_BUDGET:
        return False
    _spent[k] += n
    # GC прошлых дат, чтобы словарь не рос вечно
    today = k[1]
    for old in [kk for kk in _spent if kk[1] != today]:
        del _spent[old]
    return True


def remaining(channel_id: int) -> int:
    return max(0, DAILY_EVAL_BUDGET - _spent[_key(channel_id)])


def exhausted(channel_id: int) -> bool:
    return remaining(channel_id) <= 0
