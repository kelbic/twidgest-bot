"""Команды для Channel: /channels, /createchannel, /templates."""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from config import Config
from core.llm_client import OpenRouterClient
from core.twitter_client import TwitterClient
from db.repositories.channels import (
    create_channel,
    delete_channel,
    get_user_channels,
)
from db.repositories.users import get_or_create_user, is_tier_active
from db.session import session_maker
from templates import TEMPLATES, get_template, list_templates
from tiers import get_limits

# Shared clients for AI-assisted channel creation
_cfg = Config()
# Default LLM (Haiku) для всех задач
_llm = OpenRouterClient(_cfg.openrouter_api_key, _cfg.openrouter_model_default)
# Smart LLM (Sonnet) для критичных задач: keyword generation, candidate ranking
# Используется реже, не критично по бюджету
_llm_smart = OpenRouterClient(_cfg.openrouter_api_key, _cfg.openrouter_model_pro)
_twitter = TwitterClient(_cfg.twitter_api_key)

router = Router(name="channels")


CREATE_HELP = """\
🎯 <b>Создание канала</b>

<b>Способ 1 — готовый темплейт:</b>
<code>/createchannel template &lt;id&gt;</code>
Пример: <code>/createchannel template ai-news</code>

Доступные темплейты: /templates

<b>Способ 2 — описание темы (AI-генерация):</b>
<code>/createchannel ai &lt;описание&gt;</code>
Пример: <code>/createchannel ai крикет, премьер-лига Индии</code>

<i>AI-режим проверяет каждый источник на существование и активность перед сохранением.</i>
"""


@router.message(Command("templates"))
async def cmd_templates(message: Message) -> None:
    """Список шаблонов с готовыми командами для тапа."""
    lines = ["📚 <b>Доступные шаблоны каналов:</b>\n"]
    for tpl in list_templates():
        # Каждая строка содержит готовую команду — Telegram её подсветит
        # как кликабельную, и при тапе она автоматически отправится
        lines.append(
            f"{tpl.emoji} <b>{tpl.name}</b> — {tpl.description}\n"
            f"   📡 Источников: {len(tpl.default_sources)}\n"
            f"   👉 /createchannel_{tpl.id.replace('-', '_')}"
        )
    lines.append(
        "\n<i>Тапни команду рядом с нужным шаблоном — канал создастся автоматически.</i>"
    )
    await message.answer("\n\n".join(lines))


# Обработчик команд /createchannel_<template_id>
@router.message(lambda m: m.text and m.text.startswith("/createchannel_"))
async def cmd_createchannel_shortcut(message: Message) -> None:
    """Хендлер для команд вида /createchannel_ai_news, /createchannel_longevity и т.д."""
    if message.from_user is None or message.text is None:
        return

    # /createchannel_ai_news → ai_news → ai-news
    raw = message.text.split()[0]  # игнорируем возможные аргументы
    template_slug = raw.replace("/createchannel_", "", 1)
    # В шаблонах id с дефисом, в команде с подчёркиванием — конвертируем
    template_id = template_slug.replace("_", "-")

    # Проверяем лимит каналов
    async with session_maker()() as session:
        user = await get_or_create_user(
            session, message.from_user.id, message.from_user.username
        )
        active = await is_tier_active(user)
        effective_tier = user.tier if active else "free"
        limits = get_limits(effective_tier)
        existing = await get_user_channels(session, user.tg_user_id)
        if len(existing) >= limits.max_targets:
            await message.answer(
                f"❌ Достигнут лимит каналов для тарифа <b>{limits.name}</b>: "
                f"{limits.max_targets}.\n/upgrade для увеличения."
            )
            return

    await _create_from_template(message, template_id)


# Удалили callback handler'ы — больше не нужны


async def callback_create_template(call) -> None:
    """Юзер нажал на кнопку темплейта в /templates — создаём канал."""
    if call.data is None or call.from_user is None or call.message is None:
        return
    template_id = call.data.split(":", 1)[1]

    await call.answer("Создаю канал...")

    user_id = call.from_user.id
    username = call.from_user.username
    chat_id_to_reply = call.message.chat.id

    async with session_maker()() as session:
        user = await get_or_create_user(session, user_id, username)
        active = await is_tier_active(user)
        effective_tier = user.tier if active else "free"
        limits = get_limits(effective_tier)
        existing = await get_user_channels(session, user.tg_user_id)
        if len(existing) >= limits.max_targets:
            await call.message.bot.send_message(
                chat_id=chat_id_to_reply,
                text=(
                    f"❌ Достигнут лимит каналов для тарифа <b>{limits.name}</b>: "
                    f"{limits.max_targets}.\n/upgrade для увеличения."
                ),
            )
            return

    # Найдём темплейт
    tpl = get_template(template_id)
    if tpl is None and template_id.isdigit():
        idx = int(template_id) - 1
        templates_list = list_templates()
        if 0 <= idx < len(templates_list):
            tpl = templates_list[idx]

    if tpl is None:
        await call.message.bot.send_message(
            chat_id=chat_id_to_reply,
            text=f"❌ Темплейт <code>{template_id}</code> не найден.",
        )
        return

    # Создаём канал
    async with session_maker()() as session:
        channel = await create_channel(
            session,
            user_id=user_id,
            title=tpl.name,
            niche=tpl.niche,
            template_id=tpl.id,
            description=tpl.description,
            mode="hybrid",  # для шаблонов тоже hybrid
            sources=tpl.default_sources,
        )

    sources_preview = ", ".join(f"@{s}" for s in tpl.default_sources[:5])
    if len(tpl.default_sources) > 5:
        sources_preview += f" и ещё {len(tpl.default_sources) - 5}"

    await call.message.bot.send_message(
        chat_id=chat_id_to_reply,
        text=(
            f"✅ <b>Канал создан!</b>\n\n"
            f"{tpl.emoji} <b>{tpl.name}</b> (id={channel.id})\n"
            f"📡 Источники ({len(tpl.default_sources)}): {sources_preview}\n"
            f"⚙️ Режим: hybrid (4 дайджеста + до 5 виральных постов в день)\n\n"
            f"<b>⚠️ Следующий шаг:</b>\n"
            f"1. Создай Telegram-канал\n"
            f"2. Добавь @TwidgestBot админом\n"
            f"3. Перешли мне любое сообщение из канала\n\n"
            f"Список твоих каналов: /channels"
        ),
    )


@router.message(Command("channels"))
async def cmd_channels(message: Message) -> None:
    if message.from_user is None:
        return
    async with session_maker()() as session:
        user = await get_or_create_user(
            session, message.from_user.id, message.from_user.username
        )
        channels = await get_user_channels(session, user.tg_user_id)

    if not channels:
        await message.answer(
            "У тебя пока нет каналов.\n\nСоздай первый: /createchannel"
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

    lines = ["📢 <b>Твои каналы:</b>\n"]
    now = datetime.utcnow()
    for ch in channels:
        target_info = (
            f"📍 {ch.target_chat_title or ch.target_chat_id}"
            if ch.target_chat_id
            else "<i>⚠️ канал не настроен — переслай мне сообщение из канала</i>"
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

        lines.append(
            f"{active} <b>{ch.title}</b> (id={ch.id})\n"
            f"  Тема: {ch.niche} | Режим: {ch.mode}\n"
            f"  Источников: {len(ch.channel_sources)}\n"
            f"  {target_info}\n"
            f"  {last_info}"
        )
    lines.append(
        "\n<b>Управление:</b>\n"
        "  /sources &lt;id&gt; — источники канала\n"
        "  /addsource &lt;id&gt; @user — добавить\n"
        "  /removesource &lt;id&gt; @user — удалить\n"
        "  /deletechannel &lt;id&gt; — удалить канал"
    )
    await message.answer("\n\n".join(lines))


@router.message(Command("createchannel"))
async def cmd_createchannel(message: Message, command: CommandObject) -> None:
    if message.from_user is None:
        return
    if not command.args:
        await message.answer(CREATE_HELP)
        return

    parts = command.args.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(CREATE_HELP)
        return

    method = parts[0].lower()
    rest = parts[1].strip()

    async with session_maker()() as session:
        user = await get_or_create_user(
            session, message.from_user.id, message.from_user.username
        )
        active = await is_tier_active(user)
        effective_tier = user.tier if active else "free"
        limits = get_limits(effective_tier)

        existing_channels = await get_user_channels(session, user.tg_user_id)
        if len(existing_channels) >= limits.max_targets:
            await message.answer(
                f"❌ Достигнут лимит каналов для тарифа <b>{limits.name}</b>: "
                f"{limits.max_targets}.\n/upgrade для увеличения."
            )
            return

    if method == "template":
        await _create_from_template(message, rest)
    elif method == "ai":
        await _create_with_ai(message, rest)
    else:
        await message.answer(CREATE_HELP)


async def _create_from_template(message: Message, template_id: str) -> None:
    if message.from_user is None:
        return
    template_id = template_id.strip().lower()
    tpl = get_template(template_id)

    # Если по id не найдено — может это число (позиция в списке)
    if tpl is None and template_id.isdigit():
        idx = int(template_id) - 1  # 1-indexed для юзера
        templates = list_templates()
        if 0 <= idx < len(templates):
            tpl = templates[idx]

    if tpl is None:
        valid = ", ".join(TEMPLATES.keys())
        await message.answer(
            f"❌ Темплейт <code>{template_id}</code> не найден.\n\n"
            f"Доступные: {valid}\n\n"
            f"Проще всего — нажать кнопку в /templates"
        )
        return

    async with session_maker()() as session:
        channel = await create_channel(
            session,
            user_id=message.from_user.id,
            title=tpl.name,
            niche=tpl.niche,
            template_id=tpl.id,
            description=tpl.description,
            mode="digest",
            sources=tpl.default_sources,
        )

    sources_preview = ", ".join(f"@{s}" for s in tpl.default_sources[:5])
    if len(tpl.default_sources) > 5:
        sources_preview += f" и ещё {len(tpl.default_sources) - 5}"

    await message.answer(
        f"✅ <b>Канал создан!</b>\n\n"
        f"{tpl.emoji} <b>{tpl.name}</b> (id={channel.id})\n"
        f"📡 Источники ({len(tpl.default_sources)}): {sources_preview}\n"
        f"⚙️ Режим: hybrid (4 дайджеста в день + до 5 виральных твитов сразу)\n\n"
        f"<b>⚠️ Следующий шаг:</b>\n"
        f"1. Создай Telegram-канал\n"
        f"2. Добавь @TwidgestBot админом с правом «Публикация сообщений»\n"
        f"3. Перешли мне любое сообщение из канала\n\n"
        f"Список твоих каналов: /channels"
    )


@router.message(Command("deletechannel"))
async def cmd_deletechannel(message: Message, command: CommandObject) -> None:
    if message.from_user is None or not command.args:
        await message.answer("Использование: <code>/deletechannel &lt;id&gt;</code>")
        return
    try:
        channel_id = int(command.args.strip())
    except ValueError:
        await message.answer("❌ id должен быть числом.")
        return

    async with session_maker()() as session:
        ok = await delete_channel(session, channel_id, message.from_user.id)
    if ok:
        await message.answer(f"🗑 Канал {channel_id} удалён.")
    else:
        await message.answer(f"⚠️ Канал {channel_id} не найден или не твой.")


async def _create_with_ai(message: Message, topic_description: str) -> None:
    """AI-генерация через Twitter Search (не через угадывание имён).

    Pipeline:
    1. LLM даёт 4 поисковых ключевика для темы
    2. Twitter Search находит реальные аккаунты по каждому ключевику
    3. Дедупликация + ранжирование по followers
    4. LLM выбирает самых релевантных из кандидатов
    5. Создаём канал с реальными аккаунтами
    """
    if message.from_user is None:
        return
    if len(topic_description) < 10:
        await message.answer(
            "❌ Слишком короткое описание. Минимум 10 символов.\n"
            "Пример: <code>/createchannel ai крикет, премьер-лига Индии</code>"
        )
        return

    status_msg = await message.answer(
        f"🤖 Подбираю источники по теме: <i>{topic_description}</i>\n"
        f"Шаг 1/3: генерирую поисковые запросы..."
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
            "⚠️ Не удалось сгенерировать поисковые запросы. Попробуй переформулировать.\n"
            "Или используй готовый темплейт: /templates"
        )
        return

    queries_display = ", ".join(f"<code>{q}</code>" for q in queries[:4])
    await status_msg.edit_text(
        f"🤖 Подбираю источники по теме: <i>{topic_description}</i>\n"
        f"Шаг 2/3: ищу реальные аккаунты в X по запросам:\n{queries_display}"
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
            f"⚠️ Для темы «{topic_description}» в X найдено только {len(filtered)} "
            f"активных аккаунтов с достаточной аудиторией.\n\n"
            f"Это узкая или плохо представленная в X ниша. Попробуй:\n"
            f"1️⃣ Переформулировать на английском (manicure вместо маникюр)\n"
            f"2️⃣ Сделать тему шире (beauty industry вместо nail care)\n"
            f"3️⃣ Использовать готовый темплейт: /templates"
        )
        return

    # Сортируем по followers desc, берём топ-20 для ранжирования LLM
    filtered.sort(key=lambda u: u["followers_count"], reverse=True)
    top_candidates = filtered[:30]

    await status_msg.edit_text(
        f"🤖 Подбираю источники по теме: <i>{topic_description}</i>\n"
        f"Шаг 3/3: нашёл {len(filtered)} кандидатов, выбираю самых релевантных..."
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

    # === Шаг 4: создаём канал ===
    sources_list = [s["username"] for s in selected]
    title = topic_description[:80]

    async with session_maker()() as session:
        channel = await create_channel(
            session,
            user_id=message.from_user.id,
            title=title,
            niche="general",
            template_id=None,
            description=topic_description,
            mode="hybrid",
            sources=sources_list,
        )

    # Если выбрано мало источников — узкая тема, предупредим
    narrow_topic_warning = ""
    if len(selected) < 8:
        narrow_topic_warning = (
            f"\n⚠️ <b>Внимание:</b> для этой темы найдено мало источников ({len(selected)}). "
            f"Возможно тема слишком узкая — Twitter может не покрывать её хорошо.\n"
            f"Можешь добавить свои источники командой "
            f"<code>/addsource {channel.id} @username</code>\n"
        )

    lines = [
        f"✅ <b>Канал создан с реальными источниками из X!</b>\n",
        f"📝 <b>{title}</b> (id={channel.id})\n",
        f"ℹ️ Найдено в X по запросам: {len(filtered)}, выбрано: {len(selected)}\n",
        narrow_topic_warning,
        f"📡 <b>Источники:</b>",
    ]
    for s in selected[:15]:
        reason = s.get("reason", "").strip()
        if reason:
            lines.append(f"  • @{s['username']} — {reason}")
        else:
            lines.append(f"  • @{s['username']}")

    lines.append(
        f"\n⚙️ Режим: hybrid (4 дайджеста в день + до 5 виральных твитов сразу)\n\n"
        f"<b>⚠️ Следующий шаг:</b>\n"
        f"1. Создай Telegram-канал\n"
        f"2. Добавь @TwidgestBot админом\n"
        f"3. Перешли мне любое сообщение из канала\n\n"
        f"Не нравится? /deletechannel {channel.id}"
    )
    await status_msg.edit_text("\n".join(lines))


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
