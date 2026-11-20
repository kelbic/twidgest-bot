"""Нативная атрибуция витрины: «→ Источник · Собрано @бот» + deep-link воронки.

Режим на канале (Channel.attribution_mode):
  off         — без подписи (умолчание для оплаченных каналов);
  digest_only — подпись только в дайджестах;
  all_posts   — подпись в каждом посте (витрины админа и триал).
NULL = авто по статусу канала (core/plan.channel_status).

Ссылка подписи — https://t.me/<bot>?start=src_c<channel_id>; bot/handlers/start.py
пишет start-параметр в users.start_source при регистрации, /admin funnel считает
регистрации и привязки каналов по каждому источнику.
"""
from __future__ import annotations

import re
from datetime import datetime

from core.plan import channel_status

# Настоящий username ставит main.py на старте (bot.get_me()); здесь — дефолт для тестов.
BOT_USERNAME = "TwidgestBot"

MODES = ("off", "digest_only", "all_posts")

_SRC_LINK_RE = re.compile(r"<a\s[^>]*>\s*→\s*источник\s*</a>", re.I)
_START_PARAM_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def effective_mode(channel, now: datetime | None = None) -> str:
    explicit = (getattr(channel, "attribution_mode", None) or "").strip().lower()
    if explicit in MODES:
        return explicit
    return "off" if channel_status(channel, now) == "paid" else "all_posts"


def channel_slug(channel_id: int) -> str:
    return f"c{int(channel_id)}"


def deep_link(slug: str, bot_username: str | None = None) -> str:
    return f"https://t.me/{bot_username or BOT_USERNAME}?start=src_{slug}"


def footer_for(channel, *, is_digest: bool, bot_username: str | None = None,
               now: datetime | None = None) -> str:
    """HTML-подпись для поста канала или "" — если режим её не предусматривает."""
    mode = effective_mode(channel, now)
    if mode == "off" or (mode == "digest_only" and not is_digest):
        return ""
    user = bot_username or BOT_USERNAME
    return f'<a href="{deep_link(channel_slug(channel.id), user)}">Собрано @{user}</a>'


def apply_footer(text: str, footer: str, *, is_digest: bool = False) -> str:
    """Single: в ту же строку после последней ссылки «→ Источник» (через « · »).
    Дайджест (ссылок по пункту на тему) и пост без такой ссылки: отдельной последней строкой."""
    if not footer:
        return text
    matches = list(_SRC_LINK_RE.finditer(text))
    if matches and not is_digest:
        end = matches[-1].end()
        return f"{text[:end]} · {footer}{text[end:]}"
    return f"{text.rstrip()}\n\n{footer}"


# Служебные start-параметры — навигация внутри бота, а не канал привлечения:
# «setup» ставит кнопка «настроить в личке» из группы. Первым касанием не пишем.
SERVICE_START_PARAMS = frozenset({"setup"})


def parse_start_source(arg: str | None) -> str | None:
    """start-параметр deep-link'а, пригодный для учёта (src_c5, src_vc, …); мусор → None."""
    a = (arg or "").strip()
    if a.lower() in SERVICE_START_PARAMS:
        return None
    return a if _START_PARAM_RE.match(a) else None
