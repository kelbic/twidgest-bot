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
    photo_url: str | None = None,
) -> bool:
    """Шлёт пост в Telegram. Если photo_url задан — отправляет как фото с подписью.
    
    Возвращает True при успехе, False при ошибке. Деактивирует target при критичных ошибках.
    """

    # ПОСЛЕДНЯЯ ЛИНИЯ ОБОРОНЫ: defense-in-depth от мета-ответов LLM
    # Если каким-то образом мета-ответ дошёл до этой точки — не публикуем
    text_lower = text.lower()
    danger_phrases = (
        "к сожалению", "извините", "приносим извинения",
        "не могу составить", "не подходят для", "не могу опубликовать",
        "пожалуйста, предоставьте", "пожалуйста предоставьте",
        "недостаточно информации", "не вижу", "невозможно составить",
        "касаются военно-политических", "попадают под ограничения",
        "потребуются твиты", "предоставьте, пожалуйста",
        "нужны твиты с", "не хватает данных",
        "i'm sorry", "i cannot", "i apologize", "unfortunately",
        "please provide", "the provided tweets", "i am unable",
    )
    if any(p in text_lower for p in danger_phrases):
        logger.error(
            "BLOCKED meta-response from publishing! Channel %s, preview: %s",
            target.chat_id, text[:200],
        )
        return False

    # Также проверка структуры — если нет ни одной ссылки, скорее всего это не пост
    if "<a href" not in text and "→" not in text:
        logger.warning(
            "BLOCKED text without any links/arrows — not a real post. Preview: %s",
            text[:200],
        )
        return False

    # Telegram caption limit = 1024 chars. Если текст длиннее — режем для photo.
    # Для текстового сообщения лимит 4096, обычно хватает.
    CAPTION_LIMIT = 1024

    async def _send_photo():
        # Пытаемся отправить как фото с подписью
        caption = text
        if len(caption) > CAPTION_LIMIT:
            # Режем оставив место под маркер
            caption = caption[: CAPTION_LIMIT - 30].rstrip() + "...\n\n<i>(полный текст в источнике)</i>"
        await bot.send_photo(
            chat_id=target.chat_id,
            photo=photo_url,
            caption=caption,
        )

    async def _send_text():
        await bot.send_message(
            chat_id=target.chat_id,
            text=text,
            disable_web_page_preview=True,
        )

    try:
        if photo_url:
            try:
                await _send_photo()
            except TelegramBadRequest as exc:
                # Картинка может не загрузиться (404, taken down, etc.) — фолбэк на текст
                msg = str(exc).lower()
                if "wrong file" in msg or "failed to get http" in msg or "image_process" in msg:
                    logger.warning(
                        "Photo upload failed (%s), falling back to text. URL: %s",
                        exc, photo_url,
                    )
                    await _send_text()
                else:
                    raise
        else:
            await _send_text()
        return True
    except TelegramRetryAfter as exc:
        logger.warning(
            "Telegram rate limit on target %s, retry_after=%s",
            target.chat_id,
            exc.retry_after,
        )
        import asyncio
        await asyncio.sleep(exc.retry_after)
        try:
            if photo_url:
                await _send_photo()
            else:
                await _send_text()
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
