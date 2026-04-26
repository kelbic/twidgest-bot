"""Подбор картинки для поста через Unsplash API.

Требует UNSPLASH_ACCESS_KEY в env. Если ключа нет — функция возвращает None
(graceful degradation — посты идут без картинок).

Free tier: 50 requests/hour. Кэш keyword→URL даёт ~2-3x экономии запросов.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time

import aiohttp

logger = logging.getLogger(__name__)

UNSPLASH_API = "https://api.unsplash.com/search/photos"

# In-memory cache: keyword (normalized) → (url, fetched_at)
_CACHE: dict[str, tuple[str, float]] = {}
_CACHE_TTL_SECONDS = 6 * 3600  # 6 часов: те же keywords дают тот же URL
_CACHE_LOCK = asyncio.Lock()


async def _get_cached(key: str) -> str | None:
    async with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if entry is None:
            return None
        url, ts = entry
        if (time.time() - ts) > _CACHE_TTL_SECONDS:
            del _CACHE[key]
            return None
        return url


async def _set_cached(key: str, url: str) -> None:
    async with _CACHE_LOCK:
        _CACHE[key] = (url, time.time())
        # Защита от роста: храним максимум 500 ключей
        if len(_CACHE) > 500:
            # Удаляем самый старый
            oldest_key = min(_CACHE, key=lambda k: _CACHE[k][1])
            del _CACHE[oldest_key]


async def fetch_image_url_for_keywords(
    keywords: str,
    access_key: str,
    timeout: int = 10,
) -> str | None:
    """Получает URL фото из Unsplash API по keywords.

    Args:
        keywords: 1-3 английских слова, разделённых пробелом
        access_key: Unsplash Access Key

    Returns:
        Прямой URL изображения (regular size) или None
    """
    if not access_key:
        return None
    if not keywords:
        return None

    cleaned = re.sub(r"[^A-Za-z0-9 ]+", " ", keywords).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        return None

    # Проверяем кэш — экономим Unsplash quota
    cache_key = cleaned.lower()
    cached_url = await _get_cached(cache_key)
    if cached_url:
        logger.info("Unsplash cache hit: %r", cleaned)
        return cached_url

    headers = {
        "Authorization": f"Client-ID {access_key}",
        "Accept-Version": "v1",
    }
    params = {
        "query": cleaned,
        "per_page": 5,
        "orientation": "landscape",
        "content_filter": "high",
    }

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(
                UNSPLASH_API, params=params, timeout=timeout
            ) as resp:
                if resp.status == 403:
                    logger.warning(
                        "Unsplash rate limit (403), keywords=%r", cleaned
                    )
                    return None
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning(
                        "Unsplash HTTP %d for keywords=%r: %s",
                        resp.status, cleaned, body[:200],
                    )
                    return None
                data = await resp.json()
    except Exception:
        logger.exception("Unsplash request failed for keywords=%r", cleaned)
        return None

    results = data.get("results") or []
    if not results:
        logger.info("Unsplash: no results for keywords=%r", cleaned)
        return None

    # Берём первый результат, размер "regular" (1080px wide)
    photo = results[0]
    urls = photo.get("urls") or {}
    image_url = urls.get("regular") or urls.get("small") or urls.get("full")

    if image_url:
        logger.info(
            "Unsplash: keywords=%r → photo by %s",
            cleaned,
            (photo.get("user") or {}).get("username", "unknown"),
        )
        await _set_cached(cache_key, image_url)

    return image_url
