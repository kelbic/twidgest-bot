"""Дедупликация постов по теме через text-similarity.

Используется в viral_picker: перед публикацией single-поста проверяем что
он не про ту же тему что недавно публикованный.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import PostLog


# Stop-words для русского и английского — игнорируем при анализе
_STOP_WORDS = frozenset({
    # English
    "the", "and", "for", "are", "with", "this", "that", "from", "have", "has",
    "had", "was", "were", "been", "being", "into", "after", "before", "when",
    "where", "what", "which", "who", "how", "why", "will", "would", "could",
    "should", "may", "might", "must", "can", "shall", "but", "yet", "also",
    "more", "most", "very", "much", "many", "some", "such", "than", "then",
    "they", "them", "their", "there", "these", "those", "your", "yours",
    "you", "all", "any", "few", "own", "same", "too", "out", "off", "over",
    "under", "again", "just", "now", "rt",
    # Russian
    "это", "что", "как", "для", "при", "над", "под", "без", "над", "перед",
    "после", "также", "только", "если", "когда", "тогда", "потом", "уже",
    "ещё", "еще", "был", "была", "были", "было", "будет", "будут", "есть",
    "нет", "его", "её", "их", "наш", "ваш", "мой", "твой", "свой", "сам",
    "так", "вот", "там", "тут", "очень", "просто", "однако", "поэтому",
    "потому", "чтобы", "хотя", "пока", "уже", "ещё", "более", "менее",
    "будь", "являются", "стало", "стал", "стала", "автор",
})


def _extract_words(text: str) -> list[str]:
    """Из текста достаёт значимые слова (>=4 символов, не стоп-слова, не цифры)."""
    # Убираем HTML, ссылки, упоминания, эмодзи
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"@\w+", " ", text)
    text = re.sub(r"#\w+", " ", text)

    # Берём только буквы (рус + англ)
    words = re.findall(r"[А-Яа-яёЁa-zA-Z]+", text.lower())

    return [
        w for w in words
        if len(w) >= 4 and w not in _STOP_WORDS
    ]


def compute_topic_signature(text: str, max_words: int = 12) -> str:
    """Возвращает топ-N значимых слов как пробел-разделённую строку.

    Это и есть "подпись темы" — общие слова найдут одинаковые истории.
    """
    words = _extract_words(text)
    if not words:
        return ""

    # Считаем частоту, берём самые частые
    from collections import Counter
    counts = Counter(words)
    top = [w for w, _ in counts.most_common(max_words)]
    return " ".join(sorted(top))


def jaccard_similarity(sig1: str, sig2: str) -> float:
    """Сходство Жаккара двух signature-строк (0.0 - 1.0)."""
    if not sig1 or not sig2:
        return 0.0
    set1 = set(sig1.split())
    set2 = set(sig2.split())
    if not set1 or not set2:
        return 0.0
    intersection = len(set1 & set2)
    union = len(set1 | set2)
    return intersection / union if union > 0 else 0.0


async def is_duplicate_topic(
    session: AsyncSession,
    channel_id: int,
    new_text: str,
    similarity_threshold: float = 0.30,
    lookback_hours: int = 24,
    max_recent_posts: int = 10,
) -> tuple[bool, float, str | None]:
    """Проверяет является ли текст дубликатом недавнего поста по теме.

    Returns:
        (is_dup, max_similarity, matching_signature)
    """
    new_sig = compute_topic_signature(new_text)
    if not new_sig:
        return False, 0.0, None

    cutoff = datetime.utcnow() - timedelta(hours=lookback_hours)
    result = await session.execute(
        select(PostLog.topic_signature)
        .where(
            PostLog.target_id == channel_id,
            PostLog.posted_at > cutoff,
            PostLog.topic_signature != None,  # noqa: E711
        )
        .order_by(PostLog.posted_at.desc())
        .limit(max_recent_posts)
    )

    max_sim = 0.0
    matching_sig = None
    for row in result.all():
        old_sig = row[0]
        if not old_sig:
            continue
        sim = jaccard_similarity(new_sig, old_sig)
        if sim > max_sim:
            max_sim = sim
            matching_sig = old_sig
        if sim >= similarity_threshold:
            return True, sim, old_sig

    return False, max_sim, matching_sig
