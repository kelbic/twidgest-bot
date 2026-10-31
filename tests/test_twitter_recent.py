"""Поиск по окну свежести вместо last_tweets (биллинг twitterapi.io — за каждый твит).

Проверяется не «вызов прошёл», а контракт, на котором держится экономия и порог
min_likes: запрос ограничен окном и без ответов, «ошибка» отличима от «пусто», и кэш
откатывается на last_tweets ТОЛЬКО при ошибке поиска.
"""
from __future__ import annotations

import time

import pytest

from core import twitter_client as tc
from core.twitter_cache import TwitterCache


def _raw(i: int, user: str = "NASA") -> dict:
    return {"id": str(i), "text": f"tweet number {i} with enough text", "author": {"userName": user},
            "likeCount": 10 * i, "retweetCount": i, "replyCount": 0, "isReply": False,
            "createdAt": "Thu Oct 30 10:00:00 +0000 2026"}


@pytest.fixture()
def client():
    return tc.TwitterClient("k")


async def test_query_is_windowed_without_replies(client, monkeypatch):
    seen = {}

    async def fake(method, url, params=None, timeout=30):
        seen.update(url=url, params=params)
        return {"tweets": [_raw(1), _raw(2)], "has_next_page": False, "next_cursor": ""}

    monkeypatch.setattr(client, "_request_with_retry", fake)
    t0 = int(time.time())
    got = await client.get_recent_tweets("NASA")
    assert [t.id for t in got] == ["1", "2"]
    assert got[1].likes == 20 and got[0].username == "NASA"
    assert seen["url"].endswith("/twitter/tweet/advanced_search")
    q = seen["params"]["query"]
    assert q.startswith("from:NASA ") and "-filter:replies" in q
    since = int(q.rsplit("since_time:", 1)[1])
    window = tc.TwitterClient.RECENT_WINDOW_HOURS * 3600
    assert t0 - window - 5 <= since <= int(time.time()) - window + 5, "окно не 48ч"
    assert seen["params"]["queryType"] == "Latest"


def test_window_never_narrower_than_two_max_poll_intervals():
    """Твит, вышедший сразу после опроса тихого источника (TTL до MAX_TTL), должен
    дожить в окне до следующего опроса — иначе пропуск по построению."""
    from core.twitter_cache import MAX_TTL_SECONDS
    assert tc.TwitterClient.RECENT_WINDOW_HOURS * 3600 >= 2 * MAX_TTL_SECONDS
    assert tc.TwitterClient.RECENT_WINDOW_HOURS == 12, "дефолт окна сменился без замера"


async def test_error_is_none_and_empty_is_empty(client, monkeypatch):
    async def err(*a, **k):
        return None

    monkeypatch.setattr(client, "_request_with_retry", err)
    assert await client.get_recent_tweets("NASA") is None

    async def empty(*a, **k):
        return {"tweets": [], "has_next_page": False, "next_cursor": ""}

    monkeypatch.setattr(client, "_request_with_retry", empty)
    assert await client.get_recent_tweets("NASA") == []


async def test_bad_handle_never_reaches_the_api(client, monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("запрос с кривым хэндлом")

    monkeypatch.setattr(client, "_request_with_retry", boom)
    assert await client.get_recent_tweets("nasa OR from:x") is None
    assert await client.get_recent_tweets("vk:club1") is None


async def test_limit_is_respected(client, monkeypatch):
    async def many(*a, **k):
        return {"tweets": [_raw(i) for i in range(30)]}

    monkeypatch.setattr(client, "_request_with_retry", many)
    assert len(await client.get_recent_tweets("NASA", limit=20)) == 20


class _FakeClient:
    def __init__(self, recent):
        self.recent = recent
        self.calls: list[str] = []

    async def get_recent_tweets(self, username, limit=20):
        self.calls.append("recent")
        return self.recent

    async def get_user_tweets(self, username, limit=20):
        self.calls.append("last")
        return [tc.Tweet.from_api(_raw(9, username))]


async def test_cache_uses_search_and_falls_back_only_on_error():
    ok = _FakeClient([tc.Tweet.from_api(_raw(1))])
    got = await TwitterCache(ok).get_tweets("@NASA")
    assert ok.calls == ["recent"] and [t.id for t in got] == ["1"]

    quiet = _FakeClient([])                       # «твитов в окне нет» — НЕ ошибка
    assert await TwitterCache(quiet).get_tweets("nasa") == []
    assert quiet.calls == ["recent"], "пустое окно ушло в дорогой last_tweets"

    broken = _FakeClient(None)                    # поиск не ответил — старый путь
    got = await TwitterCache(broken).get_tweets("nasa")
    assert broken.calls == ["recent", "last"] and [t.id for t in got] == ["9"]
