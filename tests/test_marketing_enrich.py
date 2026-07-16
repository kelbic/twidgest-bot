"""Парсер t.me/s/ и метрики кадэнса — на сохранённых HTML-снапшотах, без сети."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from marketing.enrich import (
    ChannelPreview,
    compute_metrics,
    parse_preview_html,
    _parse_subscriber_count,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestParsePreview:
    def test_normal_channel(self):
        preview = parse_preview_html(_load("tme_snapshot_normal.html"), "@F1Gonzo")

        assert preview.available is True
        assert preview.username == "f1gonzo"
        assert preview.title == "Formula Gonzo — Ф1 по-русски"
        assert preview.subscribers == 1234
        assert "Гонки, паддок и стратегия" in preview.description

    def test_normal_channel_dates(self):
        preview = parse_preview_html(_load("tme_snapshot_normal.html"), "f1gonzo")

        assert len(preview.post_dates) == 10
        # Отсортированы по возрастанию, ISO-даты сконвертированы в naive UTC
        assert preview.post_dates[0] == datetime(2026, 4, 20, 9, 15)
        assert preview.post_dates[-1] == datetime(2026, 7, 1, 10, 30)
        assert all(d.tzinfo is None for d in preview.post_dates)

    def test_contact_extracted_from_description(self):
        preview = parse_preview_html(_load("tme_snapshot_normal.html"), "f1gonzo")
        # @админа из описания; @f1gonzo (сам канал) контактом не считается
        assert preview.contact == "@gonzo_admin"

    def test_post_texts_sampled_fresh_first(self):
        preview = parse_preview_html(_load("tme_snapshot_normal.html"), "f1gonzo")
        assert preview.post_texts
        assert preview.post_texts[0].startswith("Канада: главный вопрос")

    def test_no_preview_channel(self):
        preview = parse_preview_html(_load("tme_snapshot_nopreview.html"), "privatechan")

        assert preview.available is False
        assert preview.post_dates == []

    def test_garbage_html(self):
        preview = parse_preview_html("<html><body>nothing here</body></html>", "x12345")
        assert preview.available is False


class TestSubscriberCount:
    def test_plain_and_spaced(self):
        assert _parse_subscriber_count("1234") == 1234
        assert _parse_subscriber_count("1 234") == 1234

    def test_suffixes(self):
        assert _parse_subscriber_count("11.7M") == 11_700_000
        assert _parse_subscriber_count("45.2K") == 45_200
        assert _parse_subscriber_count("2K") == 2000

    def test_garbage(self):
        assert _parse_subscriber_count("subscribers") is None


class TestComputeMetrics:
    NOW = datetime(2026, 7, 3, 12, 0)

    def test_empty_and_single(self):
        assert compute_metrics([], now=self.NOW).last_post_at is None
        single = compute_metrics([self.NOW - timedelta(days=1)], now=self.NOW)
        assert single.last_post_at == self.NOW - timedelta(days=1)
        assert single.decay is None

    def test_burning_channel_decay_from_fixture(self):
        """Снапшот f1gonzo — выгорающий: апрель плотный, июнь-июль редкий."""
        preview = parse_preview_html(_load("tme_snapshot_normal.html"), "f1gonzo")
        metrics = compute_metrics(preview.post_dates, now=self.NOW)

        assert metrics.last_post_at == datetime(2026, 7, 1, 10, 30)
        # Окна: последние 30 дн — 2 поста, предыдущие 30 дн — ~5: кадэнс валится
        assert metrics.decay is not None
        assert metrics.decay < 0.6
        assert metrics.posts_per_week is not None

    def test_fallback_halves_when_window_short(self):
        """20 постов за 20 дней (окно < 60 дн) → сравнение половин выборки."""
        dates = []
        # Старая половина (дни 0..10): плотно, 8 постов
        for i in range(8):
            dates.append(self.NOW - timedelta(days=20) + timedelta(days=i * 1.2))
        # Новая половина (дни 10..20): редко, 2 поста
        dates.append(self.NOW - timedelta(days=6))
        dates.append(self.NOW - timedelta(days=1))
        metrics = compute_metrics(dates, now=self.NOW)

        assert metrics.decay is not None
        assert metrics.decay < 0.6  # замедляющийся и на коротком окне ловится

    def test_stable_channel_decay_around_one(self):
        """Равномерные 3 поста/нед за 70 дней → decay ≈ 1."""
        dates = [self.NOW - timedelta(days=70) + timedelta(days=i * 7 / 3)
                 for i in range(30)]
        metrics = compute_metrics(dates, now=self.NOW)
        assert metrics.decay is not None
        assert 0.8 <= metrics.decay <= 1.2

    def test_gap_floor_one_minute(self):
        """Два поста в одну секунду не роняют деление на ноль."""
        d = self.NOW - timedelta(days=1)
        metrics = compute_metrics([d, d, d + timedelta(hours=5)], now=self.NOW)
        assert metrics.median_gap_hours is not None
