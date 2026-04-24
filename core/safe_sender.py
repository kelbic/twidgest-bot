"""Отправка в Telegram-канал с обработкой типичных ошибок и автодеактивацией target."""
from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Target

logger = logging.getLogger(__name__)


async def send_to_target(
    bot: Bot,
    session: AsyncSession,
    target: Target,
    text: str,
) -> bool:
    """Возвращает True при успехе, False при ошибке. Деактивирует target при критичных ошибках."""
    try:
        await bot.send_message(
            chat_id=target.chat_id,
            text=text,
            disable_web_page_preview=True,
        )
        return True
    except TelegramRetryAfter as exc:
        logger.warning(
            "Telegram rate limit on target %s, retry_after=%s",
            target.chat_id,
            exc.retry_after,
        )
        # rate-limit — это временно, не деактивируем
        import asyncio
        await asyncio.sleep(exc.retry_after)
        try:
            await bot.send_message(
                chat_id=target.chat_id,
                text=text,
                disable_web_page_preview=True,
            )
            return True
        except Exception:
            logger.exception("Retry after rate-limit also failed for %s", target.chat_id)
            return False
    except TelegramForbiddenError as exc:
        # Бот удалён из канала / заблокирован — деактивируем
        logger.warning("Bot forbidden in chat %s: %s. Deactivating target.", target.chat_id, exc)
        await _deactivate_target(session, target.id)
        return False
    except TelegramBadRequest as exc:
        # chat not found / chat_id wrong / parse error
        msg = str(exc).lower()
        if "chat not found" in msg or "not enough rights" in msg or "user is deactivated" in msg:
            logger.warning(
                "Target %s unrecoverable: %s. Deactivating.", target.chat_id, exc
            )
            await _deactivate_target(session, target.id)
        else:
            logger.warning("Telegram bad request for %s: %s", target.chat_id, exc)
        return False
    except Exception:
        logger.exception("Unexpected error sending to %s", target.chat_id)
        return False


async def _deactivate_target(session: AsyncSession, target_id: int) -> None:
    result = await session.execute(select(Target).where(Target.id == target_id))
    target = result.scalar_one_or_none()
    if target:
        target.is_active = False
        await session.commit()
