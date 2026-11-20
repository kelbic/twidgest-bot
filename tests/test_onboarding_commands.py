"""Онбординг v2 · команды, группы, шаблоны, polling, лимит создания.

Кейс из живого теста: новичок всё делал в группе «Чат BugTest» — тап по
/createchannel_gaming слал /createchannel_gaming@TwidgestBot, суффикс не
срезался, три ошибки съели лимит «3 за 5 мин» и заперли юзера на 243 с.
Офлайн: MockedBot + заглушённые сокеты, апдейты — через прод-диспетчер.
"""
from __future__ import annotations

import ast
import pathlib
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

import db.session as dbs
from db.models import Channel, User
from tests._tg_fakes import (
    MockedBot,
    block_network,
    buttons,
    cb_update,
    get_dispatcher,
    msg_update,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent
SETUP_URL = "https://t.me/TwidgestBot?start=setup"


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    block_network(monkeypatch)


@pytest.fixture
async def sm(tmp_path):
    dbs.init_engine(f"sqlite+aiosqlite:///{tmp_path}/cmd.db")
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


class _FakeLLM:
    async def suggest_search_queries(self, topic, count=5, temperature=0.3):
        return ["electric vehicles"]

    async def _call_with_retry(self, *args, **kwargs):
        return None


class _FakeTwitter:
    async def search_users(self, query, limit=15):
        return [{"screen_name": f"a{i}", "name": "A", "followers_count": 9000,
                 "description": "x"} for i in range(9)]


@pytest.fixture
def fake_ai(monkeypatch):
    from bot.handlers import channels

    async def _prevalidate(pairs, **kwargs):
        return [SimpleNamespace(username=u) for u, _ in pairs[:7]]

    async def _relevance(llm, topic, validated):
        return validated

    monkeypatch.setattr(channels, "_llm_smart", _FakeLLM())
    monkeypatch.setattr(channels, "_llm", _FakeLLM())
    monkeypatch.setattr(channels, "_twitter", _FakeTwitter())
    monkeypatch.setattr(channels, "prevalidate_candidates", _prevalidate)
    monkeypatch.setattr(channels, "apply_topic_relevance", _relevance)


async def _digests(sm, user_id) -> int:
    async with sm() as s:
        return int((await s.execute(
            select(func.count(Channel.id)).where(Channel.user_id == user_id)
        )).scalar_one())


# ── 2. группа → кнопка в личку; суффикс @бот срезается ─────────────────────

@pytest.mark.parametrize("text", [
    "/createchannel_gaming@TwidgestBot",       # тап из старого списка шаблонов
    "/createchannel@TwidgestBot новости AI и LLM",
    "/start@TwidgestBot",
    "/start@twidgestbot setup",                 # упоминание без учёта регистра
    "/channels",                                # своя команда без упоминания
    "/bind -100123 5",
])
async def test_group_command_redirects_to_dm(sm, dp, bot, text):
    await dp.feed_update(bot, msg_update(text, user_id=4001, chat_type="supergroup"))

    sent = bot.messages()
    assert len(sent) == 1
    reply = sent[0]
    assert int(reply.chat_id) == -1009990001  # ответ в ту же группу, коротко
    assert [b.url for b in buttons(reply)] == [SETUP_URL]
    assert "не найден" not in reply.text
    # флоу в группе не запускался: ни дайджеста, ни юзера, ни лимита
    assert await _digests(sm, 4001) == 0
    async with sm() as s:
        assert await s.get(User, 4001) is None


@pytest.mark.parametrize("text", ["/start@OtherBot", "/weather", "просто текст"])
async def test_group_ignores_foreign_commands_and_chatter(sm, dp, bot, text):
    await dp.feed_update(bot, msg_update(text, user_id=4002, chat_type="group"))
    assert bot.api_calls() == []


async def test_bot_mention_suffix_is_stripped_in_dm(sm, dp, bot):
    await dp.feed_update(bot, msg_update("/help@TwidgestBot", user_id=4003))
    await dp.feed_update(bot, msg_update("/createchannel@twidgestbot", user_id=4003))
    await dp.feed_update(bot, msg_update("/bind@TwidgestBot", user_id=4003))
    texts = bot.texts(4003)
    assert "Как пользоваться TwidgestBot" in texts[0]
    assert "Создание дайджеста" in texts[1]
    assert "Использование: <code>/bind" in texts[2]


async def test_old_template_command_in_dm_gets_hint_not_error(sm, dp, bot):
    await dp.feed_update(bot, msg_update("/createchannel_gaming@TwidgestBot", user_id=4004))
    [reply] = bot.messages(4004)
    assert "Не знаю такой команды" in reply.text
    assert "не найден" not in reply.text
    assert [b.callback_data for b in buttons(reply)] == ["qs:ai"]


async def test_start_setup_opens_onboarding_without_attribution(sm, dp, bot):
    await dp.feed_update(bot, msg_update("/start setup", user_id=4005))
    await dp.feed_update(bot, msg_update("/start src_vc", user_id=4006))
    async with sm() as s:
        assert (await s.get(User, 4005)).start_source is None    # служебный параметр
        assert (await s.get(User, 4006)).start_source == "src_vc"  # витрина — как раньше
    [welcome] = bot.messages(4005)
    labels = [b.text for b in buttons(welcome)]
    assert labels == ["🚀 Создать дайджест", "❓ Как это работает"]


async def test_start_offers_connect_button_when_digest_has_no_channel(sm, dp, bot):
    async with sm() as s:
        s.add(User(tg_user_id=4007))
        await s.flush()
        s.add(Channel(user_id=4007, title="Без канала"))
        await s.commit()
    await dp.feed_update(bot, msg_update("/start setup", user_id=4007))
    [welcome] = bot.messages(4007)
    first = buttons(welcome)[0]
    assert first.text == "📢 Подключить Telegram-канал"
    assert first.url == "https://t.me/TwidgestBot?startchannel&admin=post_messages"


# ── 3. ссылок на убранные шаблоны в пользовательских текстах нет ───────────

def _user_strings(path: pathlib.Path) -> list[str]:
    """Все строковые литералы файла, кроме docstring'ов (их юзер не видит)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    return [
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
    ]


def test_no_template_references_in_user_facing_code():
    files = [ROOT / "main.py", *sorted((ROOT / "bot").rglob("*.py")),
             *sorted((ROOT / "workers").rglob("*.py"))]
    offenders = [
        f"{p.relative_to(ROOT)}: {s[:60]!r}"
        for p in files for s in _user_strings(p)
        if "/templates" in s or "createchannel_" in s
    ]
    assert offenders == []


def test_set_my_commands_has_no_templates():
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    cmds = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "BotCommand":
            kw = {k.arg: k.value.value for k in n.keywords}
            cmds[kw["command"]] = kw["description"]
    assert "templates" not in cmds
    assert "шаблон" not in cmds["createchannel"].lower()
    assert "дайджест" in cmds["createchannel"].lower()


def test_user_keyboards_have_no_template_buttons():
    from bot.handlers.start import WELCOME_KB
    datas = [b.callback_data for row in WELCOME_KB.inline_keyboard for b in row]
    assert not any("templat" in (d or "") for d in datas)


def test_templates_kept_for_audit_job():
    # Шаблоны убраны только из пользовательского флоу: аудит-джоба их читает
    from templates import TEMPLATES
    assert TEMPLATES
    assert (ROOT / "tools" / "audit_templates.py").exists()
    assert "from templates import" in (ROOT / "tools" / "audit_templates.py").read_text()


# ── 4. my_chat_member реально уходит в polling ─────────────────────────────

async def test_polling_requests_my_chat_member(monkeypatch):
    import main
    dp = get_dispatcher()
    captured = {}

    async def _fake_start_polling(*bots, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(dp, "start_polling", _fake_start_polling)
    await main.run_polling(dp, MockedBot())

    allowed = captured["allowed_updates"]
    assert "my_chat_member" in allowed
    assert {"message", "callback_query", "pre_checkout_query"} <= set(allowed)
    # хендлер реально зарегистрирован, а не только дописан в список
    assert "my_chat_member" in dp.resolve_used_update_types()


def test_main_polls_only_through_run_polling():
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    funcs = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    polls = [
        (fn.name, call) for fn in funcs.values() for call in ast.walk(fn)
        if isinstance(call, ast.Call) and getattr(call.func, "attr", None) == "start_polling"
    ]
    assert [name for name, _ in polls] == ["run_polling"]
    assert any(k.arg == "allowed_updates" for k in polls[0][1].keywords)
    assert any(
        isinstance(c, ast.Call) and getattr(c.func, "id", None) == "run_polling"
        for c in ast.walk(funcs["main"])
    )


# ── 5. лимит создания: ошибки не тратят, успех тратит ──────────────────────

async def test_validation_errors_do_not_consume_create_limit(sm, dp, bot, fake_ai):
    from bot.middlewares.rate_limit import CREATE_DIGEST_LIMIT
    uid = 4101
    junk = [
        "/createchannel",                         # без темы → подсказка
        "/createchannel коротко",                 # < 10 символов
        "/createchannel template ai-news",        # шаблонов больше нет
        "/createchannel_gaming@TwidgestBot",      # неизвестная команда
        "/createchannel_gaming",
    ] * 3
    for text in junk:
        await dp.feed_update(bot, msg_update(text, user_id=uid))
    assert CREATE_DIGEST_LIMIT.retry_after(uid) == 0
    assert not any("⏳" in t for t in bot.texts(uid))
    assert await _digests(sm, uid) == 0

    await dp.feed_update(bot, msg_update("/createchannel новости электромобилей", user_id=uid))
    assert await _digests(sm, uid) == 1
    assert "Дайджест «новости электромобилей» готов" in bot.texts(uid)[-1]


async def test_successful_creations_consume_create_limit(sm, dp, bot, fake_ai):
    from bot.middlewares.rate_limit import CREATE_DIGEST_LIMIT
    uid = 4102
    for i in range(3):
        await dp.feed_update(bot, msg_update(f"/createchannel тема номер {i} про AI", user_id=uid))
    assert await _digests(sm, uid) == 3
    assert CREATE_DIGEST_LIMIT.retry_after(uid) > 0

    await dp.feed_update(bot, msg_update("/createchannel ещё одна тема про AI", user_id=uid))
    assert await _digests(sm, uid) == 3
    assert "⏳ Можно создавать не больше 3 дайджестов" in bot.texts(uid)[-1]

    # путь «тема текстом → кнопка Создать» упирается в тот же лимит
    await dp.feed_update(bot, msg_update("ещё одна тема текстом", user_id=uid))
    await dp.feed_update(bot, cb_update("ft:go", user_id=uid))
    assert await _digests(sm, uid) == 3


async def test_creation_result_offers_one_click_channel_connection(sm, dp, bot, fake_ai):
    uid = 4103
    await dp.feed_update(bot, msg_update("/createchannel новости электромобилей", user_id=uid))
    final = bot.messages(uid)[-1]
    assert "Если канала ещё нет — создай пустой канал в Telegram. Затем нажми кнопку ниже и выбери его." in final.text
    assert "не имеет доступа к сообщениям" in final.text
    assert "id=" not in final.text and "forward" not in final.text.lower()
    assert [(b.text, b.url) for b in buttons(final)] == [
        ("📢 Подключить Telegram-канал",
         "https://t.me/TwidgestBot?startchannel&admin=post_messages"),
    ]


async def test_group_commands_do_not_consume_command_limits():
    from aiogram.types import Chat, Message
    from bot.middlewares.rate_limit import RateLimitMiddleware
    from tests._tg_fakes import tg_user
    from datetime import datetime, timezone

    mw = RateLimitMiddleware(admin_user_id=1)
    calls = []

    async def handler(event, data):
        calls.append(event.text)

    group_msg = Message(
        message_id=1, date=datetime.now(timezone.utc),
        chat=Chat(id=-100, type="supergroup"), from_user=tg_user(4104),
        text="/scout@TwidgestBot 5",
    )
    for _ in range(5):
        await mw(handler, group_msg, {})
    assert len(calls) == 5 and not mw._calls
