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

# In-memory cache: keyword (normalized) → (urls выдачи по порядку, fetched_at).
# Храним ВСЮ выдачу (до 5), а не первый URL: иначе похожие keywords («AI chip»)
# раз за разом отдавали одну и ту же картинку в соседние посты канала.
_CACHE: dict[str, tuple[list[str], float]] = {}
_CACHE_TTL_SECONDS = 6 * 3600  # 6 часов: те же keywords дают тот же URL
_CACHE_LOCK = asyncio.Lock()


def media_fingerprint(url: str | None, kind: str) -> str | None:
    """Стабильный отпечаток медиа для анти-дубля: '<kind>:<host/path>'.

    kind: 'orig' — медиа первоисточника, 'stock' — подобранное (Unsplash).
    Query/fragment отбрасываем: Unsplash и twimg вешают на один и тот же файл
    разные параметры размера/трекинга.
    """
    if not url:
        return None
    from urllib.parse import urlsplit
    parts = urlsplit(url.strip())
    key = f"{parts.netloc.lower()}{parts.path}" if parts.netloc else url.strip()
    return f"{kind}:{key}"[:512]


async def _get_cached(key: str) -> list[str] | None:
    async with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if entry is None:
            return None
        urls, ts = entry
        if (time.time() - ts) > _CACHE_TTL_SECONDS:
            del _CACHE[key]
            return None
        return urls


async def _set_cached(key: str, urls: list[str]) -> None:
    async with _CACHE_LOCK:
        _CACHE[key] = (urls, time.time())
        # Защита от роста: храним максимум 500 ключей
        if len(_CACHE) > 500:
            # Удаляем самый старый
            oldest_key = min(_CACHE, key=lambda k: _CACHE[k][1])
            del _CACHE[oldest_key]


async def fetch_image_url_for_keywords(
    keywords: str,
    access_key: str,
    timeout: int = 10,
    exclude: set[str] | None = None,
) -> str | None:
    """Получает URL фото из Unsplash API по keywords.

    Args:
        keywords: 1-3 английских слова, разделённых пробелом
        access_key: Unsplash Access Key
        exclude: отпечатки (media_fingerprint) уже использованных в канале
            картинок — берём первый результат выдачи, которого там нет.

    Returns:
        Прямой URL изображения (regular size) или None (в т.ч. если ВСЯ
        выдача уже была в канале — лучше текст, чем повтор).
    """
    exclude = exclude or set()

    def _first_unused(urls: list[str]) -> str | None:
        for u in urls:
            if media_fingerprint(u, "stock") not in exclude:
                return u
        return None

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
    cached_urls = await _get_cached(cache_key)
    if cached_urls:
        logger.info("Unsplash cache hit: %r", cleaned)
        return _first_unused(cached_urls)

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

    # Вся выдача, размер "regular" (1080px wide); берём первый неиспользованный
    urls_all: list[str] = []
    for photo in results:
        u = (photo.get("urls") or {})
        image_url = u.get("regular") or u.get("small") or u.get("full")
        if image_url:
            urls_all.append(image_url)
    if not urls_all:
        return None
    await _set_cached(cache_key, urls_all)
    chosen = _first_unused(urls_all)
    if chosen is None:
        logger.info("Unsplash: keywords=%r — вся выдача уже была в канале", cleaned)
    else:
        logger.info("Unsplash: keywords=%r → %d-й результат выдачи",
                    cleaned, urls_all.index(chosen) + 1)
    return chosen
