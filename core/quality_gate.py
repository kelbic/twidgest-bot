"""Quality Gate v1 для single-постов и дайджестов.

Три детерминированных проверки поверх LLM (LLM-часть Substance Gate живёт в ранкере,
core/candidate_ranker.Verdict.substance):

1. Substance Gate — пост-чек текста: формулировки-заглушки «подробностей в твите нет»,
   «автор не уточнил» и т.п. означают пустой тизер → пост не публикуется отдельно
   (rejected_low_substance). В дайджесте такие пункты вырезаются, а не валят весь выпуск.
2. Media Integrity — медиа первоисточника важнее стока; картинка не повторяется в
   последних MEDIA_WINDOW постах канала; между стоковыми картинками — минимум
   STOCK_COOLDOWN постов; нет подходящего визуала → пост уходит текстом.
3. Visual Claim Alignment — заголовок/первый абзац обещает фото/снимок/видео, а
   оригинального медиа нет → один rewrite-вызов убирает обещание; если суть новости
   только в картинке (LLM ответил SKIP) → rejected_missing_media.
"""
from __future__ import annotations

import html as _html
import logging
import re
from dataclasses import dataclass
from typing import Callable

from sqlalchemy import select

from core.image_picker import fetch_image_url_for_keywords, media_fingerprint
from db.models import PostLog

logger = logging.getLogger(__name__)

MEDIA_WINDOW = 20      # анти-дубль: последние N постов канала
STOCK_COOLDOWN = 5     # минимум постов между стоковыми картинками в канале

REJECT_LOW_SUBSTANCE = "rejected_low_substance"
REJECT_MISSING_MEDIA = "rejected_missing_media"

# ── Substance Gate: формулировки-заглушки ────────────────────────────────────────────
# Сильные — признание источника, что фактуры нет: бракуют всегда (хороший пост не
# рассказывает, чего в твите нет). Слабая «пока неизвестно» законна в фактурной
# новости («дата релиза пока неизвестна, но модель набрала 92%»), поэтому бракует
# только текст без единой цифры — признак пустого тизера.
_NEG = r"(?:нет|не\s+(?:приводит|раскрыва|сообща|указ|уточн|дал|даёт|дает|называ))"
_TEASER_STRONG = [re.compile(p, re.I) for p in (
    rf"подробност\w*[^.!?\n]{{0,60}}?\b{_NEG}",
    rf"детал\w*[^.!?\n]{{0,60}}?\b{_NEG}",
    r"автор\w*\s+не\s+уточн",
    r"информаци\w*[^.!?\n]{0,40}?\b(?:мало|нет|не\s+хватает)",
    r"в\s+(?:самом\s+)?(?:твите|посте|сообщении|источнике)\s+не\s+"
    r"(?:указ|сказ|раскрыв|уточн|приводит|назван|сообщ)",
    r"(?:название|имя)\s+[^.!?\n]{0,30}?не\s+(?:указ|назван|раскрыв|сообщ|уточн)",
)]
_TEASER_WEAK = re.compile(r"пока\s+неизвестн", re.I)

# ── Visual Claim: маркеры обещания картинки ──────────────────────────────────────────
_VISUAL_RE = re.compile(
    r"сфотографировал|на\s+(?:новом\s+)?снимк(?:е|ах)|показал\w*\s+фото|"
    r"на\s+график(?:е|ах)|скриншот|на\s+видео|\bв\s+кадре\b|\bкадр(?:ы|ах)?\s+(?:с|из|показ)",
    re.I,
)

VISUAL_REWRITE_SYSTEM = """Ты редактор русскоязычного Telegram-канала.
Пост ниже обещает показать изображение (фото, снимок, видео, график, скриншот), но
картинки к посту НЕТ. Перепиши его как текстовую новость:
- убери обещание показать изображение («на снимке», «сфотографировал», «на видео» и т.п.);
- сохрани все факты, числа, названия и HTML-ссылку на источник в конце без изменений;
- не добавляй фактов, которых нет в посте; HTML только <b>, <i>, <a href="">.
Если вся суть новости — само изображение и без него фактов в посте не остаётся,
ответь ровно SKIP.
Верни либо SKIP, либо готовый пост в HTML."""


def _plain(text: str) -> str:
    """HTML → видимый текст: проверки смотрят на то, что увидит читатель."""
    return _html.unescape(re.sub(r"<[^>]+>", " ", text or ""))


def find_teaser_admission(text: str) -> str | None:
    """Фрагмент-заглушку, если пост — пустой тизер; иначе None."""
    plain = _plain(text)
    for rx in _TEASER_STRONG:
        m = rx.search(plain)
        if m:
            return m.group(0)[:80]
    m = _TEASER_WEAK.search(plain)
    if m and not re.search(r"\d", plain):
        return m.group(0)
    return None


def _head(text: str) -> str:
    """Заголовок + первый абзац: до первой пустой строки, иначе первые 2 строки."""
    t = (text or "").strip()
    if "\n\n" in t:
        return t.split("\n\n", 1)[0]
    return "\n".join(t.splitlines()[:2])


def find_visual_claim(text: str) -> str | None:
    m = _VISUAL_RE.search(_plain(_head(text)))
    return m.group(0) if m else None


def strip_teaser_items(digest_text: str) -> tuple[str | None, int]:
    """Вырезает из дайджеста пункты-тизеры. -> (текст | None если пунктов не осталось, вырезано).

    Блоки разделены пустой строкой; первый блок — шапка («Главное за период»).
    Нумерованные пункты («1. ...») перенумеровываются.
    """
    blocks = [b for b in re.split(r"\n\s*\n", (digest_text or "").strip()) if b.strip()]
    if len(blocks) < 2:
        return digest_text, 0
    head, items = blocks[0], blocks[1:]
    kept = [b for b in items if find_teaser_admission(b) is None]
    dropped = len(items) - len(kept)
    if not kept:
        return None, dropped
    if dropped:
        n = 0
        renum = []
        for b in kept:
            if re.match(r"^\s*\d+\.\s", b):
                n += 1
                b = re.sub(r"^\s*\d+\.", f"{n}.", b, count=1)
            renum.append(b)
        kept = renum
    return "\n\n".join([head] + kept), dropped


async def recent_media(session, channel_id: int, window: int = MEDIA_WINDOW) -> list[str | None]:
    """Отпечатки медиа последних `window` постов канала, новые первыми (None — без картинки)."""
    rows = await session.execute(
        select(PostLog.media_fingerprint)
        .where(PostLog.target_id == channel_id)
        .order_by(PostLog.posted_at.desc(), PostLog.id.desc())
        .limit(window)
    )
    return [r[0] for r in rows.all()]


def stock_cooldown_ok(recent: list[str | None], cooldown: int = STOCK_COOLDOWN) -> bool:
    return not any((f or "").startswith("stock:") for f in recent[:cooldown])


async def align_visual_claim(llm, text: str) -> str | None:
    """Один rewrite-вызов: убрать обещание картинки. None = суть только в картинке
    (SKIP) или правка не удалась — пост не публикуем (rejected_missing_media)."""
    raw = await llm._call_with_retry(VISUAL_REWRITE_SYSTEM, text, max_tokens=500)
    clean = (raw or "").strip()
    if not clean or clean.upper().startswith("SKIP"):
        return None
    if find_visual_claim(clean) or "<a href" not in clean:
        return None
    return clean


@dataclass
class PreparedPost:
    text: str
    photo_url: str | None
    media_fingerprint: str | None


@dataclass
class Rejected:
    reason: str      # REJECT_LOW_SUBSTANCE | REJECT_MISSING_MEDIA (влезает в RejectionLog.reason)
    detail: str


async def prepare_single_post(
    *,
    session,
    channel,
    llm,
    text: str,
    original_media_url: str | None,
    unsplash_key: str | None,
    spend: Callable[[], bool] | None = None,
) -> PreparedPost | Rejected:
    """Quality Gate одиночного поста: текст после rewrite → готовый пост или отказ."""
    hit = find_teaser_admission(text)
    if hit:
        return Rejected(REJECT_LOW_SUBSTANCE, hit)

    recent = await recent_media(session, channel.id)
    used = {f for f in recent if f}
    orig_fp = None
    if channel.images_enabled and original_media_url:
        orig_fp = media_fingerprint(original_media_url, "orig")
        if orig_fp in used:
            logger.info("Channel %d: original media already used in last %d posts",
                        channel.id, MEDIA_WINDOW)
            orig_fp = None

    if orig_fp is None:
        claim = find_visual_claim(text)
        if claim:
            if spend is not None and not spend():
                return Rejected(REJECT_MISSING_MEDIA, f"{claim} (no budget to rewrite)")
            new_text = await align_visual_claim(llm, text)
            if new_text is None:
                return Rejected(REJECT_MISSING_MEDIA, claim)
            logger.info("Channel %d: visual claim «%s» removed by rewrite (no original media)",
                        channel.id, claim)
            text = new_text
            hit = find_teaser_admission(text)
            if hit:
                return Rejected(REJECT_LOW_SUBSTANCE, hit)
    else:
        return PreparedPost(text, original_media_url, orig_fp)

    photo_url = fp = None
    if channel.images_enabled and unsplash_key and stock_cooldown_ok(recent):
        try:
            keywords = await llm.suggest_image_keywords(text)
            if keywords:
                photo_url = await fetch_image_url_for_keywords(keywords, unsplash_key, exclude=used)
                fp = media_fingerprint(photo_url, "stock")
        except Exception:
            logger.exception("Stock image pick failed, posting text-only")
            photo_url = fp = None
    return PreparedPost(text, photo_url, fp)
