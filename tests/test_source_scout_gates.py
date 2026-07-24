"""Гейты скаута: активность и отдача.

Главный кейс владельца: популярный, но редко пишущий автор не должен
доходить до карточки — канал с таким источником молчит.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from workers.source_scout import (
    MIN_EST_POSTS_PER_WEEK,
    MIN_TWEETS_PER_WEEK,
    CandidateStats,
    apply_topic_relevance,
    prevalidate_candidates,
)


@dataclass
class FakeTweet:
    parsed_created_at: datetime
    text: str = "x" * 60
    is_reply: bool = False
    likes: int = 100
    retweets: int = 10


class FakeCache:
    def __init__(self, tweets_by_user: dict[str, list[FakeTweet]]):
        self.tweets_by_user = tweets_by_user

    async def get_tweets(self, username: str, limit: int = 20) -> list[FakeTweet]:
        return self.tweets_by_user.get(username, [])


def _tweets(count: int, span_days: float, likes: int = 100) -> list[FakeTweet]:
    """count твитов, равномерно размазанных на span_days назад от «сейчас»."""
    now = datetime.utcnow()
    step = span_days / max(count - 1, 1)
    return [
        FakeTweet(parsed_created_at=now - timedelta(days=step * i), likes=likes)
        for i in range(count)
    ]


class FakeLLM:
    def __init__(self, response: str | None):
        self.response = response

    async def _call_with_retry(self, *args, **kwargs) -> str | None:
        return self.response


async def test_popular_but_quiet_author_dropped():
    # 10 твитов за 60 дней ≈ 1 тв/нед — знаменитость, но канал будет молчать
    cache = FakeCache({"celebrity": _tweets(10, 60, likes=50_000)})
    results = await prevalidate_candidates(
        [("celebrity", "очень известный")], min_likes=10, min_retweets=0, cache=cache
    )
    assert results == []


async def test_active_author_passes():
    # 20 твитов за 14 дней ≈ 9.5 тв/нед, все проходят пороги
    cache = FakeCache({"worker": _tweets(20, 14, likes=500)})
    results = await prevalidate_candidates(
        [("worker", "активный")], min_likes=10, min_retweets=0, cache=cache
    )
    assert len(results) == 1
    assert results[0].tweets_per_week >= MIN_TWEETS_PER_WEEK
    assert results[0].est_posts_per_week >= MIN_EST_POSTS_PER_WEEK


async def test_low_yield_author_dropped():
    # Пишет часто (3.5 тв/нед), но пороги канала проходят только 2 из 20:
    # отдача 0.35 поста/нед — канал он не прокормит
    tweets = _tweets(18, 38, likes=10) + _tweets(2, 1, likes=200)
    cache = FakeCache({"lowyield": tweets})
    results = await prevalidate_candidates(
        [("lowyield", "почти всё мимо порогов")],
        min_likes=100, min_retweets=0, cache=cache,
    )
    assert results == []


def _candidate(est: float) -> CandidateStats:
    return CandidateStats(
        username="author", reason="", total=20, text_share=0.8,
        median_likes=100, passing=10, tweets_per_week=5.0,
        est_posts_per_week=est, sample_texts=["t1", "t2"],
    )


async def test_topic_share_kills_low_on_topic_yield():
    # Тема подтверждена на 50% (>= MIN_TOPIC_SHARE), но отдача по теме
    # падает ниже минимума — кандидат выпадает
    llm = FakeLLM('{"author": 50}')
    kept = await apply_topic_relevance(llm, "тема", [_candidate(est=1.5)])
    assert kept == []


async def test_topic_share_keeps_good_yield():
    llm = FakeLLM('{"author": 50}')
    kept = await apply_topic_relevance(llm, "тема", [_candidate(est=4.0)])
    assert len(kept) == 1
    assert kept[0].est_posts_per_week == 2.0


async def test_topic_llm_failure_fails_open():
    llm = FakeLLM(None)
    kept = await apply_topic_relevance(llm, "тема", [_candidate(est=1.5)])
    assert len(kept) == 1  # fail-open: без LLM кандидата не наказываем
