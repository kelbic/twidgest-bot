"""Размышления модели не должны съедать max_tokens (31.10: 36 пустых ответов haiku-5.5 за 17ч).

Контракт: по умолчанию запрос несёт reasoning.enabled=false; модель, где размышления
обязательны (HTTP 400 «Reasoning is mandatory»), получает запрос без флага и с запасом
REASONING_HEADROOM_TOKENS — и это запоминается для следующих вызовов того же клиента.
"""
from __future__ import annotations

import pytest

from core import llm_client as lc


class _Script:
    """Подмена _single_request: очередь ответов, лог отправленных payload."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.sent: list[dict] = []

    async def __call__(self, payload):
        self.sent.append(payload)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture()
def client():
    c = lc.OpenRouterClient("k", "anthropic/claude-haiku-5.5", base_delay=0)
    return c


async def test_reasoning_disabled_by_default(client, monkeypatch):
    s = _Script(["ok"])
    monkeypatch.setattr(client, "_single_request", s)
    assert await client._call_with_retry("sys", "user", max_tokens=30) == "ok"
    assert s.sent[0]["reasoning"] == {"enabled": False}
    assert s.sent[0]["max_tokens"] == 30


async def test_mandatory_reasoning_falls_back_with_headroom_and_remembers(client, monkeypatch):
    s = _Script([lc._ReasoningMandatory("400"), "ok", "ok2"])
    monkeypatch.setattr(client, "_single_request", s)
    assert await client._call_with_retry("sys", "user", max_tokens=500) == "ok"
    retry = s.sent[1]
    assert "reasoning" not in retry, "после отказа флаг всё ещё шлётся"
    assert retry["max_tokens"] == 500 + lc.REASONING_HEADROOM_TOKENS
    # следующий вызов того же клиента — сразу без флага и с запасом, без лишнего 400
    assert await client._call_with_retry("sys", "user", max_tokens=200) == "ok2"
    assert "reasoning" not in s.sent[2]
    assert s.sent[2]["max_tokens"] == 200 + lc.REASONING_HEADROOM_TOKENS
    assert len(s.sent) == 3


async def test_transition_does_not_consume_the_only_attempt(monkeypatch):
    """Живой прогон 31.10: при max_attempts=1 переход съедал попытку → None."""
    c = lc.OpenRouterClient("k", "anthropic/claude-sonnet-5.5", max_attempts=1, base_delay=0)
    s = _Script([lc._ReasoningMandatory("400"), "ok"])
    monkeypatch.setattr(c, "_single_request", s)
    assert await c._call_with_retry("sys", "user", max_tokens=30) == "ok"


async def test_mandatory_twice_gives_up_instead_of_looping(client, monkeypatch):
    s = _Script([lc._ReasoningMandatory("400"), lc._ReasoningMandatory("400")])
    monkeypatch.setattr(client, "_single_request", s)
    assert await client._call_with_retry("sys", "user", max_tokens=30) is None
    assert len(s.sent) == 2


async def test_http_400_mandatory_body_is_classified(monkeypatch):
    """Текст отказа OpenRouter (31.10, sonnet-5.5) распознаётся как _ReasoningMandatory."""
    body = ('{"error":{"message":"Reasoning is mandatory for this endpoint and cannot be '
            'disabled.","code":400}}')

    class _Resp:
        status = 400

        async def text(self):
            return body

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Session:
        def __init__(self, *a, **k):
            pass

        def post(self, *a, **k):
            return _Resp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(lc.aiohttp, "ClientSession", _Session)
    c = lc.OpenRouterClient("k", "anthropic/claude-sonnet-5.5")
    with pytest.raises(lc._ReasoningMandatory):
        await c._single_request({"model": "x", "max_tokens": 10})
