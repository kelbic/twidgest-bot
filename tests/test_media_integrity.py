"""Quality Gate v1 · Фикс 2 — Media Integrity & Visual Claim Alignment.

Блок дубля картинки в окне 20 постов канала, кулдаун стока 5 постов, приоритет медиа
первоисточника, «Хаббл сфотографировал» без оригинального медиа → перезапись или
rejected_missing_media. Сток берётся из НАСТОЯЩЕГО кэша image_picker; сеть закрыта.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import core.image_picker as ip
import db.session as dbs
from core.image_picker import fetch_image_url_for_keywords, media_fingerprint
from core.quality_gate import (
    REJECT_MISSING_MEDIA,
    PreparedPost,
    Rejected,
    prepare_single_post,
)
from db.models import PostLog

SRC = '<a href="https://x.com/NASAHubble/status/9">→ Источник</a>'
STOCK_A = "https://images.unsplash.com/photo-aaa?ixid=1&w=1080"
STOCK_B = "https://images.unsplash.com/photo-bbb?ixid=2&w=1080"
ORIG = "https://pbs.twimg.com/media/HUBBLE.jpg?name=large"
HUBBLE = ("<b>«Хаббл» сфотографировал спиральную галактику NGC 1300</b>\n"
          f"Галактика находится в 61 млн световых лет от Земли.\n\n{SRC}")
PLAIN = f"<b>SpaceX провела 150-й запуск года</b>\nFalcon 9 вывела 24 спутника.\n\n{SRC}"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    class _Boom:
        def __init__(self, *a, **k):
            raise AssertionError("сеть в тесте: Unsplash")
    monkeypatch.setattr(ip.aiohttp, "ClientSession", _Boom)
    ip._CACHE.clear()
    ip._CACHE["space galaxy"] = ([STOCK_A, STOCK_B], time.time())
    yield
    ip._CACHE.clear()


class FakeLLM:
    def __init__(self, rewrite=None, keywords="space galaxy"):
        self.rewrite, self.keywords = rewrite, keywords
        self.rewrites = self.kw_calls = 0

    async def _call_with_retry(self, system, user, **kw):
        self.rewrites += 1
        return self.rewrite

    async def suggest_image_keywords(self, text):
        self.kw_calls += 1
        return self.keywords


@pytest.fixture
async def sm(tmp_path):
    dbs.init_engine(f"sqlite+aiosqlite:///{tmp_path}/mi.db")
    await dbs.init_db()
    yield dbs.session_maker()
    dbs._engine = dbs._session_maker = None


def _channel(cid=5, images=True):
    return SimpleNamespace(id=cid, images_enabled=images, user_id=1, paid_until=None,
                           trial_until=None, attribution_mode=None)


async def _seed_posts(sm, cid, fps):
    """fps — от СТАРЫХ к новым: последний элемент — самый свежий пост."""
    base = datetime.utcnow() - timedelta(hours=len(fps) + 1)
    async with sm() as s:
        for i, fp in enumerate(fps):
            s.add(PostLog(user_id=1, target_id=cid, posted_at=base + timedelta(hours=i),
                          media_fingerprint=fp))
        await s.commit()


async def _prepare(sm, llm, text=PLAIN, original=None, channel=None):
    async with sm() as s:
        return await prepare_single_post(session=s, channel=channel or _channel(), llm=llm,
                                         text=text, original_media_url=original,
                                         unsplash_key="k")


# ── анти-дубль и кулдаун ─────────────────────────────────────────────────────────────

def test_fingerprint_ignores_query_params():
    assert media_fingerprint(STOCK_A, "stock") == media_fingerprint(
        "https://images.unsplash.com/photo-aaa?w=400", "stock")
    assert media_fingerprint(STOCK_A, "stock") != media_fingerprint(STOCK_A, "orig")


async def test_image_picker_skips_used_and_gives_none_when_all_used():
    used_a = {media_fingerprint(STOCK_A, "stock")}
    assert await fetch_image_url_for_keywords("space galaxy", "k", exclude=used_a) == STOCK_B
    used_ab = used_a | {media_fingerprint(STOCK_B, "stock")}
    assert await fetch_image_url_for_keywords("space galaxy", "k", exclude=used_ab) is None


async def test_duplicate_stock_blocked_within_20_posts(sm):
    # STOCK_A стоял 10 постов назад (кулдаун 5 пройден, окно 20 — нет)
    await _seed_posts(sm, 5, [media_fingerprint(STOCK_A, "stock")] + [None] * 9)
    res = await _prepare(sm, FakeLLM())
    assert isinstance(res, PreparedPost)
    assert res.photo_url == STOCK_B
    assert res.media_fingerprint == media_fingerprint(STOCK_B, "stock")


async def test_stock_reusable_after_window(sm):
    # STOCK_A стоял 21 пост назад — вне окна 20, повтор допустим
    await _seed_posts(sm, 5, [media_fingerprint(STOCK_A, "stock")] + [None] * 20)
    res = await _prepare(sm, FakeLLM())
    assert res.photo_url == STOCK_A


async def test_all_unused_exhausted_posts_text_only(sm):
    await _seed_posts(sm, 5, [media_fingerprint(STOCK_A, "stock"),
                              media_fingerprint(STOCK_B, "stock")] + [None] * 6)
    res = await _prepare(sm, FakeLLM())
    assert isinstance(res, PreparedPost) and res.photo_url is None and res.media_fingerprint is None


async def test_stock_cooldown_5_posts(sm):
    # сток был 3 поста назад → без картинки, и даже keywords не спрашиваем
    await _seed_posts(sm, 5, [media_fingerprint("https://images.unsplash.com/photo-zzz", "stock"),
                              None, None])
    llm = FakeLLM()
    res = await _prepare(sm, llm)
    assert res.photo_url is None and llm.kw_calls == 0


async def test_other_channel_history_does_not_block(sm):
    await _seed_posts(sm, 6, [media_fingerprint(STOCK_A, "stock")])
    res = await _prepare(sm, FakeLLM())
    assert res.photo_url == STOCK_A


# ── медиа первоисточника ─────────────────────────────────────────────────────────────

async def test_original_media_preferred_over_stock(sm):
    llm = FakeLLM()
    res = await _prepare(sm, llm, original=ORIG)
    assert res.photo_url == ORIG and res.media_fingerprint.startswith("orig:")
    assert llm.kw_calls == 0


async def test_duplicate_original_not_reattached(sm):
    await _seed_posts(sm, 5, [media_fingerprint(ORIG, "orig")])
    res = await _prepare(sm, FakeLLM(), original=ORIG)
    assert res.photo_url != ORIG


# ── «Хаббл сфотографировал» без оригинального медиа ──────────────────────────────────

async def test_visual_claim_with_original_media_kept_as_is(sm):
    llm = FakeLLM(rewrite="SKIP")
    res = await _prepare(sm, llm, text=HUBBLE, original=ORIG)
    assert isinstance(res, PreparedPost) and res.text == HUBBLE and res.photo_url == ORIG
    assert llm.rewrites == 0


async def test_visual_claim_without_media_rejected_when_image_is_the_news(sm):
    res = await _prepare(sm, FakeLLM(rewrite="SKIP"), text=HUBBLE)
    assert isinstance(res, Rejected) and res.reason == REJECT_MISSING_MEDIA


async def test_visual_claim_without_media_rewritten_to_text(sm):
    fixed = ("<b>NGC 1300: спиральная галактика в 61 млн световых лет</b>\n"
             f"Галактика с перемычкой в созвездии Эридан.\n\n{SRC}")
    llm = FakeLLM(rewrite=fixed, keywords=None)
    res = await _prepare(sm, llm, text=HUBBLE)
    assert isinstance(res, PreparedPost) and res.text == fixed and llm.rewrites == 1


async def test_rewrite_that_keeps_the_promise_is_rejected(sm):
    still = f"<b>На новом снимке «Хаббла» — галактика NGC 1300</b>\n61 млн св. лет.\n\n{SRC}"
    res = await _prepare(sm, FakeLLM(rewrite=still), text=HUBBLE)
    assert isinstance(res, Rejected) and res.reason == REJECT_MISSING_MEDIA


async def test_visual_claim_images_disabled_still_checked(sm):
    res = await _prepare(sm, FakeLLM(rewrite="SKIP"), text=HUBBLE, original=ORIG,
                         channel=_channel(images=False))
    assert isinstance(res, Rejected) and res.reason == REJECT_MISSING_MEDIA
