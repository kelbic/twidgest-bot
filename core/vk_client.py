"""VK API client for reading public community posts.

Использует Service Token из VK_ACCESS_TOKEN (.env).
Поддерживает только публичные методы (wall.get, groups.search, groups.getById).

Identifier formats supported:
- 'lentaru' (short name / domain)
- 'club12345' (numeric ID with prefix)
- 'public12345' (alternative numeric prefix)
- '12345' (raw numeric)
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

VK_API_BASE = "https://api.vk.com/method"
VK_API_VERSION = "5.199"

# Retry config (соответствует twitter_client.py)
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2.0  # seconds
REQUEST_TIMEOUT = 15.0

# Минимальное количество подписчиков для community при AI-подборе
MIN_COMMUNITY_FOLLOWERS = 1000


@dataclass
class VKPost:
    """Нормализованный пост VK для дальнейшей обработки."""
    id: int
    owner_id: int          # отрицательный для групп
    text: str
    url: str               # https://vk.com/wall-{owner_id}_{id}
    date: int              # unix timestamp
    likes: int
    reposts: int
    views: int
    comments: int
    image_url: str | None  # лучший размер картинки или None
    is_pinned: bool


@dataclass
class VKCommunity:
    """Метаданные VK-сообщества."""
    id: int                # положительный ID
    domain: str            # короткое имя или 'club{id}'
    name: str              # отображаемое название
    description: str
    members_count: int
    is_closed: int         # 0 — open, 1 — closed, 2 — private


class VKClient:
    """Async клиент VK API с retry + exp backoff."""

    def __init__(self, access_token: str):
        if not access_token:
            raise ValueError("VK access_token is required")
        self.access_token = access_token

    async def _call(
        self, method: str, params: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Низкоуровневый вызов VK API с retry."""
        full_params = {
            **params,
            "access_token": self.access_token,
            "v": VK_API_VERSION,
        }
        url = f"{VK_API_BASE}/{method}"

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url, params=full_params) as resp:
                        data = await resp.json()

                        if "error" in data:
                            err = data["error"]
                            code = err.get("error_code")
                            msg = err.get("error_msg", "")

                            # Specific errors that won't help with retry
                            if code in (5, 15, 27, 28, 29, 100):
                                logger.warning(
                                    "VK API error %s: %s (method=%s, params=%s)",
                                    code, msg, method, params,
                                )
                                return None

                            # Other errors — retry
                            logger.warning(
                                "VK API error %s: %s, retry %d/%d",
                                code, msg, attempt, MAX_RETRIES,
                            )
                        else:
                            return data.get("response")

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning(
                    "VK API network error: %s, retry %d/%d",
                    e, attempt, MAX_RETRIES,
                )

            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_BACKOFF_BASE ** attempt)

        logger.error("VK API failed after %d attempts: %s", MAX_RETRIES, method)
        return None

    @staticmethod
    def parse_identifier(raw: str) -> str | None:
        """Парсит VK identifier из разных форматов.

        Принимает:
        - 'vk:lentaru' → 'lentaru'
        - 'vk:club12345' → 'club12345'
        - 'https://vk.com/lentaru' → 'lentaru'
        - 'https://vk.com/club12345' → 'club12345'
        - 'https://m.vk.com/lentaru' → 'lentaru'
        - 'lentaru' → 'lentaru'
        - 'club12345' → 'club12345'

        Возвращает None если не VK identifier.
        """
        if not raw:
            return None
        s = raw.strip()

        # Убираем префикс vk:
        if s.lower().startswith("vk:"):
            s = s[3:].strip()

        # Извлекаем из URL
        url_match = re.match(
            r"^https?://(?:m\.)?vk\.com/([\w.]+)",
            s,
        )
        if url_match:
            s = url_match.group(1)

        # Чистим от лишних символов
        s = s.strip().lower()
        if not s:
            return None

        # Валидация: alphanumeric + . _
        if not re.match(r"^[\w.]{2,}$", s):
            return None

        return s

    @staticmethod
    def build_post_url(owner_id: int, post_id: int) -> str:
        """Создаёт ссылку на пост вида https://vk.com/wall-12345_678"""
        return f"https://vk.com/wall{owner_id}_{post_id}"

    @staticmethod
    def _pick_best_photo_url(attachments: list[dict]) -> str | None:
        """Из списка attachments выбирает URL лучшей картинки.

        Предпочитает размеры 'x' (640w), потом 'y' (1080w), потом любой.
        """
        for att in attachments:
            if att.get("type") != "photo":
                continue
            sizes = att.get("photo", {}).get("sizes", [])
            if not sizes:
                continue

            # Приоритет: x (640) → y (1080) → r (540) → q (360) → max
            preference = ["x", "y", "r", "q", "z", "m"]
            for pref in preference:
                for s in sizes:
                    if s.get("type") == pref and s.get("url"):
                        return s["url"]

            # Fallback: первый с URL
            for s in sizes:
                if s.get("url"):
                    return s["url"]

        return None

    def _normalize_post(self, raw: dict) -> VKPost | None:
        """Превращает raw VK post в VKPost. Возвращает None если пост не подходит."""
        post_id = raw.get("id")
        owner_id = raw.get("owner_id")
        if post_id is None or owner_id is None:
            return None

        # Пропускаем рекламные
        if raw.get("marked_as_ads"):
            return None

        return VKPost(
            id=post_id,
            owner_id=owner_id,
            text=raw.get("text", "") or "",
            url=self.build_post_url(owner_id, post_id),
            date=raw.get("date", 0),
            likes=raw.get("likes", {}).get("count", 0),
            reposts=raw.get("reposts", {}).get("count", 0),
            views=raw.get("views", {}).get("count", 0),
            comments=raw.get("comments", {}).get("count", 0),
            image_url=self._pick_best_photo_url(raw.get("attachments", [])),
            is_pinned=bool(raw.get("is_pinned", 0)),
        )

    async def get_community_posts(
        self, identifier: str, count: int = 20, skip_pinned: bool = True
    ) -> list[VKPost]:
        """Возвращает последние посты публичного сообщества.

        identifier: domain или 'club12345'.
        skip_pinned: True — закреплённые пропускаются (обычно реклама/welcome).
        """
        params = {"domain": identifier, "count": count}
        # offset=1 если пропускаем pinned (он всегда первый)
        if skip_pinned:
            params["offset"] = 1
            params["count"] = count + 1  # компенсируем offset

        response = await self._call("wall.get", params)
        if not response:
            return []

        items = response.get("items", [])
        posts: list[VKPost] = []
        for raw in items:
            normalized = self._normalize_post(raw)
            if normalized is None:
                continue
            if skip_pinned and normalized.is_pinned:
                continue
            posts.append(normalized)
            if len(posts) >= count:
                break

        logger.info("VK: fetched %d posts from @%s", len(posts), identifier)
        return posts

    async def search_communities(
        self, query: str, count: int = 20
    ) -> list[VKCommunity]:
        """Поиск VK-сообществ по запросу.

        Возвращает список с >=MIN_COMMUNITY_FOLLOWERS подписчиков.
        """
        params = {
            "q": query,
            "count": count,
            "type": "page,group",
            "fields": "members_count,description",
        }
        response = await self._call("groups.search", params)
        if not response:
            return []

        items = response.get("items", [])
        communities: list[VKCommunity] = []
        for raw in items:
            members = raw.get("members_count", 0)
            if members < MIN_COMMUNITY_FOLLOWERS:
                continue
            communities.append(VKCommunity(
                id=raw.get("id", 0),
                domain=raw.get("screen_name") or f"club{raw.get('id')}",
                name=raw.get("name", ""),
                description=raw.get("description", "") or "",
                members_count=members,
                is_closed=raw.get("is_closed", 0),
            ))

        logger.info(
            "VK search '%s' → %d communities (>= %d followers)",
            query, len(communities), MIN_COMMUNITY_FOLLOWERS,
        )
        return communities

    async def validate_community(self, identifier: str) -> VKCommunity | None:
        """Проверяет что сообщество существует и оно публичное."""
        params = {
            "group_id": identifier,
            "fields": "members_count,description",
        }
        response = await self._call("groups.getById", params)
        if not response:
            return None

        # Старый API возвращает list, новый — dict с groups
        groups = response if isinstance(response, list) else response.get("groups", [])
        if not groups:
            return None

        raw = groups[0]
        return VKCommunity(
            id=raw.get("id", 0),
            domain=raw.get("screen_name") or identifier,
            name=raw.get("name", ""),
            description=raw.get("description", "") or "",
            members_count=raw.get("members_count", 0),
            is_closed=raw.get("is_closed", 0),
        )
