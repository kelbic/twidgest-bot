"""Команды /start, /help, /me."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from db.repositories.users import get_or_create_user
from db.session import session_maker

router = Router(name="start")


WELCOME = """\
👋 Я <b>TwidgestBot</b> — превращаю X/Twitter в живой новостной Telegram-канал на автопилоте: нахожу авторов по теме, проверяю их по реальным твитам, перевожу и публикую лучшее.

<b>Просто напиши тему канала одним сообщением</b> — например: <i>«новости электромобилей»</i> или <i>«биохакинг и longevity»</i>.

<i>Используя бота, вы принимаете <a href="https://kelbic.github.io/twidgest-bot/legal/terms.html">условия</a> и <a href="https://kelbic.github.io/twidgest-bot/legal/privacy.html">политику конфиденциальности</a>.</i>
"""

WELCOME_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🚀 Создать канал по теме", callback_data="qs:ai")],
    [InlineKeyboardButton(text="📋 Готовые шаблоны", callback_data="qs:templates")],
    [InlineKeyboardButton(text="❓ Как это работает", callback_data="qs:how")],
])

HOW_IT_WORKS = """\
<b>Как это работает</b>

1️⃣ Ты пишешь тему — AI ищет реальных авторов в X, проверяет каждого по его последним твитам (доля текста, активность, тематичность) и создаёт канал-заготовку.

2️⃣ Ты создаёшь Telegram-канал, добавляешь меня админом с правом «Публикация сообщений» и пересылаешь мне любое сообщение из него — привязка автоматическая.

3️⃣ Дальше всё само: каждые 30 минут собираю свежие твиты, AI-редактор отбирает самое содержательное (а не просто залайканное), переводит и публикует. Дайджесты — по расписанию.

Команды и FAQ: /help
"""

HELP = """\
📖 <b>Все команды TwidgestBot</b>

<b>🎯 Каналы (основное):</b>
/channels — список твоих каналов
/templates — готовые шаблоны проверенных тем
/createchannel template &lt;id&gt; — из шаблона
/createchannel ai &lt;тема&gt; — AI подберёт источники
/deletechannel &lt;id&gt; — удалить канал

<b>📡 Источники:</b>
/sources &lt;id&gt; — список источников канала
/addsource &lt;id&gt; @user — добавить Twitter-источник
/addsource &lt;id&gt; vk:domain — добавить VK-источник
/removesource &lt;id&gt; @user — удалить источник
/scout &lt;id&gt; — AI-скаут: подберёт новые источники и проверит их по реальным твитам
/setthreshold &lt;id&gt; likes=N retweets=N — порог виральности
/regenerate &lt;id&gt; — перегенерить все источники канала через AI
/status &lt;id&gt; — детальная статистика канала
/deletechannel &lt;id&gt; — удалить канал
/bind &lt;chat_id&gt; &lt;channel_id&gt; — вручную привязать чат

<b>👤 Профиль и оплата:</b>
/me — каналы и их статусы
/upgrade — оплата каналов (999⭐ за 30 дней на канал,
первый канал — 🎁 триал 7 дней)
/payments — история платежей
/setlegal — юр-фильтр RF-рисков по каналам

<b>ℹ️ Помощь:</b>
/start — приветствие
/help — эта справка

<b>❓ Частые вопросы:</b>

<b>Как привязать канал?</b>
После создания бот-канала — создай Telegram-канал, добавь меня админом, перешли мне любое сообщение оттуда.

<b>Почему нет постов?</b>
Бот проверяет источники раз в 30 минут. После привязки жди до 30 минут первого поста. Digest-режим публикует раз в N часов (по умолчанию 12). Если канал молчит дольше — /scout &lt;id&gt; подберёт более живые источники под твою тему.

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
    await message.answer(
        WELCOME, reply_markup=WELCOME_KB, disable_web_page_preview=True
    )


@router.callback_query(F.data == "qs:ai")
async def cb_quickstart_ai(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "Напиши тему канала одним сообщением — я найду и проверю "
            "авторов в X и создам канал.\n\n"
            "Примеры: <i>«новости электромобилей»</i>, "
            "<i>«крикет, премьер-лига Индии»</i>, "
            "<i>«космос и астрономия»</i>"
        )


@router.callback_query(F.data == "qs:templates")
async def cb_quickstart_templates(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message:
        # cmd_templates использует только message.answer — безопасно
        from bot.handlers.channels import cmd_templates
        await cmd_templates(callback.message)


@router.callback_query(F.data == "qs:how")
async def cb_quickstart_how(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            HOW_IT_WORKS, disable_web_page_preview=True
        )


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
        from core.plan import (
            DAILY_EVAL_BUDGET_NOTE, MAX_SOURCES_NOTE, PRICE_STARS,
            SLOT_DAYS, channel_status,
        )
        channels = list(user.channels) if hasattr(user, "channels") else []
        st_emoji = {"admin": "🛡", "paid": "🟢", "trial": "🎁", "inactive": "🔴"}
        ch_lines = [
            f"  {st_emoji[channel_status(c)]} <b>{c.title[:40]}</b> (id={c.id})"
            for c in channels
        ] or ["  — пока нет, напиши тему одним сообщением"]

        text = (
            f"👤 <b>Ваш профиль</b>\n\n"
            f"Каналы:\n" + "\n".join(ch_lines) + "\n\n"
            f"💳 Модель простая: <b>{PRICE_STARS}⭐ за {SLOT_DAYS} дней</b> "
            f"на канал, первый канал — триал 7 дней.\n"
            f"Лимиты канала: {MAX_SOURCES_NOTE} источников, "
            f"{DAILY_EVAL_BUDGET_NOTE} AI-оценок/день.\n\n"
            f"Статусы и оплата: /upgrade · Платежи: /payments"
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

