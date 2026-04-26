"""Rate limiting middleware: ограничивает частоту дорогих команд per-user.

Использует in-memory словарь {(user_id, command): [timestamps]}.
Не выживает рестарт — это намеренно (защита от спама в моменте, не про учёт).
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message

logger = logging.getLogger(__name__)


# Лимиты: (window_seconds, max_calls)
# Команды которые НЕ в словаре — без ограничений
COMMAND_LIMITS: dict[str, tuple[int, int]] = {
    # Дорогие AI-команды: 3 раза в 5 минут
    "/createchannel": (300, 3),
    "/regenerate": (300, 3),

    # Twitter API звонки: 10 в минуту
    "/addsource": (60, 10),
    "/sources": (60, 20),
    "/status": (60, 20),
    "/channels": (60, 20),

    # Просто чтобы не спамил кнопками billing
    "/upgrade": (60, 5),
}


class RateLimitMiddleware(BaseMiddleware):
    """Отбрасывает или предупреждает при превышении лимитов команд."""

    def __init__(self, admin_user_id: int | None = None) -> None:
        # {(user_id, command): deque of timestamps}
        self._calls: dict[tuple[int, str], deque[float]] = defaultdict(deque)
        # Админ освобождён от лимитов
        self._admin_user_id = admin_user_id

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        if event.from_user is None or event.text is None:
            return await handler(event, data)

        # Админу — без лимитов
        if (
            self._admin_user_id is not None
            and event.from_user.id == self._admin_user_id
        ):
            return await handler(event, data)

        # Извлекаем команду (первое слово, до пробела/аргументов)
        cmd = event.text.split()[0].split("@")[0].lower()

        # Команда из shortcut'ов вроде /createchannel_longevity → нормализуем
        if cmd.startswith("/createchannel_"):
            cmd = "/createchannel"

        if cmd not in COMMAND_LIMITS:
            return await handler(event, data)

        window_sec, max_calls = COMMAND_LIMITS[cmd]
        now = time.time()
        key = (event.from_user.id, cmd)
        timestamps = self._calls[key]

        # Чистим старые записи вне окна
        while timestamps and timestamps[0] < now - window_sec:
            timestamps.popleft()

        # Проверяем лимит
        if len(timestamps) >= max_calls:
            wait_seconds = int(timestamps[0] + window_sec - now)
            logger.warning(
                "Rate limit exceeded: user=%s cmd=%s (%d calls in %ds, "
                "wait %ds)",
                event.from_user.id, cmd, len(timestamps), window_sec,
                wait_seconds,
            )
            await event.answer(
                f"⏳ Команда <code>{cmd}</code> временно ограничена.\n"
                f"Лимит: {max_calls} раз за {window_sec // 60 if window_sec >= 60 else window_sec}"
                f"{'мин' if window_sec >= 60 else 'сек'}.\n"
                f"Попробуй через {wait_seconds} сек."
            )
            return None  # не пропускаем дальше

        # Записываем вызов и пропускаем
        timestamps.append(now)
        return await handler(event, data)
