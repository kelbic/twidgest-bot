"""Офлайн-стенд Telegram для тестов онбординга.

MockedBot — настоящий aiogram.Bot, у которого __call__ (единая точка ВСЕХ
вызовов Bot API: bot.send_message, message.answer, bot.me() …) перехвачен и
только записывает метод. Транспорт под ним — сессия, падающая на любом
запросе, а тесты дополнительно глушат сокеты (no_network). Реальный чат
недостижим ни при каком сценарии.

Апдейты прогоняются через тот же диспетчер, что строит main.py
(build_dispatcher), — со всеми фильтрами, middleware и порядком роутеров.
"""
from __future__ import annotations

import itertools
import socket
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.methods import EditMessageText, GetChatMember, GetMe, SendMessage
from aiogram.types import (
    CallbackQuery,
    Chat,
    ChatMemberAdministrator,
    ChatMemberBanned,
    ChatMemberLeft,
    ChatMemberMember,
    ChatMemberOwner,
    ChatMemberUpdated,
    Message,
    MessageOriginChannel,
    Update,
    User,
)

BOT_USERNAME = "TwidgestBot"
BOT_ID = 42
BOT_USER = User(id=BOT_ID, is_bot=True, first_name="Twidgest", username=BOT_USERNAME)

_ids = itertools.count(1)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class NoNetworkSession(BaseSession):
    """Транспорт-предохранитель: любой реальный запрос — падение теста."""

    async def make_request(self, bot, method, timeout=None):  # noqa: ANN001
        raise AssertionError(f"network call attempted: {type(method).__name__}")

    async def stream_content(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("network stream attempted")
        yield b""  # pragma: no cover

    async def close(self) -> None:
        return None


class MockedBot(Bot):
    def __init__(self) -> None:
        super().__init__(token=f"{BOT_ID}:TEST", session=NoNetworkSession())
        self.calls: list = []
        self.forbidden_chats: set[int] = set()
        # (chat_id, user_id) → "creator" | "administrator" | "member" | Exception.
        # Не задано → TelegramBadRequest «chat not found» (бота нет в чате).
        self.chat_members: dict[tuple[int, int], object] = {}

    async def __call__(self, method, request_timeout=None):  # noqa: ANN001
        self.calls.append(method)
        if isinstance(method, GetMe):
            return BOT_USER
        if isinstance(method, GetChatMember):
            key = (int(method.chat_id), int(method.user_id))
            status = self.chat_members.get(
                key, TelegramBadRequest(method=method, message="Bad Request: chat not found")
            )
            if isinstance(status, Exception):
                raise status
            return chat_member(int(method.user_id), status)
        if isinstance(method, (SendMessage, EditMessageText)):
            chat_id = int(method.chat_id)
            if chat_id in self.forbidden_chats:
                raise TelegramForbiddenError(method=method, message="Forbidden: no rights")
            return Message(
                message_id=getattr(method, "message_id", None) or next(_ids),
                date=_now(),
                chat=Chat(id=chat_id, type="private" if chat_id > 0 else "channel"),
                from_user=BOT_USER,
                text=method.text,
                reply_markup=method.reply_markup,
            ).as_(self)
        return True

    # ── удобства для проверок ──
    def texts(self, chat_id: int | None = None) -> list[str]:
        """Тексты отправленных/отредактированных сообщений (опц. — в один чат)."""
        return [
            m.text for m in self.calls
            if isinstance(m, (SendMessage, EditMessageText))
            and (chat_id is None or int(m.chat_id) == chat_id)
        ]

    def messages(self, chat_id: int | None = None) -> list:
        return [
            m for m in self.calls
            if isinstance(m, (SendMessage, EditMessageText))
            and (chat_id is None or int(m.chat_id) == chat_id)
        ]

    def api_calls(self) -> list:
        """Всё, кроме getMe (кеш username)."""
        return [m for m in self.calls if not isinstance(m, GetMe)]


def buttons(method) -> list:
    kb = getattr(method, "reply_markup", None)
    if kb is None or not hasattr(kb, "inline_keyboard"):
        return []
    return [b for row in kb.inline_keyboard for b in row]


def tg_user(user_id: int) -> User:
    return User(id=user_id, is_bot=False, first_name=f"U{user_id}", username=f"u{user_id}")


def msg_update(
    text: str,
    *,
    user_id: int,
    chat_type: str = "private",
    chat_id: int | None = None,
    forward_from: Chat | None = None,
) -> Update:
    if chat_type == "private":
        chat = Chat(id=user_id, type="private")
    else:
        chat = Chat(id=chat_id or -1009990001, type=chat_type, title="Чат BugTest")
    extra = {}
    if forward_from is not None:
        extra["forward_origin"] = MessageOriginChannel(
            date=_now(), chat=forward_from, message_id=7
        )
    return Update(update_id=next(_ids), message=Message(
        message_id=next(_ids), date=_now(), chat=chat,
        from_user=tg_user(user_id), text=text, **extra,
    ))


def cb_update(data: str, *, user_id: int) -> Update:
    card = Message(
        message_id=next(_ids), date=_now(), chat=Chat(id=user_id, type="private"),
        from_user=BOT_USER, text="карточка",
    )
    return Update(update_id=next(_ids), callback_query=CallbackQuery(
        id=str(next(_ids)), from_user=tg_user(user_id), chat_instance="ci",
        data=data, message=card,
    ))


def bot_admin(can_post: bool = True) -> ChatMemberAdministrator:
    return ChatMemberAdministrator(
        user=BOT_USER, can_be_edited=False, is_anonymous=False,
        can_manage_chat=True, can_delete_messages=False,
        can_manage_video_chats=False, can_restrict_members=False,
        can_promote_members=False, can_change_info=False,
        can_invite_users=False, can_post_stories=False,
        can_edit_stories=False, can_delete_stories=False,
        can_post_messages=can_post,
    )


def chat_member(user_id: int, status: str):
    user = tg_user(user_id)
    if status == "creator":
        return ChatMemberOwner(user=user, is_anonymous=False)
    if status == "administrator":
        return ChatMemberAdministrator(
            user=user, can_be_edited=False, is_anonymous=False,
            can_manage_chat=True, can_delete_messages=False,
            can_manage_video_chats=False, can_restrict_members=False,
            can_promote_members=False, can_change_info=False,
            can_invite_users=False, can_post_stories=False,
            can_edit_stories=False, can_delete_stories=False,
            can_post_messages=True,
        )
    return ChatMemberMember(user=user)


def bot_left() -> ChatMemberLeft:
    return ChatMemberLeft(user=BOT_USER)


def bot_kicked() -> ChatMemberBanned:
    return ChatMemberBanned(user=BOT_USER, until_date=_now())


def my_chat_member_update(
    *, chat_id: int, title: str, actor_id: int, old, new, chat_type: str = "channel",
) -> Update:
    return Update(update_id=next(_ids), my_chat_member=ChatMemberUpdated(
        chat=Chat(id=chat_id, type=chat_type, title=title),
        from_user=tg_user(actor_id), date=_now(),
        old_chat_member=old, new_chat_member=new,
    ))


_DP = None


def get_dispatcher():
    """Прод-диспетчер из main.py. Роутер цепляется к родителю один раз —
    поэтому строим его однажды на процесс."""
    global _DP
    if _DP is None:
        import main
        _DP = main.build_dispatcher(admin_user_id=1)
    return _DP


def block_network(monkeypatch) -> None:
    """Сторож на транспорте: любая попытка открыть соединение — исключение."""
    def _blocked(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("network access is blocked in tests")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)
