"""Онбординг v2 · подключение канала в один клик.

Кнопка «Подключить Telegram-канал» (t.me/<бот>?startchannel&admin=post_messages)
→ Telegram делает бота админом → апдейт my_chat_member. Проверяем развилку
по дайджестам юзера (0 / 1 / 2 без канала), что чужой дайджест не привязать
никогда, что привязанный канал лишь подтверждается, и что удаление/разжалование
бота не роняет обработку. Всё офлайн: MockedBot + заглушённые сокеты.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select

import db.session as dbs
from db.models import Channel, Target, User
from db.repositories.channels import PENDING_MODE
from tests._tg_fakes import (
    MockedBot,
    block_network,
    bot_admin,
    bot_kicked,
    bot_left,
    buttons,
    cb_update,
    get_dispatcher,
    msg_update,
    my_chat_member_update,
)

CHAT = -1001234500001


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    block_network(monkeypatch)


@pytest.fixture
async def sm(tmp_path):
    dbs.init_engine(f"sqlite+aiosqlite:///{tmp_path}/onb.db")
    await dbs.init_db()
    from bot.handlers import channels
    from bot.middlewares.rate_limit import CREATE_DIGEST_LIMIT
    CREATE_DIGEST_LIMIT.reset()
    channels._creating.clear()
    yield dbs.session_maker()
    await dbs._engine.dispose()  # иначе потоки aiosqlite переживают event loop теста
    dbs._engine = dbs._session_maker = None


@pytest.fixture
def dp():
    return get_dispatcher()


@pytest.fixture
def bot():
    return MockedBot()


async def _seed(sm, *users, digests=()):
    """users — id; digests — (id, user_id, title, target_chat_id)."""
    async with sm() as s:
        s.add_all([User(tg_user_id=u) for u in users])
        await s.flush()
        s.add_all([
            Channel(id=i, user_id=u, title=t, target_chat_id=c,
                    target_chat_title="старый" if c else None)
            for i, u, t, c in digests
        ])
        await s.commit()


async def _digest(sm, digest_id) -> Channel:
    async with sm() as s:
        return await s.get(Channel, digest_id)


async def _pending(sm, user_id=None) -> list[Target]:
    async with sm() as s:
        q = select(Target).where(Target.mode == PENDING_MODE)
        if user_id is not None:
            q = q.where(Target.user_id == user_id)
        return list((await s.execute(q)).scalars().all())


def _added(actor, chat=CHAT, title="Мой канал", old=None):
    return my_chat_member_update(
        chat_id=chat, title=title, actor_id=actor,
        old=old or bot_left(), new=bot_admin(can_post=True),
    )


# ── 1 дайджест без канала → привязка сразу ─────────────────────────────────

async def test_one_unlinked_digest_is_linked_and_user_gets_confirmation(sm, dp, bot):
    await _seed(sm, 3001, digests=[(11, 3001, "Новости электромобилей", None)])

    await dp.feed_update(bot, _added(3001))

    d = await _digest(sm, 11)
    assert d.target_chat_id == CHAT and d.target_chat_title == "Мой канал"
    assert d.is_active
    # приветствие в канал + ЛС юзеру с подтверждением и следующими шагами
    assert any("Канал активирован" in t for t in bot.texts(CHAT))
    dm = bot.messages(3001)
    assert len(dm) == 1
    assert "✅ Канал «Мой канал» подключён к дайджесту «Новости электромобилей»!" in dm[0].text
    cbs = {b.callback_data for b in buttons(dm[0])}
    assert cbs == {"scoutrun:11", "ob:list"}
    assert await _pending(sm) == []


# ── 2 дайджеста без канала → выбор кнопкой ─────────────────────────────────

async def test_two_unlinked_digests_offer_choice_then_callback_links(sm, dp, bot):
    await _seed(sm, 3002, digests=[(21, 3002, "Крикет", None), (22, 3002, "Космос", None)])

    await dp.feed_update(bot, _added(3002))

    # ничего не привязано, пока юзер не выбрал
    assert (await _digest(sm, 21)).target_chat_id is None
    assert (await _digest(sm, 22)).target_chat_id is None
    dm = bot.messages(3002)
    assert len(dm) == 1 and "Какой дайджест в нём публиковать?" in dm[0].text
    assert [b.callback_data for b in buttons(dm[0])] == [f"lnk:21:{CHAT}", f"lnk:22:{CHAT}"]
    assert [p.chat_id for p in await _pending(sm, 3002)] == [CHAT]

    await dp.feed_update(bot, cb_update(f"lnk:22:{CHAT}", user_id=3002))

    assert (await _digest(sm, 22)).target_chat_id == CHAT
    assert (await _digest(sm, 21)).target_chat_id is None
    assert await _pending(sm) == []
    assert any("подключён к дайджесту «Космос»" in t for t in bot.texts(3002))


# ── 0 дайджестов → запомнить канал, после создания привязать сам ───────────

class _FakeLLM:
    async def suggest_search_queries(self, topic, count=5, temperature=0.3):
        return ["electric vehicles", "ev charging"]

    async def _call_with_retry(self, *args, **kwargs):
        return None  # → фолбэк-отбор по подписчикам


class _FakeTwitter:
    async def search_users(self, query, limit=15):
        return [
            {"screen_name": f"author{i}", "name": f"A{i}", "followers_count": 5000 + i,
             "description": "EV news"}
            for i in range(10)
        ]


@pytest.fixture
def fake_ai(monkeypatch):
    from bot.handlers import channels

    async def _prevalidate(pairs, **kwargs):
        return [SimpleNamespace(username=u) for u, _ in pairs[:8]]

    async def _relevance(llm, topic, validated):
        return validated

    monkeypatch.setattr(channels, "_llm_smart", _FakeLLM())
    monkeypatch.setattr(channels, "_llm", _FakeLLM())
    monkeypatch.setattr(channels, "_twitter", _FakeTwitter())
    monkeypatch.setattr(channels, "prevalidate_candidates", _prevalidate)
    monkeypatch.setattr(channels, "apply_topic_relevance", _relevance)


async def test_zero_digests_remembers_channel_and_links_after_creation(sm, dp, bot, fake_ai):
    await _seed(sm, 3003)

    await dp.feed_update(bot, _added(3003, title="EV канал"))

    pending = await _pending(sm, 3003)
    assert [(p.chat_id, p.chat_title) for p in pending] == [(CHAT, "EV канал")]
    dm = bot.messages(3003)
    assert len(dm) == 1 and "Осталось создать дайджест" in dm[0].text
    assert [b.callback_data for b in buttons(dm[0])] == ["qs:ai"]

    # новичок жмёт «Создать дайджест», пишет тему и подтверждает
    await dp.feed_update(bot, cb_update("qs:ai", user_id=3003))
    await dp.feed_update(bot, msg_update("новости электромобилей", user_id=3003))
    await dp.feed_update(bot, cb_update("ft:go", user_id=3003))

    async with sm() as s:
        digests = list((await s.execute(
            select(Channel).where(Channel.user_id == 3003))).scalars().all())
    assert len(digests) == 1
    assert digests[0].target_chat_id == CHAT and digests[0].target_chat_title == "EV канал"
    assert await _pending(sm) == []
    final = bot.messages(3003)[-1]
    assert "Канал «EV канал» подключён" in final.text
    assert {b.callback_data for b in buttons(final)} == {f"scoutrun:{digests[0].id}", "ob:list"}


# ── чужой дайджест не привязывается никогда ────────────────────────────────

async def test_channel_never_linked_to_another_users_digest(sm, dp, bot):
    # у A есть дайджест без канала; канал добавляет B (без дайджестов)
    await _seed(sm, 3004, 3005, digests=[(41, 3004, "Дайджест A", None)])

    await dp.feed_update(bot, _added(3005))

    assert (await _digest(sm, 41)).target_chat_id is None
    assert [p.user_id for p in await _pending(sm)] == [3005]
    assert bot.messages(3004) == []

    # B подделывает кнопку выбора с дайджестом A — отказ
    await dp.feed_update(bot, cb_update(f"lnk:41:{CHAT}", user_id=3005))
    assert (await _digest(sm, 41)).target_chat_id is None
    # и A не может «выбрать» канал, который сам не предъявлял
    await dp.feed_update(bot, cb_update(f"lnk:41:{CHAT}", user_id=3004))
    assert (await _digest(sm, 41)).target_chat_id is None


async def test_unknown_actor_is_ignored(sm, dp, bot):
    await _seed(sm, 3006, digests=[(61, 3006, "Чей-то", None)])

    await dp.feed_update(bot, _added(3999))  # не нажимал /start

    assert (await _digest(sm, 61)).target_chat_id is None
    assert await _pending(sm) == []
    assert bot.api_calls() == []
    async with sm() as s:
        assert await s.get(User, 3999) is None


# ── уже привязан → только подтверждение, без дублей ────────────────────────

async def test_already_linked_channel_is_confirmed_not_duplicated(sm, dp, bot):
    await _seed(sm, 3007, digests=[(71, 3007, "Привязанный", CHAT), (72, 3007, "Пустой", None)])

    await dp.feed_update(bot, _added(3007))

    assert (await _digest(sm, 71)).target_chat_id == CHAT
    assert (await _digest(sm, 72)).target_chat_id is None  # второй не тронут
    assert await _pending(sm) == []
    dm = bot.messages(3007)
    assert len(dm) == 1 and "уже подключён к дайджесту «Привязанный»" in dm[0].text
    assert bot.messages(CHAT) == []  # повторного приветствия нет

    # правка прав у уже публикующего бота — молча
    bot.calls.clear()
    await dp.feed_update(bot, _added(3007, old=bot_admin(can_post=True)))
    assert bot.api_calls() == []


# ── бота убрали / разжаловали → без падений, пауза + короткое ЛС ───────────

@pytest.mark.parametrize("new_member", [bot_left(), bot_kicked(), bot_admin(can_post=False)])
async def test_bot_removed_or_demoted_does_not_crash(sm, dp, bot, new_member):
    await _seed(sm, 3008, digests=[(81, 3008, "Мой дайджест", CHAT)])

    await dp.feed_update(bot, my_chat_member_update(
        chat_id=CHAT, title="Мой канал", actor_id=3008,
        old=bot_admin(can_post=True), new=new_member,
    ))

    d = await _digest(sm, 81)
    assert d.target_chat_id == CHAT and d.is_active is False
    dm = bot.texts(3008)
    assert len(dm) == 1 and "на паузе" in dm[0]

    # вернули права → пауза снята, юзеру — подтверждение
    bot.calls.clear()
    await dp.feed_update(bot, my_chat_member_update(
        chat_id=CHAT, title="Мой канал", actor_id=3008,
        old=new_member, new=bot_admin(can_post=True),
    ))
    assert (await _digest(sm, 81)).is_active is True
    assert any("Права вернулись" in t for t in bot.texts(3008))


async def test_removed_from_unlinked_channel_drops_pending_quietly(sm, dp, bot):
    await _seed(sm, 3009)
    await dp.feed_update(bot, _added(3009))
    assert len(await _pending(sm, 3009)) == 1

    bot.calls.clear()
    await dp.feed_update(bot, my_chat_member_update(
        chat_id=CHAT, title="Мой канал", actor_id=3009,
        old=bot_admin(can_post=True), new=bot_left(),
    ))
    assert await _pending(sm) == []
    assert bot.api_calls() == []


async def test_removed_by_unknown_actor_no_crash(sm, dp, bot):
    await _seed(sm, 3010, digests=[(101, 3010, "Д", CHAT)])
    await dp.feed_update(bot, my_chat_member_update(
        chat_id=CHAT, title="К", actor_id=3998, old=bot_admin(), new=bot_left(),
    ))
    assert (await _digest(sm, 101)).is_active is False
    assert len(bot.texts(3010)) == 1  # владельцу дайджеста — коротко
    assert bot.texts(3998) == []


async def test_group_membership_is_not_a_channel_link(sm, dp, bot):
    await _seed(sm, 3011, digests=[(111, 3011, "Д", None)])
    await dp.feed_update(bot, my_chat_member_update(
        chat_id=-100777, title="Группа", actor_id=3011,
        old=bot_left(), new=bot_admin(), chat_type="supergroup",
    ))
    assert (await _digest(sm, 111)).target_chat_id is None
    assert bot.api_calls() == []


# ── запасной путь: переслать пост из канала ────────────────────────────────

@pytest.mark.parametrize("status", ["administrator", "creator"])
async def test_forward_fallback_uses_same_linking(sm, dp, bot, status):
    from aiogram.types import Chat
    await _seed(sm, 3012, digests=[(121, 3012, "Форвард", None)])
    bot.chat_members[(CHAT, 3012)] = status  # юзер — админ/владелец канала

    await dp.feed_update(bot, msg_update(
        "тест", user_id=3012,
        forward_from=Chat(id=CHAT, type="channel", title="Канал через форвард"),
    ))

    d = await _digest(sm, 121)
    assert d.target_chat_id == CHAT and d.target_chat_title == "Канал через форвард"
    assert any("подключён к дайджесту «Форвард»" in t for t in bot.texts(3012))


async def test_forward_in_group_is_ignored(sm, dp, bot):
    from aiogram.types import Chat
    await _seed(sm, 3013, digests=[(131, 3013, "Д", None)])
    await dp.feed_update(bot, msg_update(
        "пост", user_id=3013, chat_type="supergroup",
        forward_from=Chat(id=CHAT, type="channel", title="Чужой канал"),
    ))
    assert (await _digest(sm, 131)).target_chat_id is None
    assert bot.api_calls() == []


# ── запасной путь и /bind: только канал, где юзер сам админ ────────────────

NOT_ADMIN = "Привязать можно только канал, где ты сам администратор"


def _forbidden():
    from aiogram.exceptions import TelegramForbiddenError
    from aiogram.methods import GetChatMember
    return TelegramForbiddenError(
        method=GetChatMember(chat_id=CHAT, user_id=1), message="Forbidden: bot is not a member",
    )


def _bad_request():
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import GetChatMember
    return TelegramBadRequest(
        method=GetChatMember(chat_id=CHAT, user_id=1), message="Bad Request: chat not found",
    )


async def _snapshot(sm):
    async with sm() as s:
        rows = (await s.execute(select(Channel).order_by(Channel.id))).scalars().all()
        return [(c.id, c.target_chat_id, c.target_chat_title, c.is_active) for c in rows]


@pytest.mark.parametrize("member", ["member", "unset", "bad_request", "forbidden"])
async def test_forward_from_foreign_channel_is_rejected(sm, dp, bot, member):
    from aiogram.types import Chat
    await _seed(sm, 3020, digests=[(201, 3020, "Мой", None)])
    if member == "member":
        bot.chat_members[(CHAT, 3020)] = "member"          # просто подписчик
    elif member == "bad_request":
        bot.chat_members[(CHAT, 3020)] = _bad_request()    # бота нет в канале
    elif member == "forbidden":
        bot.chat_members[(CHAT, 3020)] = _forbidden()      # нет прав / выгнали
    before = await _snapshot(sm)

    await dp.feed_update(bot, msg_update(
        "тест", user_id=3020,
        forward_from=Chat(id=CHAT, type="channel", title="Чужой канал"),
    ))

    assert await _snapshot(sm) == before
    assert await _pending(sm) == []
    [reply] = bot.texts(3020)
    assert NOT_ADMIN in reply
    assert bot.messages(CHAT) == []  # в чужой канал ничего не ушло


async def test_rejected_forward_cannot_be_picked_via_lnk(sm, dp, bot):
    from aiogram.types import Chat
    # два дайджеста без канала: при успехе был бы выбор кнопками
    await _seed(sm, 3021, digests=[(211, 3021, "A", None), (212, 3021, "B", None)])
    bot.chat_members[(CHAT, 3021)] = "member"

    await dp.feed_update(bot, msg_update(
        "тест", user_id=3021,
        forward_from=Chat(id=CHAT, type="channel", title="Чужой канал"),
    ))
    assert await _pending(sm) == []

    # подделанная кнопка выбора на отвергнутый канал — отказ
    before = await _snapshot(sm)
    await dp.feed_update(bot, cb_update(f"lnk:211:{CHAT}", user_id=3021))
    assert await _snapshot(sm) == before


@pytest.mark.parametrize("status", ["administrator", "creator"])
async def test_bind_links_when_user_is_chat_admin(sm, dp, bot, status):
    await _seed(sm, 3022, digests=[(221, 3022, "Ручной", None)])
    bot.chat_members[(CHAT, 3022)] = status

    await dp.feed_update(bot, msg_update(f"/bind {CHAT} 221", user_id=3022))

    assert (await _digest(sm, 221)).target_chat_id == CHAT
    assert "подключён к дайджесту «Ручной»" in bot.texts(3022)[-1]


@pytest.mark.parametrize("member", ["member", "unset", "bad_request", "forbidden"])
async def test_bind_rejected_when_user_is_not_chat_admin(sm, dp, bot, member):
    await _seed(sm, 3023, digests=[(231, 3023, "Ручной", None)])
    if member == "member":
        bot.chat_members[(CHAT, 3023)] = "member"
    elif member == "bad_request":
        bot.chat_members[(CHAT, 3023)] = _bad_request()
    elif member == "forbidden":
        bot.chat_members[(CHAT, 3023)] = _forbidden()
    before = await _snapshot(sm)

    await dp.feed_update(bot, msg_update(f"/bind {CHAT} 231", user_id=3023))

    assert await _snapshot(sm) == before
    [reply] = bot.texts(3023)
    assert NOT_ADMIN in reply
