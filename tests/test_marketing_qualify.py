"""Эвристики скоринга и LLM-часть квалификации (LLM мокается, без сети).

Модель покупателя из ТЗ: максимальный скор — у ВЫГОРАЮЩЕГО (жив, decay<0.6);
событийная рваность (высокий cv при decay≈1) бонуса НЕ даёт.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from marketing.enrich import CadenceMetrics
from marketing.qualify import (
    MIN_QUALIFY_SCORE,
    LlmVerdict,
    combine_score,
    llm_qualify_batch,
    score_lead_heuristics,
)

NOW = datetime(2026, 7, 3, 12, 0)


def _metrics(
    last_days_ago: float = 2,
    decay: float | None = 0.4,
    recent_ppw: float = 1.5,
    older_ppw: float = 5.0,
    gap_cv: float | None = 1.5,
    varied_hours: bool = True,
) -> CadenceMetrics:
    return CadenceMetrics(
        posts_per_week=recent_ppw,
        median_gap_hours=48.0,
        gap_cv=gap_cv,
        varied_hours=varied_hours,
        last_post_at=NOW - timedelta(days=last_days_ago),
        recent_ppw=recent_ppw,
        older_ppw=older_ppw,
        decay=decay,
    )


def _score(metrics: CadenceMetrics, subs=1500, niche="F1", contact="@adm") -> int:
    return score_lead_heuristics(metrics, subs, niche, contact, now=NOW).score


class TestHeuristics:
    def test_burning_admin_gets_max_score(self):
        """Жив + decay<0.6 — лучший лид, скор выше всех остальных сортов."""
        burning = _score(_metrics(last_days_ago=2, decay=0.3))
        stable = _score(_metrics(decay=1.0, recent_ppw=3, older_ppw=3))
        dead = _score(_metrics(last_days_ago=30, decay=0.3))

        assert burning > stable > dead
        assert burning >= 80
        verdict = score_lead_heuristics(
            _metrics(decay=0.3), 1500, "F1", "@adm", now=NOW
        )
        assert any("выгорающ" in r for r in verdict.reasons)

    def test_event_driven_channel_no_burst_bonus(self):
        """F1-уик-энды: высокий cv, но decay≈1 — рваность скор НЕ повышает."""
        event_driven = _metrics(decay=1.0, recent_ppw=4, older_ppw=4,
                                gap_cv=2.5, varied_hours=True)
        smooth_stable = _metrics(decay=1.0, recent_ppw=4, older_ppw=4,
                                 gap_cv=0.3, varied_hours=False)
        assert _score(event_driven) == _score(smooth_stable)

    def test_cv_bonus_only_with_falling_decay(self):
        """Тот же cv при падающем decay — бонус есть."""
        burning_human = _metrics(decay=0.3, gap_cv=2.5, varied_hours=True)
        burning_bot = _metrics(decay=0.3, gap_cv=0.2, varied_hours=False)
        assert _score(burning_human) == _score(burning_bot) + 10

    def test_stable_manual_moderate(self):
        """Стабильный ручной (decay≈1) — умеренный скор: сорт (а)/(б)."""
        score = _score(_metrics(decay=0.95, recent_ppw=3, older_ppw=3, gap_cv=1.5))
        assert MIN_QUALIFY_SCORE <= score < 80

    def test_dead_channel_disqualified(self):
        """Последний пост 30 дн назад — уже бросил, поздно."""
        score = _score(_metrics(last_days_ago=30, decay=0.2))
        assert score < MIN_QUALIFY_SCORE

    def test_no_posts_at_all(self):
        score = score_lead_heuristics(CadenceMetrics(), 1500, "AI", None, now=NOW).score
        assert score < MIN_QUALIFY_SCORE

    def test_tiny_channel_penalty(self):
        base = _score(_metrics(), subs=1500)
        tiny = _score(_metrics(), subs=150)
        assert tiny == base - 20

    def test_huge_channel_penalty(self):
        base = _score(_metrics(), subs=1500)
        huge = _score(_metrics(), subs=50_000)
        assert huge == base - 30

    def test_contact_bonus(self):
        with_contact = _score(_metrics(), contact="@adm")
        without = _score(_metrics(), contact=None)
        assert with_contact == without + 10

    def test_strong_vs_regional_niche(self):
        strong = _score(_metrics(), niche="AI news")
        regional = _score(_metrics(), niche="Новости Пермского края")
        neutral = _score(_metrics(), niche="вязание")
        assert strong == neutral + 15
        assert regional == neutral - 25


# --------------------------------------------------------------------------- #
# LLM-часть: мок, fail-open
# --------------------------------------------------------------------------- #

class FakeLLM:
    def __init__(self, response: str | None):
        self.response = response
        self.calls = 0

    async def call(self, system, user, max_tokens=800, temperature=0.3):
        self.calls += 1
        return self.response


LEADS_DATA = [{
    "username": "f1gonzo",
    "description": "Гонки и паддок",
    "samples": ["пост раз", "пост два"],
    "metrics_line": "1.5 пост/нед, decay=0.3",
}]


class TestLlmQualify:
    @pytest.mark.asyncio
    async def test_parses_valid_json(self):
        llm = FakeLLM(
            '{"f1gonzo": {"niche_guess": "Формула-1", "fit_0_100": 90,'
            ' "hook": "весной постил ежедневно, сейчас пара в неделю"}}'
        )
        verdicts = await llm_qualify_batch(llm, LEADS_DATA)
        assert verdicts["f1gonzo"].fit == 90
        assert verdicts["f1gonzo"].niche_guess == "Формула-1"
        assert "пара в неделю" in verdicts["f1gonzo"].hook

    @pytest.mark.asyncio
    async def test_fail_open_on_llm_failure(self):
        """LLM упал (None) → пустой dict, квалификация идёт по эвристикам."""
        verdicts = await llm_qualify_batch(FakeLLM(None), LEADS_DATA)
        assert verdicts == {}

    @pytest.mark.asyncio
    async def test_fail_open_on_garbage_json(self):
        verdicts = await llm_qualify_batch(FakeLLM("ну тут сложно сказать..."), LEADS_DATA)
        assert verdicts == {}

    @pytest.mark.asyncio
    async def test_markdown_fences_stripped(self):
        llm = FakeLLM('```json\n{"f1gonzo": {"niche_guess": "F1", "fit_0_100": 70, "hook": "x"}}\n```')
        verdicts = await llm_qualify_batch(llm, LEADS_DATA)
        assert verdicts["f1gonzo"].fit == 70


class TestCombineScore:
    def test_no_fit_keeps_heuristics(self):
        assert combine_score(80, None) == 80

    def test_fit_blends(self):
        assert combine_score(80, 100) == 85
        assert combine_score(80, 0) == 60

    def test_clamped(self):
        assert 0 <= combine_score(0, 0) <= combine_score(100, 100) <= 100
