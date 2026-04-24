"""Команды для Channel: /channels, /createchannel, /deletechannel."""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from config import Config
from core.llm_client import OpenRouterClient
from db.repositories.channels import (
    create_channel,
    delete_channel,
    get_user_channels,
)
from db.repositories.users import get_or_create_user, is_tier_active
from db.session import session_maker
from templates import TEMPLATES, get_template, list_templates
from tiers import get_limits

# Используем default LLM для подбора источников (Haiku — дешевле и быстрее)
_cfg = Config()
_llm = OpenRouterClient(_cfg.openrouter_api_key, _cfg.openrouter_model_default)

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

<i>⚠️ AI-генерация будет добавлена в следующем обновлении. Пока используй темплейты.</i>
"""


@router.message(Command("templates"))
async def cmd_templates(message: Message) -> None:
    lines = ["📚 <b>Доступные темплейты каналов:</b>\n"]
    for tpl in list_templates():
        lines.append(
            f"{tpl.emoji} <code>{tpl.id}</code> — <b>{tpl.name}</b>\n"
            f"  {tpl.description}\n"
            f"  Источников: {len(tpl.default_sources)}"
        )
    lines.append("\n<i>Создать канал: /createchannel template &lt;id&gt;</i>")
    await message.answer("\n\n".join(lines))


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
            "У тебя пока нет каналов.\n\n"
            "Создай первый: /createchannel"
        )
        return

    lines = ["📢 <b>Твои каналы:</b>\n"]
    for ch in channels:
        target_info = (
            f"📍 {ch.target_chat_title or ch.target_chat_id}"
            if ch.target_chat_id
            else "<i>⚠️ канал не настроен — переслай мне сообщение из канала</i>"
        )
        active = "✅" if ch.is_active else "⏸"
        lines.append(
            f"{active} <b>{ch.title}</b> (id={ch.id})\n"
            f"  Тема: {ch.niche}\n"
            f"  Источников: {len(ch.channel_sources)}\n"
            f"  Режим: {ch.mode}\n"
            f"  {target_info}"
        )
    lines.append("\n<i>Удалить: /deletechannel &lt;id&gt;</i>")
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
    if tpl is None:
        valid = ", ".join(TEMPLATES.keys())
        await message.answer(
            f"❌ Темплейт <code>{template_id}</code> не найден.\n\n"
            f"Доступные: {valid}\n\n"
            f"Список с описанием: /templates"
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
        f"⚙️ Режим: digest, раз в 12 часов\n\n"
        f"<b>⚠️ Следующий шаг:</b>\n"
        f"1. Создай Telegram-канал (или используй существующий)\n"
        f"2. Добавь @TwidgestBot админом с правом «Публикация сообщений»\n"
        f"3. Перешли мне любое сообщение из канала\n\n"
        f"Я сам определю чат и привяжу его к каналу <b>{tpl.name}</b>.\n\n"
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
    if message.from_user is None:
        return
    if len(topic_description) < 10:
        await message.answer(
            "❌ Слишком короткое описание. Минимум 10 символов.\n"
            "Пример: <code>/createchannel ai крикет, премьер-лига Индии</code>"
        )
        return

    await message.answer(
        f"🤖 Подбираю источники по теме: <i>{topic_description}</i>\n"
        f"Это займёт 10-30 секунд..."
    )

    suggested = await _llm.suggest_sources(topic_description, count=12)
    if not suggested:
        await message.answer(
            "⚠️ Не удалось подобрать источники. Попробуй переформулировать тему "
            "или используй готовый темплейт: /templates"
        )
        return

    # Сохраним канал с этими источниками
    sources_list = [s["username"] for s in suggested]
    title = topic_description[:80]

    async with session_maker()() as session:
        channel = await create_channel(
            session,
            user_id=message.from_user.id,
            title=title,
            niche="general",  # для AI-генерированных — generic ниша
            template_id=None,
            description=topic_description,
            mode="digest",
            sources=sources_list,
        )

    # Показываем юзеру список с reason
    lines = [
        f"✅ <b>Канал создан с AI-подобранными источниками!</b>\n",
        f"📝 <b>{title}</b> (id={channel.id})\n",
        f"📡 <b>Источники ({len(suggested)}):</b>\n",
    ]
    for s in suggested:
        reason = s.get("reason", "").strip()
        if reason:
            lines.append(f"  • @{s['username']} — {reason}")
        else:
            lines.append(f"  • @{s['username']}")

    lines.append(
        f"\n⚙️ Режим: digest, раз в 12 часов\n\n"
        f"<b>⚠️ Следующий шаг:</b>\n"
        f"1. Создай Telegram-канал\n"
        f"2. Добавь @TwidgestBot админом с правом «Публикация сообщений»\n"
        f"3. Перешли мне любое сообщение из канала\n\n"
        f"<i>Не нравится список? Удали через /deletechannel {channel.id} "
        f"и попробуй другую формулировку.</i>"
    )
    await message.answer("\n".join(lines))
