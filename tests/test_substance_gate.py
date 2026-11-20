"""Quality Gate v1 · Фикс 1 — Substance Gate.

Пропуск нормальной новости, отсев тизера «подробностей в твите нет», отсев безымянной
новости (вердикт ранкера), вырезка пунктов-тизеров из дайджеста и исключение
безымянных твитов из очереди дайджеста. Сеть закрыта (LLM — фейк).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import db.session as dbs
from core.candidate_ranker import rank_candidates
from core.quality_gate import (
    REJECT_LOW_SUBSTANCE,
    PreparedPost,
    Rejected,
    find_teaser_admission,
    prepare_single_post,
    strip_teaser_items,
)

SRC = '<a href="https://x.com/a/status/1">→ Источник</a>'

# Дословный класс дефекта с витрины: модель без названия + признание, что деталей нет
TEASER = ("<b>Вышла новая открытая модель</b>\nКомпания выложила веса новой модели. "
          f"Подробностей о самой модели в твите нет.\n\n{SRC}")
NORMAL = ("<b>OpenAI выпустила GPT-5.5 mini</b>\nКонтекст 1M токенов, цена $0.10 за "
          f"миллион входных токенов, лицензия API. Доступна с 19 ноября.\n\n{SRC}")


class FakeLLM:
    def __init__(self, reply=None):
        self.reply = reply
        self.calls = 0

    async def _call_with_retry(self, system, user, **kw):
        self.calls += 1
        return self.reply

    async def suggest_image_keywords(self, text):
        self.calls += 1
        return None


def _channel(**kw):
    base = dict(id=6, images_enabled=True, user_id=1, paid_until=None,
                trial_until=None, attribution_mode=None)
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
async def sm(tmp_path):
    dbs.init_engine(f"sqlite+aiosqlite:///{tmp_path}/sg.db")
    await dbs.init_db()
    yield dbs.session_maker()
    dbs._engine = dbs._session_maker = None


# ── детерминированный пост-чек ───────────────────────────────────────────────────────

def test_teaser_from_showcase_is_caught():
    assert find_teaser_admission(TEASER) is not None


def test_normal_news_passes():
    assert find_teaser_admission(NORMAL) is None


@pytest.mark.parametrize("phrase", [
    "Деталей в твите нет.",
    "Автор не уточнил, о какой модели речь.",
    "Информации пока мало.",
    "В твите не указано название.",
    "Название модели не раскрывается.",
])
def test_stub_phrases_rejected(phrase):
    assert find_teaser_admission(f"<b>Новость</b>\n{phrase}\n\n{SRC}") is not None


def test_weak_unknown_only_without_facts():
    # «пока неизвестно» в пустом тизере — брак; рядом с цифрами — законная новость
    assert find_teaser_admission(f"Компания готовит релиз, сроки пока неизвестны. {SRC}")
    assert find_teaser_admission(
        f"Модель набрала 92% на MMLU, дата релиза пока неизвестна. {SRC}") is None


async def test_prepare_rejects_teaser_before_any_llm_call(sm):
    llm = FakeLLM()
    async with sm() as s:
        res = await prepare_single_post(session=s, channel=_channel(), llm=llm, text=TEASER,
                                        original_media_url=None, unsplash_key=None)
    assert isinstance(res, Rejected) and res.reason == REJECT_LOW_SUBSTANCE
    assert llm.calls == 0


async def test_prepare_passes_normal_news(sm):
    async with sm() as s:
        res = await prepare_single_post(session=s, channel=_channel(), llm=FakeLLM(),
                                        text=NORMAL, original_media_url=None,
                                        unsplash_key=None)
    assert isinstance(res, PreparedPost) and res.text == NORMAL


# ── LLM-часть: вердикт ранкера ───────────────────────────────────────────────────────

def _items(*ids):
    return [SimpleNamespace(id=i, text="t", twitter_username="u", likes=1, retweets=0)
            for i in ids]


async def test_ranker_flags_map_to_substance():
    reply = json.dumps([
        {"id": 1, "interest": 8, "junk": False, "why": "релиз",
         "has_named_subject": True, "has_actionable_facts": True, "admits_missing_details": False},
        {"id": 2, "interest": 6, "junk": False, "why": "без имени",
         "has_named_subject": False, "has_actionable_facts": True, "admits_missing_details": False},
        {"id": 3, "interest": 5, "junk": False, "why": "тизер",
         "has_named_subject": True, "has_actionable_facts": False, "admits_missing_details": True},
        {"id": 4, "interest": 7, "junk": False, "why": "старый формат"},   # полей нет
    ])
    out = await rank_candidates(FakeLLM(reply), _channel(title="AI", niche="tech_ai",
                                                         description=None), _items(1, 2, 3, 4))
    assert out[1].substance == "ok"
    assert out[2].substance == "unnamed"      # безымянная новость — отсев
    assert out[3].substance == "teaser"
    assert out[4].substance == "ok"           # fail-open: нет полей — не наказываем


# ── дайджест ─────────────────────────────────────────────────────────────────────────

DIGEST = """🌐 <b>Дайджест: AI</b>
<i>Подзаголовок</i>

1. <b>Anthropic: Claude 5.5</b>
Контекст 1M, цена снижена на 40%. <a href="u1">→ Источник</a>

2. <b>Новая модель</b>
Подробностей о модели в твите нет. <a href="u2">→ Источник</a>

3. <b>Google: Gemini 4</b>
Рекорд на 3 бенчмарках. <a href="u3">→ Источник</a>"""


def test_digest_drops_teaser_item_and_renumbers():
    text, dropped = strip_teaser_items(DIGEST)
    assert dropped == 1
    assert "Новая модель" not in text
    assert "1. <b>Anthropic" in text and "2. <b>Google" in text


def test_digest_all_teasers_gives_none():
    only = "🌐 <b>Главное</b>\n\n<b>Тема</b>\nДеталей нет. <a href=\"u\">→ источник</a>"
    assert strip_teaser_items(only) == (None, 1)


async def test_digest_queue_excludes_unnamed_keeps_teaser_and_unrated(sm):
    from db.models import Channel, DigestQueueItem, User
    from db.repositories.tweets import get_digest_queue
    now = datetime.utcnow()
    async with sm() as s:
        s.add(User(tg_user_id=50))
        s.add(Channel(id=6, user_id=50, title="AI"))
        await s.flush()
        for tid, sub in (("a", None), ("b", "ok"), ("c", "teaser"), ("d", "unnamed")):
            s.add(DigestQueueItem(user_id=50, channel_id=6, tweet_id=tid, twitter_username="u",
                                  text=tid, url="u", substance=sub,
                                  tweet_created_at=now - timedelta(hours=1)))
        await s.commit()
        got = {q.tweet_id for q in await get_digest_queue(s, 50, 6, max_items=10)}
    assert got == {"a", "b", "c"}
