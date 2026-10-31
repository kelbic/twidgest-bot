"""Опрос источника по темпу публикации его каналов (31.10).

Темп выбирает владелец (/setinterval). Проверяется контракт: hybrid — половина пейсинга
в [30 мин, 3ч], digest — половина интервала дайджеста, single — как раньше (30 мин),
общий источник берёт минимум, VK и выключенные источники не участвуют; кэш уважает
базовый TTL источника.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

from core import twitter_client as tc
from core.twitter_cache import TwitterCache
from workers import collector as col


def _src(u, active=True, kind="twitter"):
    return SimpleNamespace(twitter_username=u, is_active=active, source_type=kind)


def _ch(mode, pacing=None, digest_h=12, sources=()):
    return SimpleNamespace(mode=mode, pacing=pacing, digest_interval_hours=digest_h,
                           channel_sources=list(sources))


def _patch_pacing(monkeypatch):
    import workers.viral_picker as vp
    monkeypatch.setattr(vp, "pacing_minutes", lambda ch: ch.pacing)


def test_ttl_follows_channel_pacing(monkeypatch):
    _patch_pacing(monkeypatch)
    m = col.source_poll_ttl([
        _ch("hybrid", pacing=240, sources=[_src("NASA")]),          # 4ч → 2ч
        _ch("hybrid", pacing=60, sources=[_src("openai")]),         # 1ч → 30 мин
        _ch("hybrid", pacing=15, sources=[_src("fresh")]),          # активация → пол 30 мин
        _ch("digest", digest_h=12, sources=[_src("digester")]),     # 12ч → потолок 3ч
        _ch("single", sources=[_src("instant")]),                   # как раньше
    ])
    assert m == {"nasa": 7200, "openai": 1800, "fresh": 1800, "digester": 10800, "instant": 1800}


def test_shared_source_takes_the_fastest_channel(monkeypatch):
    _patch_pacing(monkeypatch)
    m = col.source_poll_ttl([
        _ch("hybrid", pacing=240, sources=[_src("shared")]),
        _ch("hybrid", pacing=60, sources=[_src("Shared")]),
    ])
    assert m == {"shared": 1800}


def test_vk_and_inactive_sources_are_ignored(monkeypatch):
    _patch_pacing(monkeypatch)
    m = col.source_poll_ttl([_ch("hybrid", pacing=240, sources=[
        _src("vk:club1", kind="vk"), _src("off", active=False), _src("on")])])
    assert m == {"on": 7200}


class _Counting:
    def __init__(self):
        self.calls = 0

    async def get_recent_tweets(self, username, limit=20):
        self.calls += 1
        return [tc.Tweet.from_api({"id": str(self.calls), "text": "x" * 30,
                                   "author": {"userName": username},
                                   "createdAt": time.strftime("%a %b %d %H:%M:%S +0000 %Y", time.gmtime())})]

    async def get_user_tweets(self, username, limit=20):
        raise AssertionError("не должен откатываться")


async def test_cache_respects_per_source_base_ttl(monkeypatch):
    client = _Counting()
    cache = TwitterCache(client, ttl_seconds=1800)
    await cache.get_tweets("nasa", base_ttl=7200)
    assert client.calls == 1
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 3600)   # +1ч: общий TTL истёк бы
    await cache.get_tweets("nasa", base_ttl=7200)
    assert client.calls == 1, "опрос раньше базового TTL источника"
    monkeypatch.setattr(time, "time", lambda: real() + 7300)   # +2ч с запасом
    await cache.get_tweets("nasa", base_ttl=7200)
    assert client.calls == 2
    assert cache.stats()["ttl_distribution_seconds"] == {7200: 1}
