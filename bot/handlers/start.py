"""Команды /start, /help, /me."""
from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.handlers.onboarding import (
    CREATE_BUTTON,
    connect_channel_button,
    create_digest_kb,
)
from db.repositories.users import get_or_create_user
from db.session import session_maker

router = Router(name="start")


WELCOME = """\
👋 Я <b>TwidgestBot</b> — собираю дайджест новостей из X/Twitter по твоей теме и веду на автопилоте твой Telegram-канал: нахожу авторов, проверяю их по реальным твитам, перевожу и публикую лучшее.

<b>Как начать:</b> нажми «Создать дайджест» и напиши тему одним сообщением — например <i>«новости электромобилей»</i> или <i>«биохакинг и долголетие»</i>. Потом одной кнопкой подключишь свой канал.

<i>Используя бота, вы принимаете <a href="https://kelbic.github.io/twidgest-bot/legal/terms.html">условия</a> и <a href="https://kelbic.github.io/twidgest-bot/legal/privacy.html">политику конфиденциальности</a>.</i>
"""

WELCOME_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text=CREATE_BUTTON, callback_data="qs:ai")],
    [InlineKeyboardButton(text="❓ Как это работает", callback_data="qs:how")],
])

ASK_TOPIC = (
    "Напиши тему дайджеста одним сообщением — я найду и проверю авторов "
    "в X и соберу дайджест.\n\n"
    "Примеры: <i>«новости электромобилей»</i>, "
    "<i>«крикет, премьер-лига Индии»</i>, "
    "<i>«космос и астрономия»</i>"
)

HOW_IT_WORKS = """\
<b>Как это работает</b>

1️⃣ Ты пишешь тему — AI ищет реальных авторов в X, проверяет каждого по его последним твитам (доля текста, активность, тематичность) и собирает <b>дайджест</b>: тему и список источников.

2️⃣ Ты подключаешь свой Telegram-<b>канал</b>: если канала ещё нет — создаёшь пустой, жмёшь кнопку «Подключить Telegram-канал» и выбираешь его. Я сам стану админом с правом публиковать посты.

3️⃣ Дальше всё само: каждые 30 минут собираю свежие твиты, AI-редактор отбирает самое содержательное (а не просто залайканное), переводит и публикует в канал. Подборки — по расписанию.
"""

HELP = """\
📖 <b>Как пользоваться TwidgestBot</b>

<b>Дайджест</b> — тема и источники в X, которые я читаю. <b>Канал</b> — твой Telegram-канал, куда я публикую посты дайджеста.

<b>🎯 Дайджесты (основное):</b>
/channels — мои дайджесты и подключённые каналы
/createchannel &lt;тема&gt; — создать дайджест (или просто напиши тему сообщением)
/deletechannel &lt;№&gt; — удалить дайджест

<b>📡 Источники</b> (№ — номер дайджеста из /channels):
/sources &lt;№&gt; — список источников
/addsource &lt;№&gt; @user — добавить автора из X
/addsource &lt;№&gt; vk:domain — добавить сообщество VK
/removesource &lt;№&gt; @user — убрать источник
/scout &lt;№&gt; — AI-скаут: подберёт новые источники и проверит их по реальным твитам
/setthreshold &lt;№&gt; likes=N retweets=N — порог виральности
/regenerate &lt;№&gt; — заново подобрать все источники через AI
/setinterval &lt;№&gt; — как часто публиковать (кнопками)
/status &lt;№&gt; — детальная статистика

<b>👤 Профиль и оплата:</b>
/me — дайджесты и их статусы
/upgrade — оплата (999⭐ за 30 дней на дайджест, первый — 🎁 триал 7 дней)
/payments — история платежей
/setlegal — юр-фильтр RF-рисков
/setminterest — порог интересности постов (AI-оценка 1-10)
/setsourceinterest — порог интереса для ОДНОГО источника (для смешанных)

<b>ℹ️ Помощь:</b>
/start — начать сначала
/help — эта справка

<b>❓ Частые вопросы:</b>

<b>Как подключить канал?</b>
После создания дайджеста нажми «Подключить Telegram-канал» и выбери свой канал (кнопка есть и в /channels). Если канала ещё нет — сначала создай пустой канал в Telegram.
Не сработала кнопка? Добавь бота админом с правом публиковать посты, напиши в канале слово «тест» и перешли это сообщение сюда. Подпись Telegram „не имеет доступа к сообщениям“ — это норма, она не мешает публикации.

<b>Почему нет постов?</b>
Я проверяю источники раз в 30 минут — после подключения канала жди первый пост до получаса. Подборки выходят раз в несколько часов. Если канал молчит дольше — /scout &lt;№&gt; подберёт более живые источники под твою тему.

<b>Можно ли несколько дайджестов?</b>
Да — каждый со своей темой, источниками и своим каналом.

<b>Как отменить подписку?</b>
Через Telegram Settings → Payments → Subscriptions. Подписка действует до конца оплаченного месяца.

По вопросам — пиши в @TwidgestSupport (если настроено).
"""


async def _welcome_kb(message: Message, user) -> InlineKeyboardMarkup:
    """Есть дайджест без канала — следующий шаг онбординга: подключить канал."""
    has_unlinked = any(
        c.target_chat_id is None and c.archived_at is None
        for c in (getattr(user, "channels", None) or [])
    )
    bot = getattr(message, "bot", None)
    if not has_unlinked or bot is None:
        return WELCOME_KB
    return InlineKeyboardMarkup(inline_keyboard=[
        [await connect_channel_button(bot)],
        *WELCOME_KB.inline_keyboard,
    ])


@router.message(Command("start"))
async def cmd_start(message: Message, command: CommandObject | None = None) -> None:
    """/start и /start setup (кнопка «настроить в личке» из группы) — вход
    в онбординг. setup — служебный параметр, первым касанием не пишется."""
    if message.from_user is None:
        return
    # Воронка витрин: t.me/<bot>?start=src_c5 → users.start_source (первое касание)
    from core.attribution import parse_start_source
    source = parse_start_source(command.args if command else None)
    async with session_maker()() as session:
        user = await get_or_create_user(
            session,
            tg_user_id=message.from_user.id,
            tg_username=message.from_user.username,
            start_source=source,
        )
    if source:
        import logging
        logging.getLogger(__name__).info(
            "start: user %d via %s", message.from_user.id, source)
    await message.answer(
        WELCOME, reply_markup=await _welcome_kb(message, user),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data == "qs:ai")
async def cb_quickstart_ai(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message:
        await callback.message.answer(ASK_TOPIC)


@router.callback_query(F.data == "qs:templates")
async def cb_quickstart_templates(callback: CallbackQuery) -> None:
    """Кнопка из старых приветствий: готовых шаблонов больше нет — ведём к теме."""
    await callback.answer()
    if callback.message:
        await callback.message.answer(ASK_TOPIC)


@router.callback_query(F.data == "qs:how")
async def cb_quickstart_how(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            HOW_IT_WORKS, reply_markup=create_digest_kb(),
            disable_web_page_preview=True,
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
        from bot.handlers.billing import price_display
        from core.plan import (
            DAILY_EVAL_BUDGET_NOTE, MAX_SOURCES_NOTE,
            SLOT_DAYS, channel_status,
        )
        channels = list(user.channels) if hasattr(user, "channels") else []
        st_emoji = {"admin": "🛡", "paid": "🟢", "trial": "🎁", "inactive": "🔴"}
        ch_lines = [
            f"  {st_emoji[channel_status(c)]} <b>{html.escape((c.title or '')[:40])}</b> (№{c.id})"
            for c in channels
        ] or ["  — пока нет, напиши тему одним сообщением"]

        text = (
            f"👤 <b>Ваш профиль</b>\n\n"
            f"Дайджесты:\n" + "\n".join(ch_lines) + "\n\n"
            f"💳 Модель простая: <b>{price_display()} за {SLOT_DAYS} дней</b> "
            f"за дайджест, первый — триал 7 дней.\n"
            f"Лимиты дайджеста: {MAX_SOURCES_NOTE} источников, "
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
        "<i>Если ты тестируешь бот для себя — пока продолжай с Twitter-источниками: "
        "часто помогает сформулировать тему шире или по-английски.</i>"
    )
    await message.answer(text, disable_web_page_preview=True)

