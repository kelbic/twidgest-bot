"""Команды управления источниками каналов:
/sources <channel_id> — список
/addsource <channel_id> @user — добавить
/removesource <channel_id> @user — удалить
"""
from __future__ import annotations

import re

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy import select

from prompts import list_filter_modes as list_presets, get_filter_mode as get_preset, FILTER_MODES as PRESETS
from core.vk_client import VKClient as _VKClient
from config import Config as _VKConfig
_vk_cfg = _VKConfig()
_vk_client = _VKClient(_vk_cfg.vk_access_token) if _vk_cfg.vk_access_token else None
from config import Config
from core.twitter_client import TwitterClient
from db.models import Channel, ChannelSource, DigestQueueItem
from core.llm_client import OpenRouterClient
from db.repositories.users import get_or_create_user
from db.session import session_maker

router = Router(name="channel_sources")

_cfg = Config()
_twitter = TwitterClient(_cfg.twitter_api_key)
_llm_default = OpenRouterClient(_cfg.openrouter_api_key, _cfg.openrouter_model_default)
_llm_smart = OpenRouterClient(_cfg.openrouter_api_key, _cfg.openrouter_model_pro)


def _parse_args(text: str | None, expected: int) -> tuple | None:
    """Простой парсер: split + проверка количества."""
    if not text:
        return None
    parts = text.strip().split()
    if len(parts) < expected:
        return None
    return tuple(parts[:expected])


async def _get_user_channel(user_id: int, channel_id: int) -> Channel | None:
    """Возвращает канал юзера со всеми источниками или None."""
    from sqlalchemy.orm import selectinload
    async with session_maker()() as session:
        result = await session.execute(
            select(Channel)
            .where(Channel.id == channel_id, Channel.user_id == user_id)
            .options(selectinload(Channel.channel_sources))
        )
        return result.scalar_one_or_none()


@router.message(Command("sources"))
async def cmd_sources(message: Message, command: CommandObject) -> None:
    """Список источников канала."""
    if message.from_user is None:
        return
    if not command.args:
        await message.answer(
            "Использование: <code>/sources &lt;channel_id&gt;</code>\n\n"
            "ID каналов — в /channels"
        )
        return

    try:
        channel_id = int(command.args.strip().split()[0])
    except (ValueError, IndexError):
        await message.answer("❌ ID канала должен быть числом.")
        return

    channel = await _get_user_channel(message.from_user.id, channel_id)
    if channel is None:
        await message.answer(f"⚠️ Канал {channel_id} не найден или не твой.")
        return

    if not channel.channel_sources:
        await message.answer(
            f"<b>{channel.title}</b> (id={channel_id})\n\n"
            f"Источников нет.\n\n"
            f"Добавить: <code>/addsource {channel_id} @user</code> или <code>vk:domain</code>"
        )
        return

    lines = [f"📡 <b>Источники канала «{channel.title}»</b> (id={channel_id})\n"]
    for i, src in enumerate(channel.channel_sources, 1):
        active = "✅" if src.is_active else "⏸"
        prefix = "" if src.twitter_username.startswith("vk:") else "@"
        lines.append(f"  {i}. {active} {prefix}{src.twitter_username}")

    lines.append(
        f"\n<b>Команды:</b>\n"
        f"  Добавить: <code>/addsource {channel_id} @user</code> или <code>vk:domain</code>\n"
        f"  Удалить: <code>/removesource {channel_id} @username</code>"
    )
    await message.answer("\n".join(lines))


@router.message(Command("addsource"))
async def cmd_addsource(message: Message, command: CommandObject) -> None:
    """Добавить источник в канал. Поддерживает Twitter (@user) и VK (vk:domain)."""
    if message.from_user is None:
        return
    args = _parse_args(command.args, 2)
    if not args:
        await message.answer(
            "Использование:\n"
            "  Twitter: <code>/addsource &lt;channel_id&gt; @username</code>\n"
            "  VK:      <code>/addsource &lt;channel_id&gt; vk:domain</code>\n\n"
            "Примеры:\n"
            "  <code>/addsource 5 @hubermanlab</code>\n"
            "  <code>/addsource 5 vk:lentaru</code>"
        )
        return

    try:
        channel_id = int(args[0])
    except ValueError:
        await message.answer("❌ ID канала должен быть числом.")
        return

    raw_source = args[1].strip()
    is_vk = raw_source.lower().startswith("vk:")

    channel = await _get_user_channel(message.from_user.id, channel_id)
    if channel is None:
        await message.answer(f"⚠️ Канал {channel_id} не найден или не твой.")
        return

    if is_vk:
        # === VK источник ===
        from core.vk_client import VKClient
        identifier = _VKClient.parse_identifier(raw_source)
        if not identifier:
            await message.answer("❌ Некорректный VK идентификатор.\nПример: <code>vk:lentaru</code>")
            return

        stored_id = f"vk:{identifier}"
        existing = {s.twitter_username.lower() for s in channel.channel_sources}
        if stored_id in existing:
            await message.answer(f"⚠️ {stored_id} уже добавлен в канал.")
            return

        if not _vk_client:
            await message.answer("❌ VK интеграция не настроена (нет VK_ACCESS_TOKEN).")
            return

        status_msg = await message.answer(f"🔍 Проверяю VK сообщество <code>{identifier}</code>...")
        community = await _vk_client.validate_community(identifier)
        if not community:
            await status_msg.edit_text(
                f"❌ VK сообщество <code>{identifier}</code> не найдено или закрытое."
            )
            return
        if community.is_closed != 0:
            await status_msg.edit_text(
                f"❌ Сообщество <b>{community.name}</b> закрытое — бот не может читать посты."
            )
            return

        async with session_maker()() as session:
            session.add(ChannelSource(
                channel_id=channel_id,
                twitter_username=stored_id,
                source_type="vk",
                is_active=True,
            ))
            await session.commit()

        await status_msg.edit_text(
            f"✅ VK источник <b>{community.name}</b> (<code>{stored_id}</code>) "
            f"добавлен в канал <b>{channel.title}</b>.\n\n"
            f"👥 Подписчиков: {community.members_count:,}\n"
            f"Бот начнёт собирать посты в следующем цикле (до 30 мин)."
        )

    else:
        # === Twitter источник ===
        username = raw_source.lstrip("@").strip()
        if not re.fullmatch(r"[A-Za-z0-9_]{1,32}", username):
            await message.answer(
                "❌ Некорректный username. Только латиница, цифры, _ (до 32 символов)."
            )
            return

        existing = {s.twitter_username.lower() for s in channel.channel_sources}
        if username.lower() in existing:
            await message.answer(f"⚠️ @{username} уже добавлен в канал.")
            return

        status_msg = await message.answer(f"🔍 Проверяю что @{username} существует в X...")
        validation = await _twitter.validate_usernames([username])
        is_alive = validation.get(username.lower(), False) or validation.get(username, False)

        if not is_alive:
            await status_msg.edit_text(
                f"❌ Аккаунт @{username} не найден в X или не публикует твиты.\n\n"
                f"Проверь правильность написания имени."
            )
            return

        async with session_maker()() as session:
            session.add(ChannelSource(
                channel_id=channel_id,
                twitter_username=username,
                source_type="twitter",
                is_active=True,
            ))
            await session.commit()

        await status_msg.edit_text(
            f"✅ Источник <b>@{username}</b> добавлен в канал <b>{channel.title}</b>.\n\n"
            f"Бот начнёт собирать твиты с него в следующем цикле (до 30 мин)."
        )


@router.message(Command("removesource"))
async def cmd_removesource(message: Message, command: CommandObject) -> None:
    """Удалить источник из канала."""
    if message.from_user is None:
        return
    args = _parse_args(command.args, 2)
    if not args:
        await message.answer(
            "Использование: <code>/removesource &lt;channel_id&gt; @username</code>"
        )
        return

    try:
        channel_id = int(args[0])
    except ValueError:
        await message.answer("❌ ID канала должен быть числом.")
        return

    username = args[1].lstrip("@").strip()

    channel = await _get_user_channel(message.from_user.id, channel_id)
    if channel is None:
        await message.answer(f"⚠️ Канал {channel_id} не найден или не твой.")
        return

    # Найдём источник
    target_source = None
    for src in channel.channel_sources:
        if src.twitter_username.lower() == username.lower():
            target_source = src
            break

    if target_source is None:
        await message.answer(
            f"⚠️ Источник @{username} не найден в этом канале.\n\n"
            f"Список: <code>/sources {channel_id}</code>"
        )
        return

    async with session_maker()() as session:
        from sqlalchemy import delete as sa_delete
        from db.models import DigestQueueItem

        # Удаляем источник
        await session.execute(
            sa_delete(ChannelSource).where(ChannelSource.id == target_source.id)
        )
        # Удаляем из digest_queue все твиты этого автора в этом канале
        deleted_q = await session.execute(
            sa_delete(DigestQueueItem).where(
                DigestQueueItem.channel_id == channel_id,
                DigestQueueItem.twitter_username == username,
            )
        )
        await session.commit()

    remaining = len(channel.channel_sources) - 1
    cleaned = deleted_q.rowcount or 0
    cleanup_note = f"\n🧹 Также удалено {cleaned} ожидающих твитов из очереди." if cleaned > 0 else ""
    await message.answer(
        f"🗑 Источник <b>@{username}</b> удалён из канала <b>{channel.title}</b>.\n\n"
        f"Осталось источников: {remaining}{cleanup_note}"
    )


@router.message(Command("regenerate"))
async def cmd_regenerate(message: Message, command: CommandObject) -> None:
    """Перегенерация источников канала через AI."""
    if message.from_user is None:
        return
    if not command.args:
        await message.answer(
            "Использование: <code>/regenerate &lt;channel_id&gt;</code>\n\n"
            "Полностью пересоздаёт список источников канала через AI-подбор. "
            "Тема канала остаётся прежней. Старые источники и накопленные "
            "твиты удаляются."
        )
        return

    try:
        channel_id = int(command.args.strip().split()[0])
    except (ValueError, IndexError):
        await message.answer("❌ ID канала должен быть числом.")
        return

    channel = await _get_user_channel(message.from_user.id, channel_id)
    if channel is None:
        await message.answer(f"⚠️ Канал {channel_id} не найден или не твой.")
        return

    # Сохраним старые источники чтобы исключить их при регенерации
    previous_sources = [s.twitter_username.lower() for s in channel.channel_sources]

    description = channel.description or channel.title
    if not description or len(description) < 10:
        await message.answer(
            f"❌ Невозможно перегенерировать — у канала нет описания темы. "
            f"Создай новый через /createchannel."
        )
        return

    status_msg = await message.answer(
        f"🔄 Перегенерирую источники для «{channel.title}»...\n"
        f"Тема: <i>{description}</i>\n"
        f"Шаг 1/3: генерирую новые поисковые запросы..."
    )

    # Step 1: Multi-shot keyword generation
    import asyncio as _asyncio
    query_tasks = [
        _llm_smart.suggest_search_queries(description, count=5, temperature=0.3),
        _llm_smart.suggest_search_queries(description, count=5, temperature=0.7),
        _llm_smart.suggest_search_queries(description, count=5, temperature=1.0),
    ]
    query_results = await _asyncio.gather(*query_tasks, return_exceptions=True)
    queries = []
    seen = set()
    for r in query_results:
        if isinstance(r, list):
            for q in r:
                ql = q.lower().strip()
                if ql not in seen:
                    seen.add(ql)
                    queries.append(q)

    if not queries:
        await status_msg.edit_text("⚠️ Не удалось сгенерировать запросы. Попробуй позже.")
        return

    queries_display = ", ".join(f"<code>{q}</code>" for q in queries[:6])
    await status_msg.edit_text(
        f"🔄 Перегенерирую источники для «{channel.title}»\n"
        f"Шаг 2/3: ищу аккаунты в X по {len(queries)} запросам:\n{queries_display}"
    )

    # Step 2: Twitter search для каждого keyword
    all_candidates: dict = {}
    for query in queries[:10]:
        users = await _twitter.search_users(query, limit=15)
        for u in users:
            sn = u["screen_name"].lower()
            if sn in all_candidates:
                if u["followers_count"] > all_candidates[sn]["followers_count"]:
                    all_candidates[sn] = u
            else:
                all_candidates[sn] = u

    MIN_FOLLOWERS = 1000
    # Сначала пробуем без старых
    filtered_new_only = [
        u for u in all_candidates.values()
        if u["followers_count"] >= MIN_FOLLOWERS
        and u["screen_name"].lower() not in previous_sources
    ]

    # Если новых мало — возвращаем всех (старых тоже, лучше чем ничего)
    if len(filtered_new_only) >= 6:
        filtered = filtered_new_only
        all_new = True
    else:
        filtered = [u for u in all_candidates.values() if u["followers_count"] >= MIN_FOLLOWERS]
        all_new = False

    if len(filtered) < 3:
        await status_msg.edit_text(
            f"⚠️ Найдено только {len(filtered)} активных аккаунтов с достаточной "
            f"аудиторией. Тема может быть слишком узкой для Twitter — попробуй:\n\n"
            f"1. Переформулировать на английском\n"
            f"2. Сделать тему шире\n"
            f"3. Использовать готовый шаблон: /templates\n\n"
            f"Источники не изменены."
        )
        return

    filtered.sort(key=lambda u: u["followers_count"], reverse=True)
    top_candidates = filtered[:30]

    await status_msg.edit_text(
        f"🔄 Перегенерирую источники для «{channel.title}»\n"
        f"Шаг 3/3: нашёл {len(filtered)} кандидатов, выбираю самых релевантных..."
    )

    # Step 3: LLM rank — переиспользуем _llm_rank_candidates из channels.py
    # Дублируем логику здесь чтобы не делать circular import
    import json as _json
    candidate_list = [
        {
            "username": c["screen_name"],
            "name": c["name"],
            "bio": c["description"][:200],
            "followers": c["followers_count"],
        }
        for c in top_candidates
    ]
    system = (
        "Ты помогаешь отобрать лучшие X-аккаунты для тематического канала. "
        "Тебе дан список РЕАЛЬНЫХ аккаунтов с описаниями и подписчиками. "
        "Выбери из них 8-12 самых релевантных теме канала и объясни выбор. "
        "Отвечай строго JSON-массивом, без преамбулы, без markdown. "
        "Формат: [{\"username\": \"...\", \"reason\": \"короткое объяснение\"}, ...]. "
        "Исключай личные/спам аккаунты, NSFW, fan-страницы, дубликаты по теме."
    )
    avoid_hint = ""
    if previous_sources:
        avoid_hint = (
            f"\n\nВАЖНО: Юзер ПРЕДЫДУЩИМ запросом получил эти источники: "
            f"{', '.join('@' + p for p in previous_sources[:15])}\n"
            f"Они оказались НЕРЕЛЕВАНТНЫМИ. Постарайся выбрать ДРУГИЕ источники "
            f"из списка кандидатов. Если из 8-12 выбранных хотя бы половина "
            f"новых — это хорошо. Если все кандидаты те же — выбери лучших, "
            f"но добавь объяснение в reason."
        )

    user_prompt = (
        f"Тема канала: {description}\n\n"
        f"Кандидаты:\n{_json.dumps(candidate_list, ensure_ascii=False, indent=1)}\n\n"
        f"Выбери 8-12 лучших, верни JSON.{avoid_hint}"
    )
    rank_result = await _llm_smart._call_with_retry(system, user_prompt, max_tokens=2000)

    selected = []
    if rank_result:
        clean = rank_result.strip()
        if clean.startswith("```"):
            lines = clean.split("\n")
            clean = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
        try:
            data = _json.loads(clean)
            valid_names = {c["screen_name"].lower() for c in top_candidates}
            for item in data if isinstance(data, list) else []:
                if not isinstance(item, dict):
                    continue
                uname = str(item.get("username", "")).lstrip("@").strip()
                if uname.lower() in valid_names:
                    selected.append({"username": uname, "reason": str(item.get("reason", ""))})
        except Exception:
            pass

    if not selected or len(selected) < 3:
        # Fallback: просто топ по followers
        selected = [
            {"username": c["screen_name"], "reason": f"{c['followers_count']:,} подписчиков"}
            for c in top_candidates[:10]
        ]

    # Step 4: Заменяем источники в БД
    from sqlalchemy import delete as sa_delete
    async with session_maker()() as session:
        # Удаляем старые источники
        await session.execute(
            sa_delete(ChannelSource).where(ChannelSource.channel_id == channel_id)
        )
        # Чистим digest_queue (старые твиты от удалённых источников)
        await session.execute(
            sa_delete(DigestQueueItem).where(DigestQueueItem.channel_id == channel_id)
        )
        # Добавляем новые
        for s in selected:
            session.add(ChannelSource(
                channel_id=channel_id,
                twitter_username=s["username"],
                is_active=True,
            ))
        await session.commit()

    # Show result
    lines = [
        f"✅ <b>Источники перегенерированы!</b>\n",
        f"📝 <b>{channel.title}</b> (id={channel_id})\n",
        f"📡 <b>Новые источники ({len(selected)}):</b>",
    ]
    for s in selected[:15]:
        reason = s.get("reason", "").strip()
        if reason:
            lines.append(f"  • @{s['username']} — {reason}")
        else:
            lines.append(f"  • @{s['username']}")

    lines.append(
        f"\n⚠️ Старые твиты в очереди удалены. "
        f"Бот начнёт собирать с новых источников в следующем цикле (до 30 мин)."
    )
    await status_msg.edit_text("\n".join(lines))


@router.message(Command("setimages"))
async def cmd_setimages(message: Message, command: CommandObject) -> None:
    """Включить/выключить картинки для канала."""
    if message.from_user is None:
        return
    args = _parse_args(command.args, 2)
    if not args:
        await message.answer(
            "Использование: <code>/setimages &lt;channel_id&gt; on|off</code>\n\n"
            "<code>on</code> — посты будут с картинками (Unsplash)\n"
            "<code>off</code> — только текст"
        )
        return

    try:
        channel_id = int(args[0])
    except ValueError:
        await message.answer("❌ ID канала должен быть числом.")
        return

    state = args[1].lower()
    if state not in ("on", "off", "true", "false", "1", "0"):
        await message.answer("❌ Используй: <code>on</code> или <code>off</code>")
        return

    enable = state in ("on", "true", "1")

    channel = await _get_user_channel(message.from_user.id, channel_id)
    if channel is None:
        await message.answer(f"⚠️ Канал {channel_id} не найден или не твой.")
        return

    from sqlalchemy import update as sa_update
    async with session_maker()() as session:
        await session.execute(
            sa_update(Channel)
            .where(Channel.id == channel_id)
            .values(images_enabled=enable)
        )
        await session.commit()

    state_emoji = "🖼" if enable else "📝"
    state_text = "включены" if enable else "отключены"
    await message.answer(
        f"{state_emoji} Картинки в канале «{channel.title}» <b>{state_text}</b>.\n\n"
        f"{'Посты будут идти с релевантными фото из Unsplash.' if enable else 'Посты будут только текстом.'}"
    )


@router.message(Command("status"))
async def cmd_status(message: Message, command: CommandObject) -> None:
    """Детальный статус канала: posts, queue, sources, last fetched tweets, skip reasons."""
    if message.from_user is None:
        return
    if not command.args:
        await message.answer(
            "Использование: <code>/status &lt;channel_id&gt;</code>"
        )
        return

    try:
        channel_id = int(command.args.strip().split()[0])
    except (ValueError, IndexError):
        await message.answer("❌ ID канала должен быть числом.")
        return

    channel = await _get_user_channel(message.from_user.id, channel_id)
    if channel is None:
        await message.answer(f"⚠️ Канал {channel_id} не найден или не твой.")
        return

    from datetime import datetime, timedelta
    from sqlalchemy import select, func as sa_func
    from db.models import (
        DigestQueueItem,
        DigestLog,
        PostLog,
        ProcessedTweet,
        RejectionLog,
    )

    now = datetime.utcnow()
    cutoff_24h = now - timedelta(hours=24)

    async with session_maker()() as session:
        # Посты за 24ч
        posts_q = await session.execute(
            select(PostLog.is_digest, sa_func.count(PostLog.id))
            .where(
                PostLog.target_id == channel_id,
                PostLog.posted_at > cutoff_24h,
            )
            .group_by(PostLog.is_digest)
        )
        posts_breakdown = dict(posts_q.all())
        single_count = posts_breakdown.get(False, 0) or posts_breakdown.get(0, 0)
        digest_count = posts_breakdown.get(True, 0) or posts_breakdown.get(1, 0)

        # Последний пост
        last_post_q = await session.execute(
            select(sa_func.max(PostLog.posted_at))
            .where(PostLog.target_id == channel_id)
        )
        last_post = last_post_q.scalar_one_or_none()

        # Последний дайджест
        last_digest_q = await session.execute(
            select(DigestLog.posted_at)
            .where(DigestLog.target_id == channel_id)
            .order_by(DigestLog.posted_at.desc())
            .limit(1)
        )
        last_digest = last_digest_q.scalar_one_or_none()

        # Отказы за 24ч + последний пример каждой причины
        rejections_q = await session.execute(
            select(RejectionLog.reason, sa_func.count(RejectionLog.id))
            .where(
                RejectionLog.channel_id == channel_id,
                RejectionLog.rejected_at > cutoff_24h,
            )
            .group_by(RejectionLog.reason)
        )
        rejections_by_reason = dict(rejections_q.all())
        total_rejections = sum(rejections_by_reason.values())

        # Последний обработанный твит на источник
        # Берём processed_tweets — самый свежий per twitter_username
        last_tweets_q = await session.execute(
            select(
                ProcessedTweet.twitter_username,
                sa_func.max(ProcessedTweet.processed_at),
            )
            .where(ProcessedTweet.user_id == channel.user_id)
            .group_by(ProcessedTweet.twitter_username)
        )
        last_tweet_by_source = dict(last_tweets_q.all())

        # Очередь дайджеста по источникам
        queue_q = await session.execute(
            select(
                DigestQueueItem.twitter_username,
                sa_func.count(DigestQueueItem.id),
            )
            .where(DigestQueueItem.channel_id == channel_id)
            .group_by(DigestQueueItem.twitter_username)
        )
        queue_by_source = dict(queue_q.all())
        total_in_queue = sum(queue_by_source.values())

        # Последний rejection per source — для понимания "что было отклонено"
        last_reject_q = await session.execute(
            select(
                RejectionLog.twitter_username,
                RejectionLog.reason,
                RejectionLog.rejected_at,
            )
            .where(
                RejectionLog.channel_id == channel_id,
                RejectionLog.rejected_at > cutoff_24h,
            )
            .order_by(RejectionLog.rejected_at.desc())
            .limit(50)
        )
        last_rejects = list(last_reject_q.all())
        # Группируем по source, берём самый свежий
        last_reject_by_source = {}
        for username, reason, ts in last_rejects:
            if username not in last_reject_by_source:
                last_reject_by_source[username] = (reason, ts)

        sources = list(channel.channel_sources)

    # ====== Формируем ответ ======
    parts = []

    title = f"📊 <b>Канал «{channel.title}»</b> (id={channel_id})"
    parts.append(title)

    images_str = "🖼 картинки on" if channel.images_enabled else "📝 без картинок"
    _preset = get_preset(channel.filter_preset)
    filter_str = f"{_preset.emoji} {_preset.name}"
    parts.append(
        f"🎯 Тема: <code>{channel.niche}</code> | "
        f"Режим: <code>{channel.mode}</code> | {images_str}"
    )
    parts.append(f"🎚 Фильтр: {filter_str} (<code>/setfilter {channel_id} ...</code>)")
    if channel.filter_preset == "unfiltered":
        parts.append(f"📊 Виральность: мин. лайков={channel.min_likes} (авто-0 для unfiltered)")
    else:
        parts.append(f"📊 Виральность: мин. лайков={channel.min_likes}, мин. ретвитов={channel.min_retweets}")

    target_str = channel.target_chat_title or (
        str(channel.target_chat_id) if channel.target_chat_id else "не привязан"
    )
    parts.append(f"📍 Куда постит: {target_str}")

    age = now - channel.created_at
    if age.days > 0:
        age_str = f"{age.days}д {age.seconds // 3600}ч назад"
    elif age.seconds > 3600:
        age_str = f"{age.seconds // 3600}ч назад"
    else:
        age_str = f"{age.seconds // 60}м назад"
    parts.append(
        f"⏱ Создан: {channel.created_at.strftime('%d.%m, %H:%M')} ({age_str})"
    )

    # === Активность за 24 часа ===
    parts.append("")
    parts.append("📈 <b>Активность за 24 часа</b>")
    total_posts = single_count + digest_count
    parts.append(
        f"  Опубликовано: <b>{total_posts}</b> "
        f"({single_count} single + {digest_count} digest)"
    )

    if last_post:
        delta = now - last_post
        if delta.total_seconds() < 3600:
            last_str = f"{int(delta.total_seconds() // 60)}м назад"
        elif delta.total_seconds() < 86400:
            last_str = f"{int(delta.total_seconds() // 3600)}ч назад"
        else:
            last_str = f"{delta.days}д назад"
        parts.append(f"  Последний пост: {last_str}")
    else:
        parts.append("  Последний пост: <i>пока не было</i>")

    if total_rejections > 0:
        rej_str = ", ".join(
            f"{cnt} <code>{reason}</code>"
            for reason, cnt in sorted(rejections_by_reason.items(), key=lambda x: -x[1])
        )
        parts.append(f"  Отказов фильтра: <b>{total_rejections}</b> ({rej_str})")

    # === Источники с детализацией ===
    parts.append("")
    parts.append(f"📡 <b>Источники ({len(sources)})</b>")

    if not sources:
        parts.append("  <i>не настроены</i>")
    else:
        for src in sources:
            uname = src.twitter_username
            queue_count = queue_by_source.get(uname, 0)
            last_seen = last_tweet_by_source.get(uname)
            reject_info = last_reject_by_source.get(uname)

            # Иконка статуса
            if queue_count > 0:
                icon = "✅"
                status_text = f"{queue_count} в очереди"
            elif reject_info:
                icon = "⏸"
                reason, ts = reject_info
                status_text = f"отклонено ({reason})"
            elif last_seen:
                delta = now - last_seen
                if delta.total_seconds() < 3600:
                    seen_ago = f"{int(delta.total_seconds() // 60)}м"
                elif delta.total_seconds() < 86400:
                    seen_ago = f"{int(delta.total_seconds() // 3600)}ч"
                else:
                    seen_ago = f"{delta.days}д"
                icon = "👁"
                status_text = f"видели {seen_ago} назад"
            else:
                icon = "⚠️"
                status_text = "ни одного твита"

            src_prefix = "" if uname.startswith("vk:") else "@"
            parts.append(f"  {icon} {src_prefix}{uname} — {status_text}")

    # === Очередь и расписание ===
    parts.append("")
    parts.append(f"🗂 В очереди дайджеста: <b>{total_in_queue}</b>")

    if channel.mode in ("digest", "hybrid"):
        if last_digest:
            next_digest_at = last_digest + timedelta(hours=channel.digest_interval_hours)
            time_to_next = next_digest_at - now
            if time_to_next.total_seconds() > 0:
                hours_left = int(time_to_next.total_seconds() // 3600)
                mins_left = int((time_to_next.total_seconds() % 3600) // 60)
                if hours_left > 0:
                    parts.append(f"⏰ Следующий дайджест: ~через {hours_left}ч {mins_left}м")
                else:
                    parts.append(f"⏰ Следующий дайджест: ~через {mins_left}м")
            else:
                parts.append("⏰ Следующий дайджест: при ближайшем publisher cycle")
        else:
            parts.append("⏰ Первый дайджест: при ближайшем publisher cycle")

    # === Команды ===
    parts.append("")
    parts.append("🛠 <b>Команды:</b>")
    parts.append(f"  <code>/sources {channel_id}</code> — управление источниками")
    parts.append(f"  <code>/regenerate {channel_id}</code> — пересоздать через AI")
    images_cmd = "off" if channel.images_enabled else "on"
    parts.append(f"  <code>/setimages {channel_id} {images_cmd}</code> — переключить картинки")
    parts.append(f"  <code>/deletechannel {channel_id}</code> — удалить канал")

    # Используем настоящие переносы строк
    text = "\n".join(parts)
    await message.answer(text)


@router.message(Command("filters"))
async def cmd_filters(message: Message) -> None:
    """Список доступных пресетов фильтра ценности."""
    lines = ["🎚 <b>Доступные пресеты фильтра ценности:</b>\n"]
    for p in list_presets():
        lines.append(
            f"{p.emoji} <code>{p.code}</code> — <b>{p.name}</b>\n"
            f"  {p.description}"
        )
    lines.append("\n<i>Сменить пресет: /setfilter &lt;channel_id&gt; &lt;preset&gt;</i>")
    lines.append("<i>Например: /setfilter 5 community</i>")
    await message.answer("\n\n".join(lines))


@router.message(Command("setfilter"))
async def cmd_setfilter(message: Message, command: CommandObject) -> None:
    """Меняет filter_preset канала."""
    if message.from_user is None:
        return
    args = _parse_args(command.args, 2)
    if not args:
        await message.answer(
            "Использование: <code>/setfilter &lt;channel_id&gt; &lt;preset&gt;</code>\n\n"
            "Список пресетов: /filters"
        )
        return

    try:
        channel_id = int(args[0])
    except ValueError:
        await message.answer("❌ ID канала должен быть числом.")
        return

    preset_code = args[1].lower().strip()
    if preset_code not in PRESETS:
        valid = ", ".join(PRESETS.keys())
        await message.answer(
            f"❌ Пресет <code>{preset_code}</code> не найден.\n\n"
            f"Доступные: {valid}\n\n"
            f"Подробнее: /filters"
        )
        return

    channel = await _get_user_channel(message.from_user.id, channel_id)
    if channel is None:
        await message.answer(f"⚠️ Канал {channel_id} не найден или не твой.")
        return

    from sqlalchemy import update as sa_update
    async with session_maker()() as session:
        await session.execute(
            sa_update(Channel)
            .where(Channel.id == channel_id)
            .values(filter_preset=preset_code)
        )
        await session.commit()

    preset = get_preset(preset_code)
    await message.answer(
        f"{preset.emoji} Фильтр канала <b>«{channel.title}»</b> переключён на "
        f"<b>{preset.name}</b>.\n\n"
        f"<i>{preset.description}</i>\n\n"
        f"Изменения применятся к новым твитам в следующем цикле сбора."
    )
    if preset_code == "unfiltered":
        await message.answer(
            "⚠️ <b>Режим 'Без фильтра':</b> бот будет публиковать весь контент "
            "без проверки качества. Юридические ограничения (наркотики, дозировки, "
            "полит. риски) сохраняются. Вы используете этот режим на свой страх и риск."
        )

