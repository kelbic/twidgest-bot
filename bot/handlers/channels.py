"""Дайджесты юзера: /channels (список), /createchannel (создание), /deletechannel.

В текстах для юзера сущность бота — «дайджест», «канал» — только его настоящий
Telegram-канал. Готовые шаблоны из пользовательского флоу убраны (templates.py
остаётся для аудита tools/audit_templates.py); создание — AI-подбор по теме.
"""
from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from bot.handlers.onboarding import (
    CONNECT_FALLBACK,
    CONNECT_STEP,
    connect_channel_button,
    connect_channel_kb,
    create_digest_kb,
)
from bot.middlewares.rate_limit import CREATE_DIGEST_LIMIT
from config import Config
from core.llm_client import OpenRouterClient
from core.plan import MAX_CHANNELS_PER_USER, PRICE_STARS, channel_status
from core.twitter_cache import TwitterCache
from core.twitter_client import TwitterClient
from workers.source_scout import apply_topic_relevance, prevalidate_candidates
from db.repositories.channels import (
    create_channel,
    delete_channel,
    get_user_channels,
)
from db.repositories.users import get_or_create_user
from db.session import session_maker

# Shared clients for AI-assisted channel creation
logger = logging.getLogger(__name__)

_cfg = Config()
# Default LLM (Haiku) для всех задач
_llm = OpenRouterClient(_cfg.openrouter_api_key, _cfg.openrouter_model_default)
# Smart LLM (Sonnet) для критичных задач: keyword generation, candidate ranking
# Используется реже, не критично по бюджету
_llm_smart = OpenRouterClient(_cfg.openrouter_api_key, _cfg.openrouter_model_pro)
_twitter = TwitterClient(_cfg.twitter_api_key)
_scout_cache = TwitterCache(_twitter, ttl_seconds=1800)

router = Router(name="channels")

# Юзеры, у которых прямо сейчас идёт создание: вторая попытка, пока первая
# не закончилась, обходила бы лимит «по успеху» — отбиваем её сразу.
_creating: set[int] = set()


CREATE_HELP = """🎯 <b>Создание дайджеста</b>

Напиши тему одним сообщением — например <i>«новости электромобилей»</i> \
или <i>«крикет, премьер-лига Индии»</i>. Я найду авторов в X, проверю каждого \
по реальным твитам и соберу дайджест, а потом помогу подключить твой Telegram-канал.

Можно и командой: <code>/createchannel новости AI и LLM</code>
"""


# Режим публикации — по-русски (в БД: hybrid | digest | single)
_MODE_RU = {
    "hybrid": "подборки + самое обсуждаемое",
    "digest": "подборки",
    "single": "посты по одному",
}


def _max_digests_text() -> str:
    return (
        f"❌ Максимум {MAX_CHANNELS_PER_USER} дайджестов на аккаунт. "
        f"Если нужно больше — напиши @kelbic."
    )


@router.message(Command("channels"))
async def cmd_channels(message: Message) -> None:
    if message.from_user is None:
        return
    await send_digest_list(message, message.from_user.id, message.from_user.username)


@router.callback_query(F.data == "ob:list")
async def cb_digest_list(callback: CallbackQuery) -> None:
    """Кнопка «Мои дайджесты» (после подключения канала)."""
    await callback.answer()
    if callback.from_user is None or callback.message is None:
        return
    await send_digest_list(
        callback.message, callback.from_user.id, callback.from_user.username
    )


async def send_digest_list(
    message: Message, user_id: int, username: str | None = None
) -> None:
    """Список дайджестов юзера. message — куда отвечать (команда или
    сообщение под кнопкой), user_id — чей список (НЕ message.from_user:
    у сообщения под кнопкой автор — бот)."""
    async with session_maker()() as session:
        user = await get_or_create_user(session, user_id, username)
        channels = await get_user_channels(session, user.tg_user_id)

    if not channels:
        await message.answer(
            "У тебя пока нет дайджестов. Нажми кнопку и напиши тему — "
            "соберу первый.",
            reply_markup=create_digest_kb(),
        )
        return

    # Получаем last_post_at для каждого канала
    from datetime import datetime
    from sqlalchemy import select, func as sa_func
    from db.models import PostLog

    async with session_maker()() as session:
        last_posts_q = await session.execute(
            select(PostLog.target_id, sa_func.max(PostLog.posted_at))
            .group_by(PostLog.target_id)
        )
        last_posts = {row[0]: row[1] for row in last_posts_q.all()}

    lines = ["📰 <b>Твои дайджесты:</b>\n"]
    now = datetime.utcnow()
    for ch in channels:
        target_info = (
            f"📢 Канал: {html.escape(ch.target_chat_title or str(ch.target_chat_id))}"
            if ch.target_chat_id
            else "<i>⚠️ Telegram-канал не подключён — кнопка ниже</i>"
        )
        active = "✅" if ch.is_active else "⏸"

        # Последний пост
        last = last_posts.get(ch.id)
        from datetime import timedelta as _td

        # Подсчитаем отказы за последние 24ч для этого канала
        from db.models import RejectionLog
        from sqlalchemy import select as _sel, func as _func
        async with session_maker()() as _s:
            _cnt = await _s.execute(
                _sel(_func.count(RejectionLog.id))
                .where(
                    RejectionLog.channel_id == ch.id,
                    RejectionLog.rejected_at > now - _td(hours=24),
                )
            )
            rejections_count = int(_cnt.scalar_one() or 0)

        if last:
            delta = now - last
            if delta.total_seconds() < 3600:
                last_info = f"📤 Последний пост: {int(delta.total_seconds() // 60)} мин назад"
            elif delta.total_seconds() < 86400:
                last_info = f"📤 Последний пост: {int(delta.total_seconds() // 3600)}ч назад"
            else:
                days = delta.days
                last_info = f"⚠️ Последний пост: {days}д назад"
        elif ch.target_chat_id:
            since_create = now - ch.created_at
            if since_create > _td(hours=8):
                last_info = "⚠️ Постов нет более 8 часов"
            elif since_create > _td(hours=1) and rejections_count >= 3:
                last_info = (
                    f"⚠️ Цикл прошёл, но фильтр отсёк {rejections_count} твитов — "
                    f"тема может содержать политику/медицину. Попробуй другую."
                )
            elif since_create > _td(minutes=45):
                last_info = "⏳ Цикл сбора прошёл, твиты в очереди дайджеста"
            else:
                last_info = "⏳ Жду первого цикла сбора (до 30 мин)"
        else:
            last_info = ""

        # Если есть отказы — покажем общее число
        if rejections_count > 0 and ch.target_chat_id:
            last_info += f"\n  📋 Отказов фильтра за 24ч: {rejections_count}"

        images_status = "🖼" if ch.images_enabled else "📝"
        # filter_preset на отдельной строке чтоб не перегружать
        from prompts import get_filter_mode as _gp
        _preset = _gp(ch.filter_preset)
        filter_str = f"{_preset.emoji} {_preset.name}"
        if ch.filter_preset == "unfiltered":
            threshold_str = "виральность откл."
        else:
            threshold_str = f"лайков≥{ch.min_likes}, ретв≥{ch.min_retweets}"
        # Порог интереса ранкера — показываем только когда включён (0 = выкл)
        interest_str = ""
        if (ch.min_interest or 0) > 0:
            interest_str = (
                f"\n  🎯 Порог интереса: {ch.min_interest}/10 — публикуем только "
                f"твиты, которые AI-редактор оценил ≥{ch.min_interest} по теме канала"
            )
        lines.append(
            f"{active} <b>{html.escape(ch.title or '')}</b> (№{ch.id})\n"
            f"  Тема: {html.escape(ch.niche or '')} | Режим: {html.escape(_MODE_RU.get(ch.mode, ch.mode or ''))} | {images_status} | Фильтр: {filter_str}\n"
            f"  Источников: {len(ch.channel_sources)} | 📊 {threshold_str}{interest_str}\n"
            f"  {target_info}\n"
            f"  {last_info}"
        )
    lines.append(
        "\n<i>💡 AI-редактор отсекает оффтоп по теме дайджеста (берёт её из названия). "
        "Если фильтр пропускает не то — уточни тему: "
        "<code>/setdescription &lt;№&gt; про что дайджест</code>.</i>"
    )
    lines.append(
        "\n<b>Управление</b> (№ — номер дайджеста из списка):\n"
        "  /sources &lt;№&gt; — источники\n  /status &lt;№&gt; — детальная статистика\n"
        "  /addsource &lt;№&gt; @user — добавить источник\n"
        "  /removesource &lt;№&gt; @user — убрать источник\n"
        "  /scout &lt;№&gt; — AI-скаут: подобрать новые источники\n"
        "  /regenerate &lt;№&gt; — пересоздать источники\n  /setimages &lt;№&gt; on|off — картинки в постах\n"
        "  /setthreshold &lt;№&gt; likes=N retweets=N — порог виральности\n"
        "  /setminterest &lt;№&gt; 0-10 — порог интереса (AI-оценка темы)\n"
        "  /deletechannel &lt;№&gt; — удалить дайджест"
    )
    kb = None
    if any(ch.target_chat_id is None for ch in channels):
        lines.append(f"<i>{CONNECT_STEP}</i>")
        kb = await connect_channel_kb(message.bot)
    await message.answer("\n\n".join(lines), reply_markup=kb)


@router.message(Command("createchannel"))
async def cmd_createchannel(message: Message, command: CommandObject) -> None:
    if message.from_user is None:
        return
    if not command.args:
        await message.answer(CREATE_HELP, reply_markup=create_digest_kb())
        return

    parts = command.args.split(maxsplit=1)

    # - /createchannel ai <тема>      — явный AI (обратная совместимость)
    # - /createchannel <любой текст>  — AI по умолчанию
    # - /createchannel template <id>  — шаблонов больше нет → подсказка (лимит не тратит)
    first_word = parts[0].lower() if parts else ""
    if first_word == "template":
        await message.answer(CREATE_HELP, reply_markup=create_digest_kb())
        return
    if first_word == "ai":
        if len(parts) < 2:
            await message.answer(CREATE_HELP, reply_markup=create_digest_kb())
            return
        rest = parts[1].strip()
    else:
        # Любой текст после /createchannel = AI с этим описанием
        rest = command.args.strip()

    async with session_maker()() as session:
        # Регистрация юзера, если /createchannel пришёл раньше /start
        await get_or_create_user(
            session, message.from_user.id, message.from_user.username
        )

    await _create_with_ai(message, rest)


def _slot_status_line(channel) -> str:
    """Строка статуса слота для success-сообщений создания."""
    st = channel_status(channel)
    if st == "trial":
        return (f"🎁 <b>Триал {channel.trial_until:%d.%m} включён</b> — "
                f"7 дней бесплатно, дальше {PRICE_STARS}⭐/30 дней\n")
    if st == "inactive":
        return (f"💳 Дайджест создан <b>неактивным</b> — активируй за "
                f"{PRICE_STARS}⭐ командой /upgrade\n")
    return ""  # admin/paid — без строки


@router.message(Command("deletechannel"))
async def cmd_deletechannel(message: Message, command: CommandObject) -> None:
    if message.from_user is None or not command.args:
        await message.answer(
            "Использование: <code>/deletechannel &lt;№ дайджеста&gt;</code>\n"
            "Номера — в /channels"
        )
        return
    try:
        channel_id = int(command.args.strip())
    except ValueError:
        await message.answer("❌ Номер дайджеста должен быть числом.")
        return

    async with session_maker()() as session:
        ok = await delete_channel(session, channel_id, message.from_user.id)
    if ok:
        await message.answer(
            f"🗑 Дайджест №{channel_id} удалён. Твой Telegram-канал не тронут."
        )
    else:
        await message.answer(f"⚠️ Дайджест №{channel_id} не найден или не твой.")


def _sources_summary(found: int, validated: int, connected: int) -> str:
    """Честная строка воронки источников. Если подключённых больше прошедших
    проверку — был fail-open добор (узкая тема), и мы это прямо говорим."""
    base = f"ℹ️ Найдено в X: {found}, прошли проверку по твитам и теме: {validated}"
    if connected > validated:
        return (base + f"\nПодключено {connected} "
                f"(добрали {connected - validated} кандидатов — тема узкая, "
                f"мало кто прошёл проверку)\n")
    return base + f", подключено: {connected}\n"


async def _create_with_ai(
    message: Message, topic_description: str, user_id: int | None = None
) -> None:
    """AI-генерация через Twitter Search (не через угадывание имён).

    Pipeline:
    1. LLM даёт 4 поисковых ключевика для темы
    2. Twitter Search находит реальные аккаунты по каждому ключевику
    3. Дедупликация + ранжирование по followers
    4. LLM выбирает самых релевантных из кандидатов
    5. Создаём канал с реальными аккаунтами
    """
    uid = user_id if user_id is not None else (
        message.from_user.id if message.from_user else None
    )
    if uid is None:
        return
    # Проверки ввода и лимитов — ДО дорогой работы и без учёта в лимите:
    # лимит тратит только успешно созданный дайджест.
    if len(topic_description) < 10:
        await message.answer(
            "❌ Слишком короткое описание темы — нужно хотя бы 10 символов.\n"
            "Например: <i>«крикет, премьер-лига Индии»</i>"
        )
        return
    is_admin = uid == _cfg.admin_user_id
    if not is_admin:
        async with session_maker()() as session:
            if len(await get_user_channels(session, uid)) >= MAX_CHANNELS_PER_USER:
                await message.answer(_max_digests_text())
                return
        wait = CREATE_DIGEST_LIMIT.retry_after(uid)
        if wait:
            await message.answer(
                f"⏳ Можно создавать не больше {CREATE_DIGEST_LIMIT.max_calls} "
                f"дайджестов за {CREATE_DIGEST_LIMIT.window_sec // 60} мин. "
                f"Попробуй через {wait} сек."
            )
            return
    if uid in _creating:
        await message.answer("⏳ Уже собираю дайджест — подожди минуту, пока закончу.")
        return

    _creating.add(uid)
    try:
        await _create_with_ai_pipeline(message, topic_description, uid)
    finally:
        _creating.discard(uid)


async def _create_with_ai_pipeline(
    message: Message, topic_description: str, uid: int
) -> bool:
    """Сам подбор и создание. True — дайджест создан.

    Лимит учитывается сразу после записи дайджеста в БД — даже если потом
    не удастся отрисовать итоговое сообщение, создание уже состоялось."""
    topic_html = html.escape(topic_description)
    status_msg = await message.answer(
        f"🤖 Подбираю источники по теме: <i>{topic_html}</i>\n"
        f"Шаг 1/4: генерирую поисковые запросы..."
    )

    # === Шаг 1: Multi-shot keyword generation через Sonnet ===
    # Делаем 3 параллельных запроса с разными температурами для лучшего покрытия
    import asyncio as _asyncio
    query_tasks = [
        _llm_smart.suggest_search_queries(topic_description, count=5, temperature=0.3),
        _llm_smart.suggest_search_queries(topic_description, count=5, temperature=0.7),
        _llm_smart.suggest_search_queries(topic_description, count=5, temperature=1.0),
    ]
    query_results = await _asyncio.gather(*query_tasks, return_exceptions=True)

    # Объединяем все keywords из всех попыток, дедупим
    queries: list[str] = []
    seen = set()
    for r in query_results:
        if isinstance(r, list):
            for q in r:
                q_lower = q.lower().strip()
                if q_lower not in seen:
                    seen.add(q_lower)
                    queries.append(q)

    if not queries:
        await status_msg.edit_text(
            "⚠️ Не удалось подобрать поисковые запросы. Попробуй "
            "переформулировать тему — например, шире или по-английски."
        )
        return False

    queries_display = ", ".join(f"<code>{html.escape(q)}</code>" for q in queries[:4])
    await status_msg.edit_text(
        f"🤖 Подбираю источники по теме: <i>{topic_html}</i>\n"
        f"Шаг 2/4: ищу реальные аккаунты в X по запросам:\n{queries_display}"
    )

    # === Шаг 2: Twitter Search по каждому ключевику ===
    all_candidates: dict[str, dict] = {}  # screen_name -> user dict
    for query in queries[:10]:
        users = await _twitter.search_users(query, limit=15)
        for u in users:
            sn = u["screen_name"].lower()
            # Дедуп: если уже видели — сохраняем тот что с большим followers
            if sn in all_candidates:
                if u["followers_count"] > all_candidates[sn]["followers_count"]:
                    all_candidates[sn] = u
            else:
                all_candidates[sn] = u

    # Фильтруем низкокачественных: меньше 500 фоловеров или меньше 50 твитов
    MIN_FOLLOWERS = 1000
    MIN_TWEETS = 0  # disabled — bigger accounts can be tweet-light
    filtered = [
        u for u in all_candidates.values()
        if u["followers_count"] >= MIN_FOLLOWERS
        # statuses_count check disabled
    ]

    if len(filtered) < 3:
        await status_msg.edit_text(
            f"⚠️ Для темы «{topic_html}» в X найдено только {len(filtered)} "
            f"активных аккаунтов с достаточной аудиторией.\n\n"
            f"Похоже, тема <b>локальная или плохо представлена в X</b> "
            f"(например, региональные новости РФ, узкие хобби, русскоязычные ниши).\n\n"
            f"<b>Что попробовать:</b>\n\n"
            f"1️⃣ Переформулировать на английском (manicure вместо маникюр)\n"
            f"2️⃣ Сделать тему шире (beauty industry вместо nail care)\n"
            f"3️⃣ <b>Использовать Telegram-каналы как источники</b> — "
            f"напиши /tg_help для деталей"
        )
        return False

    # Сортируем по followers desc, берём топ-20 для ранжирования LLM
    filtered.sort(key=lambda u: u["followers_count"], reverse=True)
    top_candidates = filtered[:30]

    await status_msg.edit_text(
        f"🤖 Подбираю источники по теме: <i>{topic_html}</i>\n"
        f"Шаг 3/4: нашёл {len(filtered)} кандидатов, выбираю самых релевантных..."
    )

    # === Шаг 3: LLM выбирает самых релевантных из реальных кандидатов ===
    selected = await _llm_rank_candidates(topic_description, top_candidates)
    if not selected or len(selected) < 3:
        # fallback: берём топ-12 просто по followers
        selected = [
            {
                "username": c["screen_name"],
                "reason": f"{c['followers_count']:,} подписчиков. {c['description'][:120]}",
            }
            for c in top_candidates[:12]
        ]

    # === Шаг 4: превалидация по реальным твитам (общий код с /scout) ===
    # Отбор выше шёл по followers и bio — сюда проскакивают медиа-аккаунты
    # без текста и авторы, пишущие раз в месяц. Проверяем каждого по его
    # последним твитам. Пороги engagement = 0: канал ещё не создан,
    # гейты — доля текста, активность (< 21 дня тишины), частота;
    # сортировка — по ожидаемой отдаче (постов/нед).
    await status_msg.edit_text(
        f"🤖 Подбираю источники по теме: <i>{topic_html}</i>\n"
        f"Шаг 4/4: проверяю {len(selected)} кандидатов по их реальным твитам..."
    )
    pairs = [
        ((s["username"] or "").lstrip("@").strip().lower(), s.get("reason", ""))
        for s in selected
    ]
    pairs = [(u, r) for u, r in pairs if u]
    try:
        validated = await prevalidate_candidates(
            pairs, min_likes=0, min_retweets=0, cache=_scout_cache
        )
        validated = await apply_topic_relevance(
            _llm, topic_description, validated
        )
        sources_list = [c.username for c in validated]
    except Exception:
        logger.exception("createchannel ai: prevalidation failed, fail-open")
        sources_list = []

    # Причины из LLM-отбора — по username, чтобы показать их у РЕАЛЬНО
    # подключённых источников (а не у кандидатов до проверки).
    reason_by_user = {
        (s.get("username") or "").lstrip("@").strip().lower(): s.get("reason", "")
        for s in selected
    }
    # Все прошедшие двойную проверку (твиты + тема) остаются — потолок 15.
    n_validated = len(sources_list)
    MAX_CREATION_SOURCES = 15
    sources_list = sources_list[:MAX_CREATION_SOURCES]
    # Fail-open: пустой канал недопустим. Если проверку прошло мало —
    # добираем непроверенными в LLM-порядке до разумного минимума.
    MIN_CREATION_SOURCES = 6
    if len(sources_list) < MIN_CREATION_SOURCES:
        for u, _r in pairs:
            if u not in sources_list:
                sources_list.append(u)
            if len(sources_list) >= MIN_CREATION_SOURCES:
                break

    # === Шаг 5: создаём канал ===
    title = topic_description[:80]

    async with session_maker()() as session:
        channel = await create_channel(
            session,
            user_id=uid,
            title=title,
            niche="general",
            template_id=None,
            description=topic_description,
            mode="hybrid",
            sources=sources_list,
        )
    if uid != _cfg.admin_user_id:
        CREATE_DIGEST_LIMIT.record(uid)

    # Если выбрано мало источников — узкая тема, предупредим
    narrow_topic_warning = ""
    if len(selected) < 8:
        narrow_topic_warning = (
            f"\n⚠️ <b>Внимание:</b> для этой темы найдено мало источников ({len(selected)}). "
            f"Возможно, тема слишком узкая — X может покрывать её слабо. "
            f"Позже можно подобрать ещё источники кнопкой «Подобрать ещё источники».\n"
        )

    head = [
        f"✅ <b>Дайджест «{html.escape(title)}» готов — источники найдены в X!</b>\n",
        f"{_slot_status_line(channel)}",
        _sources_summary(len(filtered), n_validated, len(sources_list)),
        narrow_topic_warning,
        f"📡 <b>Источники:</b>",
    ]
    src_lines = []
    for u in sources_list:
        reason = reason_by_user.get(u.lstrip("@").strip().lower(), "").strip()
        if reason:
            src_lines.append(f"  • @{html.escape(str(u))} — {html.escape(str(reason))}")
        else:
            src_lines.append(f"  • @{html.escape(str(u))}")

    lines = [
        "\n⚙️ Публикация: 4 подборки в день + до 5 самых обсуждаемых твитов сразу.\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
    ]

    # Канал уже ждёт дайджест (юзер сначала сделал бота админом) — подключаем сам
    from bot.handlers.forward import link_pending_channel, next_steps_kb
    linked = None
    try:
        linked = await link_pending_channel(message.bot, uid, channel.id)
    except Exception:
        logger.exception("createchannel: auto-link of pending channel failed")

    if linked is not None:
        _chat_id, chat_title = linked
        lines.append(
            f"✅ <b>Канал «{html.escape(chat_title or 'Без названия')}» подключён "
            f"к этому дайджесту!</b>\n\n"
            f"Первые посты появятся в течение получаса — после ближайшего сбора твитов."
        )
        kb = next_steps_kb(channel.id)
    else:
        lines.append(
            f"<b>Остался один шаг — подключить твой Telegram-канал, куда я буду "
            f"публиковать посты.</b>\n\n"
            f"{CONNECT_STEP}\n\n"
            f"<i>{CONNECT_FALLBACK}</i>"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [await connect_channel_button(message.bot)],
        ])
    text = "\n".join(head + src_lines + lines)
    if len(text) > 4000:  # лимит Telegram 4096: длинные пояснения LLM режем первыми
        src_lines = [f"  • @{html.escape(str(u))}" for u in sources_list]
        text = "\n".join(head + src_lines + lines)
    await status_msg.edit_text(text, reply_markup=kb)
    return True


async def _llm_rank_candidates(
    topic: str, candidates: list[dict]
) -> list[dict] | None:
    """LLM выбирает из реальных кандидатов самых релевантных теме.

    Возвращает список {username, reason}.
    """
    import json as _json

    # Формируем компактный список для LLM
    candidate_list = [
        {
            "username": c["screen_name"],
            "name": c["name"],
            "bio": c["description"][:200],
            "followers": c["followers_count"],
        }
        for c in candidates
    ]

    system = (
        "Ты помогаешь отобрать лучшие X-аккаунты для тематического канала. "
        "Тебе дан список РЕАЛЬНЫХ аккаунтов с описаниями и подписчиками. "
        "Выбери из них 8-12 самых релевантных теме канала и объясни выбор. "
        "Отвечай строго JSON-массивом, без преамбулы, без markdown. "
        "Формат: [{\"username\": \"...\", \"reason\": \"короткое объяснение\"}, ...]. "
        "Исключай личные/спам аккаунты, NSFW, fan-страницы, дубликаты по теме."
    )
    user = (
        f"Тема канала: {topic}\n\n"
        f"Кандидаты (уже существующие в X аккаунты):\n"
        f"{_json.dumps(candidate_list, ensure_ascii=False, indent=1)}\n\n"
        f"Выбери 8-12 лучших для этой темы, верни JSON."
    )

    result = await _llm_smart._call_with_retry(system, user, max_tokens=2000)
    if not result:
        return None

    clean = result.strip()
    if clean.startswith("```"):
        lines = clean.split("\n")
        clean = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])

    try:
        data = _json.loads(clean)
    except Exception:
        return None

    if not isinstance(data, list):
        return None

    valid = set(c["screen_name"].lower() for c in candidates)
    selected = []
    for item in data:
        if not isinstance(item, dict):
            continue
        uname = str(item.get("username", "")).lstrip("@").strip()
        if uname.lower() not in valid:
            continue  # LLM вернула имя которого не было — игнорим
        reason = str(item.get("reason", "")).strip()
        selected.append({"username": uname, "reason": reason})
    return selected
