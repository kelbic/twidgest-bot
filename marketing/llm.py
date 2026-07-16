"""Единая точка связки с OpenRouterClient прода + дневной счётчик вызовов.

Все LLM-вызовы marketing-бота идут через MarketingLLM.call() — здесь
проверяется персистентный дневной кап (переживает рестарты). Кап исчерпан →
вежливый None, не исключение; причина доступна в last_refusal.
"""
from __future__ import annotations

import logging

from core.llm_client import OpenRouterClient

from marketing import repo
from marketing.db import session_maker

logger = logging.getLogger(__name__)

COUNTER_LLM = "llm_calls"


class MarketingLLM:
    def __init__(self, client: OpenRouterClient, daily_cap: int) -> None:
        self.client = client
        self.daily_cap = daily_cap
        self.last_refusal: str | None = None

    async def _consume(self) -> bool:
        async with session_maker()() as session:
            ok = await repo.try_consume(session, COUNTER_LLM, self.daily_cap)
        if not ok:
            self.last_refusal = f"дневной кап LLM ({self.daily_cap}) исчерпан"
            logger.warning("LLM daily cap (%d) exhausted, call refused", self.daily_cap)
        return ok

    async def call(
        self,
        system: str,
        user: str,
        max_tokens: int = 800,
        temperature: float = 0.3,
    ) -> str | None:
        """Низкоуровневый вызов LLM с учётом капа."""
        self.last_refusal = None
        if not await self._consume():
            return None
        # _call_with_retry — приватный API прод-клиента; это единственное
        # место, где мы его трогаем (осознанная точка связки из ТЗ).
        return await self.client._call_with_retry(
            system, user, max_tokens=max_tokens, temperature=temperature
        )

    async def suggest_sources(
        self, topic: str, count: int = 12
    ) -> list[dict[str, str]] | None:
        """Подбор X-аккаунтов по теме (для демо). Тоже под капом."""
        self.last_refusal = None
        if not await self._consume():
            return None
        return await self.client.suggest_sources(topic, count=count)
