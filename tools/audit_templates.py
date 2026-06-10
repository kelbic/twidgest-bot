"""Аудит хардкод-источников из templates.py через скаут-превалидацию.

Прогоняет каждый источник каждого шаблона через те же гейты, что /scout:
форма (текст/активность/частота/пороги шаблона) + тема (LLM, доля твитов
по теме шаблона). Печатает отчёт и вердикт по каждому шаблону.

Запуск из корня репо (нужен .env):
    python3 tools/audit_templates.py            # полный аудит
    python3 tools/audit_templates.py --no-llm   # без проверки темы (дешевле)
    python3 tools/audit_templates.py -t ai-news # один шаблон

Стоимость полного прогона: ~121 fetch twitterapi.io + 15 LLM-вызовов.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import Config
from core.llm_client import OpenRouterClient
from core.twitter_cache import TwitterCache
from core.twitter_client import TwitterClient
from templates import TEMPLATES
from workers.source_scout import apply_topic_relevance, prevalidate_candidates

logging.basicConfig(level=logging.INFO, format="%(message)s")
# Глушим шум HTTP-клиентов, оставляем причины отсева от скаута
logging.getLogger("aiohttp").setLevel(logging.WARNING)
logging.getLogger("core").setLevel(logging.WARNING)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-llm", action="store_true", help="без проверки темы")
    ap.add_argument("-t", "--template", help="только один шаблон по id")
    args = ap.parse_args()

    cfg = Config()
    twitter = TwitterClient(cfg.twitter_api_key)
    cache = TwitterCache(twitter, ttl_seconds=3600)
    llm = OpenRouterClient(cfg.openrouter_api_key, cfg.openrouter_model_default)

    items = TEMPLATES.items()
    if args.template:
        items = [(args.template, TEMPLATES[args.template])]

    summary: list[str] = []
    for tpl_id, tpl in items:
        print(f"\n{'=' * 64}\n{tpl.emoji} {tpl.name} (id={tpl_id}) — "
              f"{len(tpl.default_sources)} источников, "
              f"пороги {tpl.suggested_min_likes}/{tpl.suggested_min_retweets}")
        print("-" * 64)

        candidates = [(s.lower().lstrip("@"), "") for s in tpl.default_sources]
        alive = await prevalidate_candidates(
            candidates, tpl.suggested_min_likes, tpl.suggested_min_retweets, cache
        )
        if not args.no_llm and alive:
            topic = f"{tpl.name} / {tpl.description}"
            alive = await apply_topic_relevance(llm, topic, alive)

        alive_names = {c.username for c in alive}
        dead = [u for u, _ in candidates if u not in alive_names]

        for c in alive:
            print(f"  ✅ @{c.username}: {c.stats_line()}")
        for u in dead:
            print(f"  ❌ @{u}  (причина — в логе отсева выше)")

        n = len(alive)
        # Доля живых, а не абсолют: шаблоны разного размера (6-9 источников).
        # Пограничные авторы «мигают» между прогонами (окно последних 20
        # твитов сдвигается) — потеря одного из шести не повод для тревоги.
        share = n / max(len(tpl.default_sources), 1)
        if share >= 0.75 and n >= 4:
            verdict = "✅ здоров"
        elif share >= 0.5:
            verdict = "⚠️ доукомплектовать"
        else:
            verdict = "❌ сломан"
        total_yield = sum(c.est_posts_per_week for c in alive)
        line = (f"{verdict:<22} {tpl_id:<16} живых {n}/{len(tpl.default_sources)}, "
                f"суммарная отдача ~{total_yield:.0f} постов/нед")
        summary.append(line)
        print(f"  → {line}")

    print(f"\n{'=' * 64}\nИТОГО:")
    for line in sorted(summary):
        print(" ", line)


if __name__ == "__main__":
    asyncio.run(main())
