"""Подключение Telegram-канала юзера к его дайджесту.

Основной путь — кнопка «Подключить Telegram-канал» (t.me/<бот>?startchannel):
юзер выбирает канал, Telegram делает бота админом и шлёт апдейт my_chat_member,
где from_user — тот, кто добавил бота. Запасной путь — переслать боту пост из
канала (форвард). Оба пути сходятся в offer_channel → link_channel, логика
привязки одна.

Решение по юзеру (только его собственные дайджесты, чужие не трогаем никогда):
  • канал уже привязан к его дайджесту → подтверждаем, дублей не плодим;
  • ровно один дайджест без канала → привязываем сразу;
  • несколько → кнопки выбора (lnk:<дайджест>:<чат>, владелец проверяется);
  • ни одного → запоминаем канал (db/repositories/channels: pending) и
    предлагаем создать дайджест; после создания он привяжется сам.
"""
from __future__ import annotations

import html
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    Chat,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select

from bot.handlers.onboarding import (
    CONNECT_FALLBACK,
    CREATE_BUTTON,
    bot_username,
    connect_channel_kb,
)
from db.models import Channel, User
from db.repositories.channels import (
    drop_pending_channel,
    get_channel,
    get_pending_channels,
    get_user_channels,
    remember_pending_channel,
    set_channel_target,
)
from db.repositories.users import get_or_create_user
from db.session import session_maker

logger = logging.getLogger(__name__)
router = Router(name="forward")

UNTITLED = "Без названия"


def _q(text: str | None) -> str:
    return html.escape(text or UNTITLED)


def next_steps_kb(digest_id: int) -> InlineKeyboardMarkup:
    """Что делать после привязки — только то, что в боте уже есть."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🔍 Подобрать ещё источники", callback_data=f"scoutrun:{digest_id}"
        )],
        [InlineKeyboardButton(text="📋 Мои дайджесты", callback_data="ob:list")],
    ])


def linked_text(chat_title: str | None, digest_title: str | None,
                greeted: bool = True) -> str:
    text = (
        f"✅ Канал «{_q(chat_title)}» подключён к дайджесту «{_q(digest_title)}»!\n\n"
        f"Первые посты появятся в течение получаса — после ближайшего сбора "
        f"твитов. Подборки выходят по расписанию, самое обсуждаемое — сразу."
    )
    if not greeted:
        text += (
            "\n\n⚠️ Не получилось опубликовать в канале приветствие — проверь, "
            "что у меня есть право публиковать посты."
        )
    return text


async def link_channel(
    bot: Bot, digest_id: int, chat_id: int, chat_title: str | None
) -> bool:
    """Единая привязка канала к дайджесту (те же поля, что всегда:
    Channel.target_chat_id/target_chat_title) + снятие ожидания канала +
    приветствие в канал. Владельца проверяет вызывающий.

    Возвращает True, если приветствие в канал опубликовано."""
    async with session_maker()() as session:
        digest = await get_channel(session, digest_id)
        owner_id = digest.user_id if digest is not None else None
        await set_channel_target(session, digest_id, chat_id, chat_title, activate=True)
        if owner_id is not None:
            await drop_pending_channel(session, chat_id, user_id=owner_id)
    logger.info("link: digest %s → chat %s («%s»)", digest_id, chat_id, chat_title)

    # Приветствие в канал — мгновенный сигнал, что бот живой и права есть
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=(
                "✨ <b>Канал активирован</b>\n\n"
                "Я буду публиковать сюда адаптированные посты из X на русском.\n"
                "Первая подборка появится в течение 6 часов. Самое обсуждаемое — "
                "ещё быстрее.\n\n"
                f"<i>Создано через</i> @{await bot_username(bot)}"
            ),
            disable_web_page_preview=True,
        )
        return True
    except Exception as exc:  # noqa: BLE001 — привязка уже сделана, приветствие best-effort
        logger.warning("link: greeting to chat %s failed: %s", chat_id, exc)
        return False


async def _dm(bot: Bot, user_id: int, text: str,
              kb: InlineKeyboardMarkup | None = None) -> None:
    try:
        await bot.send_message(
            chat_id=user_id, text=text, reply_markup=kb,
            disable_web_page_preview=True,
        )
    except Exception as exc:  # noqa: BLE001 — юзер мог заблокировать бота
        logger.info("dm to %s failed: %s", user_id, exc)


async def offer_channel(
    bot: Bot,
    user_id: int,
    chat_id: int,
    chat_title: str | None,
    *,
    quiet_if_linked: bool = False,
    resumed: frozenset[int] = frozenset(),
) -> str:
    """Юзер предъявил канал (добавил бота админом или переслал пост).

    resumed — дайджесты, с которых только что снята пауза потери доступа.
    Возвращает исход: unknown_user | already | linked | choose | pending."""
    async with session_maker()() as session:
        if await session.get(User, user_id) is None:
            logger.info("offer_channel: unknown user %s (chat %s) — skip", user_id, chat_id)
            return "unknown_user"
        digests = await get_user_channels(session, user_id)

        already = next((d for d in digests if d.target_chat_id == chat_id), None)
        if already is not None:
            await drop_pending_channel(session, chat_id, user_id=user_id)
            if already.id in resumed:
                await _dm(bot, user_id, (
                    f"✅ Права вернулись — дайджест «{_q(already.title)}» снова "
                    f"публикуется в канал «{_q(chat_title)}»."
                ), next_steps_kb(already.id))
            elif not already.is_active:
                # Форвард не доказывает прав: дайджест на паузе из-за потери доступа
                await _dm(bot, user_id, (
                    f"⏸ Канал «{_q(chat_title)}» подключён к дайджесту "
                    f"«{_q(already.title)}», но я потерял право публиковать в нём. "
                    f"Нажми кнопку и снова сделай меня админом канала."
                ), await connect_channel_kb(bot))
            elif not quiet_if_linked:
                await _dm(bot, user_id, (
                    f"✅ Канал «{_q(chat_title)}» уже подключён к дайджесту "
                    f"«{_q(already.title)}». Ничего делать не нужно."
                ), next_steps_kb(already.id))
            return "already"

        unbound = [d for d in digests if d.target_chat_id is None]
        if len(unbound) != 1:
            await remember_pending_channel(session, user_id, chat_id, chat_title)

    if len(unbound) == 1:
        digest = unbound[0]
        greeted = await link_channel(bot, digest.id, chat_id, chat_title)
        await _dm(bot, user_id, linked_text(chat_title, digest.title, greeted),
                  next_steps_kb(digest.id))
        return "linked"

    if unbound:
        rows = [
            [InlineKeyboardButton(
                text=f"📰 {(d.title or UNTITLED)[:48]}",
                callback_data=f"lnk:{d.id}:{chat_id}",
            )]
            for d in unbound[:8]
        ]
        await _dm(bot, user_id, (
            f"📢 Вижу канал «{_q(chat_title)}». Какой дайджест в нём публиковать?"
        ), InlineKeyboardMarkup(inline_keyboard=rows))
        return "choose"

    await _dm(bot, user_id, (
        f"📢 Вижу канал «{_q(chat_title)}».\n\n"
        f"Осталось создать дайджест для него: нажми кнопку и напиши тему "
        f"одним сообщением. Когда дайджест будет готов, я сам подключу его "
        f"к этому каналу."
    ), InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=CREATE_BUTTON, callback_data="qs:ai"),
    ]]))
    return "pending"


async def link_pending_channel(bot: Bot, user_id: int, digest_id: int) -> tuple[int, str | None] | None:
    """После создания дайджеста: если юзер уже добавил бота в канал и канал
    ждёт дайджест — привязываем самый свежий. Возвращает (chat_id, title)."""
    async with session_maker()() as session:
        pending = await get_pending_channels(session, user_id)
        if not pending:
            return None
        # Канал мог уже уйти к другому дайджесту юзера (защита от дублей):
        # такие строки ожидания просто снимаем
        taken = {d.target_chat_id for d in await get_user_channels(session, user_id)}
        for p in pending:
            if p.chat_id in taken:
                await drop_pending_channel(session, p.chat_id, user_id=user_id)
        row = next((p for p in pending if p.chat_id not in taken), None)
        if row is None:
            return None
        chat_id, title = row.chat_id, row.chat_title
    await link_channel(bot, digest_id, chat_id, title)
    return chat_id, title


# --------------------------------------------------------------------------- #
# my_chat_member: бота добавили админом / сняли права / удалили
# --------------------------------------------------------------------------- #


def _can_post(member) -> bool:
    return (
        getattr(member, "status", None) == "administrator"
        and bool(getattr(member, "can_post_messages", False))
    )


async def _pause_digests_for_chat(bot: Bot, chat_id: int, chat_title: str | None) -> list[int]:
    """Бот больше не может публиковать в канал: ставим на паузу привязанные
    дайджесты (как сделал бы safe_sender на следующей отправке) и коротко
    пишем владельцам. Возвращает id владельцев, которым написали."""
    async with session_maker()() as session:
        res = await session.execute(
            select(Channel).where(
                Channel.target_chat_id == chat_id,
                Channel.is_active == True,  # noqa: E712
            )
        )
        paused = list(res.scalars().all())
        for d in paused:
            d.is_active = False
        await session.commit()
    notified = []
    for d in paused:
        await _dm(bot, d.user_id, (
            f"⚠️ Я больше не могу публиковать в канал «{_q(chat_title)}», "
            f"поэтому дайджест «{_q(d.title)}» на паузе. Верни мне права админа "
            f"с публикацией постов — и он продолжит работу."
        ))
        notified.append(d.user_id)
    return notified


async def _resume_digests_for_chat(chat_id: int) -> frozenset[int]:
    """Права на публикацию снова есть — снимаем паузу потери доступа
    (is_active=False ставит только потеря доступа: safe_sender и хендлер ниже).
    Возвращает id возобновлённых дайджестов."""
    async with session_maker()() as session:
        res = await session.execute(
            select(Channel).where(
                Channel.target_chat_id == chat_id,
                Channel.is_active == False,  # noqa: E712
            )
        )
        resumed = list(res.scalars().all())
        for d in resumed:
            d.is_active = True
        await session.commit()
    return frozenset(d.id for d in resumed)


@router.my_chat_member()
async def on_my_chat_member(event: ChatMemberUpdated, bot: Bot) -> None:
    chat = event.chat
    if chat.type != "channel":
        return  # группы/лички — не наш сценарий подключения
    actor_id = event.from_user.id if event.from_user else None
    title = chat.title or UNTITLED
    new, old = event.new_chat_member, event.old_chat_member

    if _can_post(new):
        resumed = await _resume_digests_for_chat(chat.id)
        if actor_id is None:
            return
        # Правка прав у уже публикующего бота — не повод для сообщения,
        # если канал уже привязан
        await offer_channel(
            bot, actor_id, chat.id, title,
            quiet_if_linked=_can_post(old), resumed=resumed,
        )
        return

    # Права на публикацию пропали (сняли галку / разжаловали / удалили).
    # Ошибки отправки ЛС глушит _dm; исключения БД пусть видит лог aiogram.
    async with session_maker()() as session:
        await drop_pending_channel(session, chat.id)
        actor_known = (
            actor_id is not None and await session.get(User, actor_id) is not None
        )
    notified = await _pause_digests_for_chat(bot, chat.id, title)
    # Подсказка «включи право публиковать» — когда бота только что добавили
    # без этого права или его сняли; повторные правки прав её не дублируют
    just_lost_or_added = old.status != "administrator" or _can_post(old)
    if (new.status == "administrator" and actor_known and just_lost_or_added
            and actor_id not in notified):
        await _dm(bot, actor_id, (
            f"⚠️ Я в админах канала «{_q(title)}», но без права публиковать "
            f"посты. Включи его в настройках администратора — и я подключусь."
        ))


@router.callback_query(F.data.startswith("lnk:"))
async def cb_link_choice(callback: CallbackQuery, bot: Bot) -> None:
    """Выбор дайджеста для канала. Привязываем, только если дайджест — свой,
    а канал юзер действительно предъявлял (строка ожидания у ЭТОГО юзера)."""
    if callback.from_user is None or callback.data is None:
        return
    try:
        _, d_raw, c_raw = callback.data.split(":", 2)
        digest_id, chat_id = int(d_raw), int(c_raw)
    except ValueError:
        await callback.answer("Кнопка устарела", show_alert=True)
        return
    uid = callback.from_user.id

    async with session_maker()() as session:
        digest = await get_channel(session, digest_id, user_id=uid)
        if digest is None:
            await callback.answer("Дайджест не найден", show_alert=True)
            return
        if digest.target_chat_id == chat_id:
            await callback.answer("Уже подключён ✅")
            return
        if digest.target_chat_id is not None:
            await callback.answer(
                "Этот дайджест уже подключён к другому каналу", show_alert=True
            )
            return
        pending = next(
            (p for p in await get_pending_channels(session, uid) if p.chat_id == chat_id),
            None,
        )
        if pending is None:
            await callback.answer(
                "Кнопка устарела — подключи канал заново", show_alert=True
            )
            return
        chat_title = pending.chat_title

    greeted = await link_channel(bot, digest.id, chat_id, chat_title)
    await callback.answer("Готово ✅")
    text = linked_text(chat_title, digest.title, greeted)
    kb = next_steps_kb(digest.id)
    try:
        if callback.message is None:
            raise ValueError("no message")
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:  # noqa: BLE001 — старое сообщение не редактируется
        await _dm(bot, uid, text, kb)


# --------------------------------------------------------------------------- #
# Запасной путь: переслать боту пост из канала
# --------------------------------------------------------------------------- #


def _forwarded_chat(message: Message) -> Chat | None:
    origin = message.forward_origin
    if origin is not None:
        chat = getattr(origin, "chat", None) or getattr(origin, "sender_chat", None)
        if chat is not None:
            return chat
    return message.forward_from_chat


def _has_forwarded_chat(message: Message) -> bool:
    return _forwarded_chat(message) is not None


@router.message(F.chat.type == "private", _has_forwarded_chat)
async def handle_forwarded_from_channel(message: Message, bot: Bot) -> None:
    if message.from_user is None:
        return
    chat = _forwarded_chat(message)
    if chat is None:
        return

    if chat.type not in {"channel", "supergroup", "group"}:
        await message.answer(
            "⚠️ Я публикую только в каналы. Перешли мне сообщение из своего "
            "Telegram-канала.\n\n" + CONNECT_FALLBACK
        )
        return

    # Регистрируем юзера (форвард мог прийти раньше /start)
    async with session_maker()() as session:
        await get_or_create_user(
            session, message.from_user.id, message.from_user.username
        )
    await offer_channel(bot, message.from_user.id, chat.id, chat.title or UNTITLED)


# Ручная привязка для тех, кто знает chat_id (в /help не показывается).
# Command(...) сам срезает «@бот» в группах и не путает /bind с /bindXXX.
@router.message(Command("bind"))
async def cmd_bind(message: Message, command: CommandObject) -> None:
    """Ручная привязка: /bind <chat_id> <номер дайджеста>"""
    if message.from_user is None:
        return
    parts = (command.args or "").split()
    if len(parts) != 2:
        await message.answer(
            "Использование: <code>/bind &lt;chat_id&gt; &lt;номер дайджеста&gt;</code>"
        )
        return
    try:
        chat_id = int(parts[0])
        channel_id = int(parts[1])
    except ValueError:
        await message.answer("❌ chat_id и номер дайджеста должны быть числами.")
        return

    async with session_maker()() as session:
        all_channels = await get_user_channels(session, message.from_user.id)
        target_channel = next((c for c in all_channels if c.id == channel_id), None)
        if target_channel is None:
            await message.answer(f"⚠️ Дайджест №{channel_id} не найден или не твой.")
            return
        await set_channel_target(session, channel_id, chat_id, None)

    await message.answer(
        f"✅ Чат <code>{chat_id}</code> подключён к дайджесту "
        f"«{html.escape(target_channel.title or '')}»."
    )
