"""Middleware для команд /admin: пропускает только владельца бота."""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message

logger = logging.getLogger(__name__)


class AdminOnlyMiddleware(BaseMiddleware):
    def __init__(self, admin_user_id: int) -> None:
        self.admin_user_id = admin_user_id

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        user = event.from_user
        if user is None or user.id != self.admin_user_id:
            logger.warning(
                "Unauthorized /admin attempt from user %s",
                user.id if user else "unknown",
            )
            # Молчим — не выдаём, что admin-команды существуют
            return None
        return await handler(event, data)
