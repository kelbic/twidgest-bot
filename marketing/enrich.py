"""Обогащение лида по публичному веб-превью t.me/s/<username>.

Обычный HTTPS GET без авторизации (это НЕ userbot). Превью отдаёт ~20
последних постов: даты, подписчики, описание. Поверх дат считаем метрики,
главная из которых — ТРАЕКТОРИЯ кадэнса (decay): выгорающий админ постит
не рвано, а всё реже.

Вежливость: ≤1 запрос/сек, кэш ответа 24ч, таймаут 10с, один ретрай.
"""
from __future__ import annotations

import asyncio
import html as html_lib
import logging
import re
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import aiohttp

logger = logging.getLogger(__name__)

PREVIEW_URL = "https://t.me/s/{username}"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) TwidgestMarketingScout/1.0"
REQUEST_TIMEOUT = 10
CACHE_TTL_SECONDS = 24 * 3600
MIN_REQUEST_INTERVAL = 1.0  # секунд между запросами к t.me

_MSG_MARKER = "tgme_widget_message"
_TIME_RE = re.compile(r'<time datetime="([^"]+)"')
_TITLE_RE = re.compile(
    r'tgme_channel_info_header_title[^>]*>(.*?)</(?:div|span)>', re.S
)
_DESC_RE = re.compile(r'tgme_channel_info_description[^>]*>(.*?)</div>', re.S)
_SUBS_RE = re.compile(
    r'<span class="counter_value">([^<]+)</span>\s*<span class="counter_type">subscriber',
)
_TAG_RE = re.compile(r"<[^>]+>")
_CONTACT_RE = re.compile(r"@([A-Za-z][A-Za-z0-9_]{3,31})")
_MSG_TEXT_RE = re.compile(
    r'tgme_widget_message_text[^>]*>(.*?)</div>', re.S
)


@dataclass
class ChannelPreview:
    """Что удалось вытащить из t.me/s/<username>."""

    username: str
    available: bool = False           # False = приватный канал / без превью
    fetch_failed: bool = False        # True = сеть/5xx: НЕ «нет превью», можно ретраить
    title: str | None = None
    description: str | None = None
    subscribers: int | None = None
    contact: str | None = None        # @админа из описания, если найден
    post_dates: list[datetime] = field(default_factory=list)  # naive UTC, asc
    post_texts: list[str] = field(default_factory=list)       # свежие сверху


@dataclass
class CadenceMetrics:
    """Метрики поверх дат постов."""

    posts_per_week: float | None = None
    median_gap_hours: float | None = None
    gap_cv: float | None = None       # вторичный сигнал: человек vs автопостер
    varied_hours: bool = False        # посты в разное время суток
    last_post_at: datetime | None = None
    recent_ppw: float | None = None
    older_ppw: float | None = None
    decay: float | None = None        # ГЛАВНЫЙ сигнал: recent_ppw / older_ppw


def _strip_tags(fragment: str) -> str:
    text = re.sub(r"<br\s*/?>", " ", fragment)
    text = _TAG_RE.sub("", text)
    return html_lib.unescape(text).strip()


def _parse_subscriber_count(raw: str) -> int | None:
    """'11.7M' / '45.2K' / '5 432' / '1234' → int."""
    clean = raw.strip().replace(" ", "").replace(" ", "").replace(" ", "")
    clean = clean.replace(",", ".")
    mult = 1
    if clean[-1:].upper() == "K":
        mult, clean = 1_000, clean[:-1]
    elif clean[-1:].upper() == "M":
        mult, clean = 1_000_000, clean[:-1]
    try:
        return int(float(clean) * mult)
    except ValueError:
        return None


def parse_preview_html(page_html: str, username: str) -> ChannelPreview:
    """Парсит HTML превью. Без BeautifulSoup — стабильные маркеры + regex."""
    preview = ChannelPreview(username=username.lstrip("@").lower())

    if _MSG_MARKER not in page_html:
        # Канал приватный, без превью, либо это страница-заглушка t.me/<u>
        return preview

    m = _TITLE_RE.search(page_html)
    if m:
        preview.title = _strip_tags(m.group(1))[:256] or None

    m = _DESC_RE.search(page_html)
    if m:
        preview.description = _strip_tags(m.group(1))[:2000] or None

    m = _SUBS_RE.search(page_html)
    if m:
        preview.subscribers = _parse_subscriber_count(m.group(1))

    # Контакт админа: первый @username из описания, не совпадающий с каналом
    if preview.description:
        for candidate in _CONTACT_RE.findall(preview.description):
            if candidate.lower() != preview.username:
                preview.contact = f"@{candidate}"
                break

    dates: list[datetime] = []
    for raw in _TIME_RE.findall(page_html):
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            continue
        if dt.tzinfo is not None:
            dt = (dt - dt.utcoffset()).replace(tzinfo=None)
        dates.append(dt)
    preview.post_dates = sorted(dates)

    texts = [_strip_tags(t) for t in _MSG_TEXT_RE.findall(page_html)]
    # Свежие посты — в конце страницы; для сэмплов удобнее свежие сверху
    preview.post_texts = [t for t in reversed(texts) if t][:8]

    preview.available = bool(preview.post_dates)
    return preview


def compute_metrics(
    dates: list[datetime], now: datetime | None = None
) -> CadenceMetrics:
    """Метрики кадэнса по датам постов (naive UTC, любой порядок).

    decay: если выборка покрывает ≥60 дней — окна [now-30d, now] vs
    [now-60d, now-30d]. Иначе fallback — сравнение половин временного
    диапазона выборки. Суть неизменна: ищем ЗАМЕДЛЯЮЩИХСЯ, а не рваных.
    """
    now = now or datetime.utcnow()
    metrics = CadenceMetrics()
    if not dates:
        return metrics

    dates = sorted(dates)
    metrics.last_post_at = dates[-1]
    if len(dates) < 2:
        return metrics

    # Интервалы между постами, floor 1 минута
    gaps = [
        max((b - a).total_seconds() / 3600.0, 1.0 / 60.0)
        for a, b in zip(dates, dates[1:])
    ]
    span_days = max((dates[-1] - dates[0]).total_seconds() / 86400.0, 0.05)
    metrics.posts_per_week = (len(dates) - 1) / span_days * 7.0
    metrics.median_gap_hours = statistics.median(gaps)
    mean_gap = statistics.mean(gaps)
    if len(gaps) >= 2 and mean_gap > 0:
        metrics.gap_cv = statistics.pstdev(gaps) / mean_gap
    metrics.varied_hours = len({d.hour for d in dates}) >= 5

    # --- ГЛАВНАЯ метрика: траектория кадэнса ---
    covers_60d = dates[0] <= now - timedelta(days=60)
    if covers_60d:
        recent = [d for d in dates if d > now - timedelta(days=30)]
        older = [
            d for d in dates
            if now - timedelta(days=60) < d <= now - timedelta(days=30)
        ]
        metrics.recent_ppw = len(recent) / 30.0 * 7.0
        metrics.older_ppw = len(older) / 30.0 * 7.0
    else:
        # Fallback: две половины временного диапазона выборки (равной длины)
        mid = dates[0] + (dates[-1] - dates[0]) / 2
        half_days = max(span_days / 2.0, 0.05)
        newer = [d for d in dates if d > mid]
        older_half = [d for d in dates if d <= mid]
        metrics.recent_ppw = len(newer) / half_days * 7.0
        metrics.older_ppw = len(older_half) / half_days * 7.0

    if metrics.older_ppw and metrics.older_ppw > 0:
        metrics.decay = metrics.recent_ppw / metrics.older_ppw
    elif metrics.recent_ppw and metrics.recent_ppw > 0:
        metrics.decay = 2.0  # раньше молчал, теперь постит — растущий, не выгорающий
    else:
        metrics.decay = None
    return metrics


class TelegramPreviewClient:
    """HTTP-клиент превью: throttle 1 rps, кэш 24ч, таймаут 10с, один ретрай.

    Кэшируются только УСПЕШНЫЕ ответы: транзиентный таймаут/5xx не должен на
    сутки приклеить каналу ярлык «без превью» — такой лид остаётся в new и
    ретраится следующим циклом.
    """

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, str]] = {}
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        self._session: aiohttp.ClientSession | None = None

    def _http(self) -> aiohttp.ClientSession:
        # Одна долгоживущая сессия: keep-alive к t.me вместо TLS-рукопожатия
        # на каждый лид (fetch'и и так сериализованы _lock'ом)
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": USER_AGENT},
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def fetch_html(self, username: str) -> tuple[str | None, bool]:
        """(html | None, fetch_failed). failed=True — сеть/5xx, не кэшируется."""
        username = username.lstrip("@").lower()
        cached = self._cache.get(username)
        if cached and time.monotonic() - cached[0] < CACHE_TTL_SECONDS:
            return cached[1], False

        html_text: str | None = None
        failed = False
        async with self._lock:
            for attempt in (1, 2):
                wait = MIN_REQUEST_INTERVAL - (time.monotonic() - self._last_request)
                if wait > 0:
                    await asyncio.sleep(wait)
                self._last_request = time.monotonic()
                try:
                    async with self._http().get(
                        PREVIEW_URL.format(username=username),
                        allow_redirects=True,
                    ) as resp:
                        if resp.status == 200:
                            html_text = await resp.text()
                            failed = False
                            break
                        logger.info(
                            "t.me/s/%s → HTTP %d (attempt %d)",
                            username, resp.status, attempt,
                        )
                        if resp.status < 500:
                            failed = False
                            break  # 4xx — окончательный ответ, ретраить бессмысленно
                        failed = True
                except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    failed = True
                    logger.info(
                        "t.me/s/%s fetch failed (attempt %d): %s",
                        username, attempt, exc,
                    )

        if html_text is not None:
            self._cache[username] = (time.monotonic(), html_text)
        return html_text, failed

    async def fetch_preview(self, username: str) -> ChannelPreview:
        html_text, failed = await self.fetch_html(username)
        if not html_text:
            return ChannelPreview(
                username=username.lstrip("@").lower(), fetch_failed=failed
            )
        return parse_preview_html(html_text, username)
