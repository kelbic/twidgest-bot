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
# Команды которые НЕ в словаре — без ограничений.
# /createchannel здесь НЕТ: создание дайджеста лимитируется по факту успеха
# (CREATE_DIGEST_LIMIT ниже) — ошибки ввода и опечатки не должны съедать лимит.
COMMAND_LIMITS: dict[str, tuple[int, int]] = {
    # Дорогие AI-команды: 3 раза в 5 минут
    "/regenerate": (300, 3),

    # Скаут: LLM + платные fetch'и twitterapi.io — 3 раза в сутки
    "/scout": (86400, 3),

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

        # Вне лички команды не исполняются (бот шлёт кнопку «настроить
        # в личке») — и не должны тратить лимит.
        if event.chat is not None and event.chat.type != "private":
            return await handler(event, data)

        # Админу — без лимитов
        if (
            self._admin_user_id is not None
            and event.from_user.id == self._admin_user_id
        ):
            return await handler(event, data)

        # Извлекаем команду (первое слово, до пробела/аргументов; @бот отрезаем)
        parts = event.text.split()
        cmd = parts[0].split("@")[0].lower() if parts else ""

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


class SuccessRateLimit:
    """Лимит «N успехов за окно»: проверка — до дорогой работы, учёт — только
    после успеха. Так ошибки ввода, неизвестные команды и сорвавшиеся попытки
    лимит не тратят (раньше три опечатки подряд запирали юзера на 5 минут).

    Память процесса, как и у RateLimitMiddleware: защита от спама в моменте."""

    def __init__(self, window_sec: int, max_calls: int) -> None:
        self.window_sec = window_sec
        self.max_calls = max_calls
        self._ok: dict[int, deque[float]] = defaultdict(deque)

    def _trim(self, user_id: int, now: float) -> deque[float]:
        ts = self._ok[user_id]
        while ts and ts[0] <= now - self.window_sec:
            ts.popleft()
        return ts

    def retry_after(self, user_id: int) -> int:
        """0 — можно; иначе сколько секунд ждать."""
        now = time.time()
        ts = self._trim(user_id, now)
        if len(ts) < self.max_calls:
            return 0
        return max(1, int(ts[0] + self.window_sec - now))

    def record(self, user_id: int) -> None:
        self._trim(user_id, time.time()).append(time.time())

    def reset(self) -> None:
        self._ok.clear()


# Создание дайджеста (AI-подбор источников): 3 успешных за 5 минут.
CREATE_DIGEST_LIMIT = SuccessRateLimit(window_sec=300, max_calls=3)
