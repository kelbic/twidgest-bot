"""Отправка в Telegram-канал с обработкой типичных ошибок и автодеактивацией.

ФИКС кросс-тенантного бага: раньше воркеры передавали анонимный FakeTarget
с id=channel.id, а _deactivate_target гасил строку в таблице targets по этому
id. Channel.id и Target.id — независимые автоинкременты, поэтому при потере
доступа к каналу N деактивировался ЧУЖОЙ target с совпавшим id, а сам канал
оставался активным и фейлился каждый цикл.

Теперь воркеры передают ChannelTarget, и деактивация бьёт ровно в Channel,
а владелец получает уведомление (best-effort), почему канал замолчал.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Channel

logger = logging.getLogger(__name__)


@dataclass
class ChannelTarget:
    """Куда постим: канал из модели Channel."""

    channel_id: int
    chat_id: int


async def send_to_target(
    bot: Bot,
    session: AsyncSession,
    target: ChannelTarget,
    text: str,
    photo_url: str | None = None,
) -> bool:
    """Шлёт пост в Telegram. Если photo_url задан — отправляет как фото с подписью.

    Возвращает True при успехе, False при ошибке.
    При критичных ошибках (бот выкинут из канала, чат не существует)
    деактивирует Channel и уведомляет владельца.
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
        # Бот удалён из канала / заблокирован — деактивируем Channel
        logger.warning(
            "Bot forbidden in chat %s: %s. Deactivating channel %d.",
            target.chat_id, exc, target.channel_id,
        )
        await _deactivate_channel(bot, session, target.channel_id)
        return False
    except TelegramBadRequest as exc:
        # chat not found / chat_id wrong / parse error
        msg = str(exc).lower()
        if "chat not found" in msg or "not enough rights" in msg or "user is deactivated" in msg:
            logger.warning(
                "Channel %d unrecoverable (chat %s): %s. Deactivating.",
                target.channel_id, target.chat_id, exc,
            )
            await _deactivate_channel(bot, session, target.channel_id)
        else:
            logger.warning("Telegram bad request for %s: %s", target.chat_id, exc)
        return False
    except Exception:
        logger.exception("Unexpected error sending to %s", target.chat_id)
        return False


async def _deactivate_channel(
    bot: Bot, session: AsyncSession, channel_id: int
) -> None:
    """Деактивирует Channel и уведомляет владельца (best-effort)."""
    result = await session.execute(
        select(Channel).where(Channel.id == channel_id)
    )
    channel = result.scalar_one_or_none()
    if channel is None:
        logger.error("Cannot deactivate: channel %d not found", channel_id)
        return

    channel.is_active = False
    await session.commit()
    logger.warning(
        "Channel %d («%s») deactivated: bot lost access to chat %s",
        channel.id, channel.title, channel.target_chat_id,
    )

    # Уведомляем владельца, иначе он не поймёт, почему канал замолчал
    try:
        await bot.send_message(
            chat_id=channel.user_id,
            text=(
                f"⚠️ Канал <b>«{channel.title}»</b> остановлен: бот потерял "
                f"доступ к привязанному Telegram-каналу (удалён из админов "
                f"или канал не существует).\n\n"
                f"Верни бота админом с правом Post Messages и перепривяжи "
                f"канал — форвардни в бота любое сообщение из него. "
                f"Список каналов: /channels"
            ),
            disable_web_page_preview=True,
        )
    except Exception:
        logger.info(
            "Could not notify owner %d about channel %d deactivation",
            channel.user_id, channel.id,
        )
