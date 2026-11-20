"""Quality Gate v1 · Фикс 3 — атрибуция витрины и воронка deep-link'ов.

Футер по режимам off / digest_only / all_posts и в авто-режиме по статусу канала;
вставка в строку «→ Источник»; подпись переживает обрезку подписи к фото и не
обходит защиту от мета-ответов; /start src_<slug> пишет первое касание; воронка по
источникам; админ-команда; безопасная миграция новых колонок на старой базе.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from aiogram.filters import CommandObject

import db.session as dbs
from core.attribution import (
    apply_footer,
    deep_link,
    effective_mode,
    footer_for,
    parse_start_source,
)
from core.safe_sender import ChannelTarget, send_to_target

SRC = '<a href="https://x.com/a/status/1">→ Источник</a>'
POST = f"<b>Заголовок</b>\nТекст новости с цифрой 42.\n\n{SRC}"
LINK6 = "https://t.me/TwidgestBot?start=src_c6"
FOOT6 = f'<a href="{LINK6}">Собрано @TwidgestBot</a>'


def _ch(cid=6, mode=None, user_id=1, paid=None, trial=None):
    return SimpleNamespace(id=cid, attribution_mode=mode, user_id=user_id,
                           paid_until=paid, trial_until=trial)


# ── режимы ───────────────────────────────────────────────────────────────────────────

def test_footer_modes():
    off, dig, allp = _ch(mode="off"), _ch(mode="digest_only"), _ch(mode="all_posts")
    assert footer_for(off, is_digest=False) == "" and footer_for(off, is_digest=True) == ""
    assert footer_for(dig, is_digest=False) == "" and footer_for(dig, is_digest=True) == FOOT6
    assert footer_for(allp, is_digest=False) == FOOT6 and footer_for(allp, is_digest=True) == FOOT6


def test_auto_mode_by_channel_status():
    future = datetime.utcnow() + timedelta(days=5)
    assert effective_mode(_ch(user_id=1)) == "all_posts"                  # витрина админа
    assert effective_mode(_ch(user_id=77, trial=future)) == "all_posts"   # триал
    assert effective_mode(_ch(user_id=77, paid=future)) == "off"          # оплаченный
    assert effective_mode(_ch(user_id=77, paid=future, mode="all_posts")) == "all_posts"


def test_deep_link_format():
    assert deep_link("c6") == LINK6


def test_single_footer_same_line_as_source():
    out = apply_footer(POST, FOOT6)
    assert out.endswith(f"{SRC} · {FOOT6}")
    assert out.count("\n") == POST.count("\n")          # новой строки не добавилось


def test_digest_footer_own_last_line_and_lowercase_source():
    digest = ('🌐 <b>Главное</b>\n\n<b>Тема</b>\nТекст. <a href="u1">→ источник</a>\n\n'
              '<b>Тема 2</b>\nТекст. <a href="u2">→ источник</a>')
    out = apply_footer(digest, FOOT6, is_digest=True)
    assert out.endswith(f'→ источник</a>\n\n{FOOT6}')
    single = apply_footer('Текст. <a href="u">→ источник</a>', FOOT6)
    assert single.endswith(f'→ источник</a> · {FOOT6}')


# ── отправка ─────────────────────────────────────────────────────────────────────────

class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append(("text", text))

    async def send_photo(self, chat_id, photo, caption, **kw):
        self.sent.append(("photo", caption))


async def test_send_text_carries_footer():
    bot = FakeBot()
    assert await send_to_target(bot, None, ChannelTarget(6, -100), POST, footer=FOOT6)
    assert bot.sent == [("text", apply_footer(POST, FOOT6))]


async def test_long_caption_keeps_footer():
    bot = FakeBot()
    long_post = "<b>Т</b>\n" + "слово " * 300 + f"\n\n{SRC}"
    assert await send_to_target(bot, None, ChannelTarget(6, -100), long_post,
                                photo_url="https://x/p.jpg", footer=FOOT6)
    kind, caption = bot.sent[0]
    assert kind == "photo" and len(caption) <= 1024 and caption.endswith(FOOT6)


async def test_footer_does_not_bypass_meta_guard():
    bot = FakeBot()
    meta = f"К сожалению, не могу составить пост. {SRC}"
    assert not await send_to_target(bot, None, ChannelTarget(6, -100), meta, footer=FOOT6)
    assert bot.sent == []


# ── /start и воронка ─────────────────────────────────────────────────────────────────

@pytest.fixture
async def sm(tmp_path):
    dbs.init_engine(f"sqlite+aiosqlite:///{tmp_path}/af.db")
    await dbs.init_db()
    yield dbs.session_maker()
    dbs._engine = dbs._session_maker = None


class FakeMsg:
    def __init__(self, uid):
        self.from_user = SimpleNamespace(id=uid, username=f"u{uid}")
        self.answers = []

    async def answer(self, text, **kw):
        self.answers.append(text)


def _cmd(args):
    return CommandObject(prefix="/", command="start", mention=None, args=args)


def test_parse_start_source():
    assert parse_start_source("src_c6") == "src_c6"
    assert parse_start_source("src_vc") == "src_vc"
    assert parse_start_source("<script>") is None
    assert parse_start_source(None) is None


async def test_start_records_first_touch_only(sm):
    from bot.handlers.start import cmd_start
    from db.models import User
    await cmd_start(FakeMsg(500), _cmd("src_c6"))
    await cmd_start(FakeMsg(500), _cmd("src_vc"))        # повторный /start — не перезаписывает
    await cmd_start(FakeMsg(501), _cmd(None))
    await cmd_start(FakeMsg(502), _cmd("bad param!"))
    async with sm() as s:
        assert (await s.get(User, 500)).start_source == "src_c6"
        assert (await s.get(User, 501)).start_source is None
        assert (await s.get(User, 502)).start_source is None


async def test_funnel_by_source(sm):
    from db.models import Channel, User
    from db.repositories.admin import get_funnel_by_source
    async with sm() as s:
        s.add_all([User(tg_user_id=1), User(tg_user_id=10, start_source="src_c5"),
                   User(tg_user_id=11, start_source="src_c5"),
                   User(tg_user_id=12, start_source="src_vc"), User(tg_user_id=13)])
        s.add_all([Channel(user_id=10, title="a", target_chat_id=-1),
                   Channel(user_id=12, title="b")])
        await s.commit()
        by = await get_funnel_by_source(s, since=None, exclude_user_id=1)
    assert set(by) == {"src_c5", "src_vc"}
    assert by["src_c5"]["registered"] == 2 and by["src_c5"]["bound_channel"] == 1
    assert by["src_vc"]["created_channel"] == 1 and by["src_vc"]["bound_channel"] == 0


async def test_admin_attribution_command(sm):
    from bot.handlers.admin import _admin_attribution
    from db.models import Channel, User
    async with sm() as s:
        s.add(User(tg_user_id=1))
        s.add(Channel(id=6, user_id=1, title="AI за 5 минут"))
        await s.commit()
    msg = FakeMsg(1)
    await _admin_attribution(msg, ["6", "all_posts"])
    async with sm() as s:
        assert (await s.get(Channel, 6)).attribution_mode == "all_posts"
    assert LINK6 in msg.answers[-1]
    await _admin_attribution(msg, ["6", "auto"])
    async with sm() as s:
        assert (await s.get(Channel, 6)).attribution_mode is None
    await _admin_attribution(msg, ["6", "loud"])
    assert msg.answers[-1].startswith("Usage")


# ── миграция ─────────────────────────────────────────────────────────────────────────

async def test_new_columns_added_to_old_db_without_data_loss(tmp_path):
    dbs.init_engine(f"sqlite+aiosqlite:///{tmp_path}/old.db")
    eng = dbs._engine
    try:
        async with eng.begin() as c:
            await c.exec_driver_sql("CREATE TABLE users (tg_user_id BIGINT PRIMARY KEY, "
                                    "tier VARCHAR(16), trial_used BOOLEAN, created_at DATETIME)")
            await c.exec_driver_sql("CREATE TABLE channels (id INTEGER PRIMARY KEY, user_id BIGINT, "
                                    "title VARCHAR(255))")
            await c.exec_driver_sql("CREATE TABLE post_log (id INTEGER PRIMARY KEY, user_id BIGINT, "
                                    "target_id INTEGER, posted_at DATETIME, is_digest BOOLEAN)")
            await c.exec_driver_sql("CREATE TABLE digest_queue (id INTEGER PRIMARY KEY, user_id BIGINT, "
                                    "channel_id INTEGER, tweet_id VARCHAR(32), twitter_username "
                                    "VARCHAR(32), text TEXT, url VARCHAR(512))")
            await c.exec_driver_sql("INSERT INTO users VALUES (7, 'free', 0, '2026-01-01')")
            await c.exec_driver_sql("INSERT INTO channels VALUES (6, 7, 'AI')")
            await c.exec_driver_sql("INSERT INTO post_log VALUES (1, 7, 6, '2026-01-02', 0)")
            await c.exec_driver_sql("INSERT INTO digest_queue VALUES (1, 7, 6, 't', 'u', 'x', 'u')")
        await dbs.init_db()
        async with eng.connect() as c:
            for table, col in (("users", "start_source"), ("channels", "attribution_mode"),
                               ("post_log", "media_fingerprint"), ("digest_queue", "substance")):
                cols = {r[1] for r in (await c.exec_driver_sql(f"PRAGMA table_info({table})")).fetchall()}
                assert col in cols, (table, col)
                n, nulls = (await c.exec_driver_sql(
                    f"SELECT count(*), sum({col} IS NULL) FROM {table}")).fetchone()
                assert n == 1 and nulls == 1, (table, n, nulls)
    finally:
        dbs._engine = dbs._session_maker = None
