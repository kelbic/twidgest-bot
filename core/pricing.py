"""Живые цены LLM с OpenRouter + env-переопределения (для /costs).

Зачем: хардкод цен протухает при смене модели или её прайса. OpenRouter
отдаёт прайс каждой модели в /api/v1/models (pricing.prompt/completion,
USD за ТОКЕН) — берём цену именно сконфигурированной модели, кэшируем
на сутки. Любую цену можно переопределить через env:
  LLM_IN_PER_MTOK / LLM_OUT_PER_MTOK  — USD за миллион токенов
  TW_USD_PER_TWEET                    — тариф twitterapi.io за твит
Fallback-константы — последний рубеж, если и API недоступен, и env пуст.
"""
from __future__ import annotations

import logging
import os
import time

import aiohttp

logger = logging.getLogger(__name__)

_FALLBACK_IN_PER_MTOK = 1.0    # Haiku 4.5 на момент написания
_FALLBACK_OUT_PER_MTOK = 5.0
_FALLBACK_TW_PER_TWEET = 0.00015  # twitterapi.io: 15 кредитов/твит

_cache: dict[str, tuple[float, float, float]] = {}  # model_id -> (in, out, ts)
_TTL = 24 * 3600


def tw_usd_per_tweet() -> float:
    try:
        return float(os.getenv("TW_USD_PER_TWEET", "") or _FALLBACK_TW_PER_TWEET)
    except ValueError:
        return _FALLBACK_TW_PER_TWEET


async def llm_usd_per_mtok(model_id: str) -> tuple[float, float, str]:
    """(in_per_mtok, out_per_mtok, source). source: 'env'|'openrouter'|'fallback'."""
    env_in, env_out = os.getenv("LLM_IN_PER_MTOK"), os.getenv("LLM_OUT_PER_MTOK")
    if env_in and env_out:
        try:
            return float(env_in), float(env_out), "env"
        except ValueError:
            pass

    hit = _cache.get(model_id)
    if hit and time.monotonic() - hit[2] < _TTL:
        return hit[0], hit[1], "openrouter"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://openrouter.ai/api/v1/models", timeout=15
            ) as resp:
                data = await resp.json()
        for m in data.get("data") or []:
            if m.get("id") == model_id:
                pr = m.get("pricing") or {}
                # OpenRouter отдаёт USD за ОДИН токен строкой
                pin = float(pr.get("prompt") or 0) * 1e6
                pout = float(pr.get("completion") or 0) * 1e6
                if pin > 0 or pout > 0:
                    _cache[model_id] = (pin, pout, time.monotonic())
                    return pin, pout, "openrouter"
        logger.warning("pricing: model %s not found in OpenRouter list", model_id)
    except Exception as exc:
        logger.warning("pricing: OpenRouter fetch failed: %s", exc)

    return _FALLBACK_IN_PER_MTOK, _FALLBACK_OUT_PER_MTOK, "fallback"
