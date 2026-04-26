"""Команды /start, /help, /me."""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from db.repositories.users import get_or_create_user, is_tier_active
from db.session import session_maker
from tiers import get_limits

router = Router(name="start")


WELCOME = """\
👋 Привет! Я <b>TwidgestBot</b> — автоматизирую новостные каналы в Telegram.

<b>Что я умею:</b>
- Слежу за твиттер-аккаунтами и постлю их контент тебе в канал
- Перевожу твиты на русский через ИИ
- Фильтрую по engagement (лайки, ретвиты)
- Собираю дайджесты — отдельными постами или сводкой раз в N часов

🚀 <b>Быстрый старт за 3 шага:</b>

<b>1.</b> Создай свой канал в боте:
  • /templates — 15 готовых тем (AI, крипта, longevity, спорт...)
  • /createchannel template longevity — взять шаблон
  • /createchannel ai крикет, IPL — AI подберёт источники по описанию

<b>2.</b> Привяжи Telegram-канал:
  • Создай канал в Telegram (или используй существующий)
  • Добавь @TwidgestBot админом с правом «Публикация сообщений»
  • Перешли мне любое сообщение из канала

<b>3.</b> Готово! Бот сам определит канал и привяжет к нему источники.
Через 30 минут в канале появится первый пост.

🏆 <b>Тарифы:</b>
- Free — 3 источника, 1 канал, 5 постов/день
- Starter (99⭐) — 10 источников, 2 канала, digest-режим
- Pro (299⭐) — 30 источников, 5 каналов, Claude LLM

Команды: /help
"""

HELP = """\
📖 <b>Все команды TwidgestBot</b>

<b>🎯 Каналы (основное):</b>
/channels — список твоих каналов
/templates — 15 готовых шаблонов
/createchannel template &lt;id&gt; — из шаблона
/createchannel ai &lt;тема&gt; — AI подберёт источники
/deletechannel &lt;id&gt; — удалить канал

<b>📡 Источники:</b>
/sources &lt;id&gt; — список источников канала
/addsource &lt;id&gt; @user — добавить источник
/removesource &lt;id&gt; @user — удалить источник
/regenerate &lt;id&gt; — перегенерить все источники канала через AI
/deletechannel &lt;id&gt; — удалить канал
/bind &lt;chat_id&gt; &lt;channel_id&gt; — вручную привязать чат

<b>👤 Профиль:</b>
/me — тариф и лимиты
/upgrade — тарифы и покупка
/payments — история платежей

<b>ℹ️ Помощь:</b>
/start — приветствие
/help — эта справка

<b>❓ Частые вопросы:</b>

<b>Как привязать канал?</b>
После создания бот-канала — создай Telegram-канал, добавь меня админом, перешли мне любое сообщение оттуда.

<b>Почему нет постов?</b>
Бот проверяет источники раз в 30 минут. После привязки жди до 30 минут первого поста. Digest-режим публикует раз в N часов (по умолчанию 12).

<b>Можно ли несколько каналов?</b>
Да, от Starter — до 2, от Pro — до 5. Каждый со своей темой и источниками.

<b>Как отменить подписку?</b>
Через Telegram Settings → Payments → Subscriptions. Подписка действует до конца оплаченного месяца.

По вопросам — пиши в @TwidgestSupport (если настроено).
"""


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    if message.from_user is None:
        return
    async with session_maker()() as session:
        await get_or_create_user(
            session,
            tg_user_id=message.from_user.id,
            tg_username=message.from_user.username,
        )
    await message.answer(WELCOME)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP)


@router.message(Command("me"))
async def cmd_me(message: Message) -> None:
    if message.from_user is None:
        return
    async with session_maker()() as session:
        user = await get_or_create_user(
            session,
            tg_user_id=message.from_user.id,
            tg_username=message.from_user.username,
        )
        active = await is_tier_active(user)
        effective_tier = user.tier if active else "free"
        limits = get_limits(effective_tier)

        sources_count = sum(len(c.channel_sources) for c in user.channels) if hasattr(user, 'channels') else 0
        channels_count = len(user.channels) if hasattr(user, 'channels') else 0

        text = (
            f"👤 <b>Ваш профиль</b>\n\n"
            f"Тариф: <b>{limits.name}</b>"
            f"{' (истёк, действует Free)' if not active else ''}\n"
            f"Каналов: <b>{channels_count}/{limits.max_targets}</b>\n"
            f"Источников всего: <b>{sources_count}</b>\n"
            f"Постов/день: до <b>{limits.max_posts_per_day}</b>\n\n"
            f"Digest-режим: {'✅' if limits.can_use_digest_mode else '❌ (только Starter+)'}\n"
            f"Custom-промпт: {'✅' if limits.can_use_custom_prompt else '❌ (только Pro+)'}\n"
            f"Pro-LLM (Claude): {'✅' if limits.use_pro_llm else '❌'}\n\n"
            f"Хотите больше? /upgrade"
        )
        await message.answer(text)
