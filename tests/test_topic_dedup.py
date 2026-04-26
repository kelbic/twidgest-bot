"""Unit-тесты на topic deduplication функции.

Не требуют LLM — чистая обработка текста.
"""
import pytest
from core.topic_dedup import (
    compute_topic_signature,
    jaccard_similarity,
    _extract_words,
)


class TestExtractWords:
    """Извлечение значимых слов из текста."""

    def test_filters_stop_words(self):
        text = "the cat and the dog were sleeping"
        words = _extract_words(text)
        # the/and/were должны быть отфильтрованы
        assert "cat" in words
        assert "dog" in words
        assert "sleeping" in words
        assert "the" not in words
        assert "were" not in words

    def test_filters_short_words(self):
        text = "AI is fun and so on"
        words = _extract_words(text)
        # Слова <4 символов отфильтрованы
        assert all(len(w) >= 4 for w in words)

    def test_strips_html(self):
        text = "<b>Tesla</b> announces new <i>model</i>"
        words = _extract_words(text)
        assert "tesla" in words
        assert "announces" in words
        assert "model" in words
        # Теги не попали в слова
        assert all("<" not in w and ">" not in w for w in words)

    def test_strips_urls(self):
        text = "Check out https://example.com/foo and t.co/abc"
        words = _extract_words(text)
        assert "example" not in words  # из URL
        assert "check" in words

    def test_strips_mentions_hashtags(self):
        text = "Hey @elonmusk what about #AI revolution"
        words = _extract_words(text)
        assert "elonmusk" not in words
        assert "revolution" in words

    def test_russian_text(self):
        text = "Турция возвращается в календарь Формулы-1 с 2027 года"
        words = _extract_words(text)
        assert "турция" in words
        assert "возвращается" in words
        assert "календарь" in words
        assert "формулы" in words  # от Формулы-1

    def test_handles_empty(self):
        assert _extract_words("") == []
        assert _extract_words("a b c") == []  # все слова короче 4
        assert _extract_words("the and") == []  # все стоп-слова


class TestComputeSignature:
    """Создание topic signature."""

    def test_returns_sorted_words(self):
        sig = compute_topic_signature("Tesla announces Tesla new Tesla model")
        # Tesla встречается 3 раза - должна быть в топе
        words = sig.split()
        assert "tesla" in words

    def test_empty_for_empty_input(self):
        assert compute_topic_signature("") == ""
        assert compute_topic_signature("the and") == ""

    def test_max_words_limit(self):
        text = " ".join(f"word{i}long" for i in range(20))
        sig = compute_topic_signature(text, max_words=5)
        assert len(sig.split()) <= 5

    def test_signatures_deterministic(self):
        text = "F1 returns to Turkey in 2027 with Istanbul Park track"
        sig1 = compute_topic_signature(text)
        sig2 = compute_topic_signature(text)
        assert sig1 == sig2


class TestJaccardSimilarity:
    """Сходство Жаккара."""

    def test_identical_signatures(self):
        sig = "tesla model autopilot"
        assert jaccard_similarity(sig, sig) == 1.0

    def test_completely_different(self):
        assert jaccard_similarity("tesla autopilot", "manicure pink") == 0.0

    def test_partial_overlap(self):
        # 2 общих из 4 уникальных = 2/4 = 0.5
        sim = jaccard_similarity("tesla autopilot model", "tesla autopilot wheel")
        assert sim == pytest.approx(0.5, abs=0.01)

    def test_empty_signatures(self):
        assert jaccard_similarity("", "tesla") == 0.0
        assert jaccard_similarity("tesla", "") == 0.0
        assert jaccard_similarity("", "") == 0.0


class TestRealWorldScenarios:
    """Реальные кейсы дедупликации."""

    def test_turkey_f1_dup(self):
        """Главный баг: 4 поста про Турцию F1 от разных авторов."""
        post1 = "Турция вернётся в календарь Формулы-1 с 2027 года на пять сезонов. Гран-при Istanbul Park снова будет частью чемпионата после перерыва."
        post2 = "Турция возвращается в календарь Формулы-1 с 2027 года! Istanbul Park подарил болельщикам немало ярких моментов."
        post3 = "Льюис Хэмилтон завоевал свой седьмой титул чемпиона мира на Гран-при Турции 2020 года."

        sig1 = compute_topic_signature(post1)
        sig2 = compute_topic_signature(post2)
        sig3 = compute_topic_signature(post3)

        # 1 vs 2 — те же события, должны иметь высокое сходство
        sim_1_2 = jaccard_similarity(sig1, sig2)
        assert sim_1_2 >= 0.35, (
            f"Posts about same event should match (sim={sim_1_2:.2f}, "
            f"sig1={sig1!r}, sig2={sig2!r})"
        )

        # 1 vs 3 — про разные F1-события (возвращение vs Хэмилтон)
        # Должно быть НИЖЕ порога чтобы пройти как разные новости
        sim_1_3 = jaccard_similarity(sig1, sig3)
        assert sim_1_3 < 0.50, (
            f"Different F1 events should NOT be too similar (sim={sim_1_3:.2f})"
        )

    def test_different_news_pass(self):
        """Разные новости должны легко пройти."""
        ai_news = "OpenAI выпустила GPT-5 — новую модель с улучшенным reasoning"
        sport_news = "Реал Мадрид выиграл Лигу Чемпионов в финале против Манчестер Сити"

        sig_ai = compute_topic_signature(ai_news)
        sig_sport = compute_topic_signature(sport_news)

        sim = jaccard_similarity(sig_ai, sig_sport)
        assert sim < 0.20
