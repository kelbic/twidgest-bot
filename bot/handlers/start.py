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

<i>Используя бота, вы принимаете <a href="https://kelbic.github.io/twidgest-bot/legal/terms.html">условия использования</a> и <a href="https://kelbic.github.io/twidgest-bot/legal/privacy.html">политику конфиденциальности</a>.</i>

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
- Free (пробный, 30 дней) — 10 источников, 3 канала, 50 постов/день
- Pro (2999⭐/мес) — всё то же + Claude LLM, digest каждые 3ч

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
/addsource &lt;id&gt; @user — добавить Twitter-источник
/addsource &lt;id&gt; vk:domain — добавить VK-источник
/removesource &lt;id&gt; @user — удалить источник
/regenerate &lt;id&gt; — перегенерить все источники канала через AI
/status &lt;id&gt; — детальная статистика канала
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
    await message.answer(WELCOME, disable_web_page_preview=True)


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


@router.message(Command("legal"))
async def cmd_legal(message: Message) -> None:
    text = (
        "📜 <b>Юридическая информация</b>\n\n"
        "<a href=\"https://kelbic.github.io/twidgest-bot/legal/privacy.html\">"
        "Политика конфиденциальности</a>\n"
        "<a href=\"https://kelbic.github.io/twidgest-bot/legal/terms.html\">"
        "Пользовательское соглашение</a>\n\n"
        "Используя бота, вы соглашаетесь с условиями указанных документов.\n\n"
        "Вопросы? Создайте issue в "
        "<a href=\"https://github.com/kelbic/twidgest-bot/issues\">GitHub</a>."
    )
    await message.answer(text, disable_web_page_preview=True)


@router.message(Command("tg_help"))
async def cmd_tg_help(message: Message) -> None:
    """Помощь по добавлению Telegram-каналов как источников (manual setup)."""
    text = (
        "📡 <b>Telegram-каналы как источники</b>\n\n"
        "Если твоя тема плохо покрывается X (Twitter) — например, региональные "
        "новости РФ, узкоспециализированные ниши, русскоязычный контент — "
        "можно использовать публичные Telegram-каналы как альтернативу.\n\n"
        "<b>Сейчас это в режиме ручной настройки:</b>\n"
        "1. Найди 3-5 публичных Telegram-каналов по своей теме\n"
        "2. Скопируй их usernames (например, @lentaru, @meduzaproject)\n"
        "3. Напиши администратору <a href=\"https://t.me/kelbic\">@kelbic</a> "
        "со ссылками — настрою вручную в течение дня\n\n"
        "Автоматическая интеграция с TGStat API в работе — будет доступна "
        "следующим этапом для Pro-тарифа.\n\n"
        "<i>Если ты тестируешь бот для себя — пока продолжай с Twitter-источниками "
        "или используй /templates с готовой темой.</i>"
    )
    await message.answer(text, disable_web_page_preview=True)

