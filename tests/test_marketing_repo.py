"""БД-слой и правила, добавленные по итогам ревью: атомарность капов,
воронка по журналу событий, валидатор черновиков, сетевой сбой превью."""
from __future__ import annotations

import asyncio

import pytest

from marketing import repo
from marketing.copywriter import validate_draft
from marketing.enrich import TelegramPreviewClient


@pytest.fixture
async def session_maker(tmp_path):
    """Изолированная файловая SQLite на тест (in-memory даёт по БД на коннект)."""
    import marketing.db as mdb
    mdb.init_engine(f"sqlite+aiosqlite:///{tmp_path}/test_marketing.db")
    await mdb.init_db()
    yield mdb.session_maker()
    mdb._engine = mdb._session_maker = None


class TestTryConsume:
    async def test_respects_cap(self, session_maker):
        async with session_maker() as s:
            for _ in range(3):
                assert await repo.try_consume(s, "llm_calls", cap=3) is True
            assert await repo.try_consume(s, "llm_calls", cap=3) is False
            assert await repo.get_counter(s, "llm_calls") == 3

    async def test_concurrent_consumers_never_exceed_cap(self, session_maker):
        """Гонка воркера и колбэка: UPSERT + условный UPDATE, не check-then-act."""
        async def consume():
            async with session_maker() as s:
                return await repo.try_consume(s, "cards", cap=5)

        results = await asyncio.gather(*(consume() for _ in range(12)))
        assert sum(results) == 5
        async with session_maker() as s:
            assert await repo.get_counter(s, "cards") == 5


class TestFunnelReached:
    async def test_dead_lead_still_counts_as_reached(self, session_maker):
        """Лид, умерший после contacted, прошёл contacted — знаменатель не тает."""
        async with session_maker() as s:
            lead1, _ = await repo.add_lead(s, "alpha")
            lead2, _ = await repo.add_lead(s, "beta")
            for lead in (lead1, lead2):
                await repo.set_status(s, lead, "contacted")
            await repo.set_status(s, lead1, "replied")
            await repo.set_status(s, lead2, "dead")

            reached = await repo.funnel_reached(s, ["contacted", "replied"])
        assert reached == {"contacted": 2, "replied": 1}


class TestAddLead:
    async def test_dedup_and_blacklist(self, session_maker):
        async with session_maker() as s:
            _, first = await repo.add_lead(s, "@SomeChan", "AI")
            _, second = await repo.add_lead(s, "somechan")
            await repo.blacklist_add(s, "spammer", "optout")
            _, third = await repo.add_lead(s, "spammer")
        assert (first, second, third) == ("created", "duplicate", "blacklisted")


class TestValidateDraft:
    GOOD = (
        "Заметил ваш канал про Формулу-1 — в мае выходило по шесть разборов, "
        "в июне только два. Я сделал бота, который сам собирает виральные посты "
        "из X по нише и переписывает на русский. Прислать демо по Ф1?"
    )

    def test_accepts_good_draft(self):
        assert validate_draft(self.GOOD) == self.GOOD

    @pytest.mark.parametrize("link", ["https://x.com", "www.twidgest.ru", "t.me/bot"])
    def test_rejects_any_link_form(self, link):
        assert validate_draft(self.GOOD + " " + link) is None

    def test_rejects_wrong_length(self):
        assert validate_draft("коротко") is None
        assert validate_draft("х" * 900) is None
        assert validate_draft(None) is None


class TestPreviewFetchFailure:
    async def test_network_failure_flagged_not_no_preview(self, monkeypatch):
        client = TelegramPreviewClient()

        async def fake_fetch(username):
            return None, True
        monkeypatch.setattr(client, "fetch_html", fake_fetch)

        preview = await client.fetch_preview("somechan")
        assert preview.fetch_failed is True
        assert preview.available is False

    async def test_failures_not_cached(self, monkeypatch):
        """Сбой не должен на 24ч приклеить каналу ярлык «без превью»."""
        client = TelegramPreviewClient()
        calls = []

        class FakeResp:
            status = 500
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                return False

        class FakeSession:
            closed = False
            def get(self, url, **kw):
                calls.append(url)
                return FakeResp()

        monkeypatch.setattr(client, "_http", lambda: FakeSession())
        monkeypatch.setattr("marketing.enrich.MIN_REQUEST_INTERVAL", 0)

        first = await client.fetch_html("somechan")
        second = await client.fetch_html("somechan")
        assert first == (None, True) and second == (None, True)
        assert len(calls) == 4  # 2 попытки × 2 вызова: кэш None не появился
