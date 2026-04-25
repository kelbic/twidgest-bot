"""TwidgestBot — multi-tenant SaaS для автодайджестов из X в Telegram.

Запускает в одном процессе:
- aiogram polling для пользовательских команд
- APScheduler с двумя джобами: collector (30 мин) и publisher (1 час)
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from bot.handlers import admin, billing, channels, forward, sources, start, targets
from bot.middlewares.admin_check import AdminOnlyMiddleware
from config import Config
from core.llm_client import OpenRouterClient
from core.twitter_cache import TwitterCache
from core.twitter_client import TwitterClient
from db.session import init_db, init_engine
from workers.collector import run_collect_cycle
from workers.expiry_check import run_expiry_check
from workers.publisher import run_publish_cycle
from workers.channel_health import run_channel_health_cycle
from workers.viral_picker import run_viral_picker_cycle


async def main() -> None:
    cfg = Config()

    logging.basicConfig(
        level=cfg.log_level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    init_engine(cfg.database_url)
    await init_db()
    logging.info("DB initialized at %s", cfg.database_url)

    bot = Bot(
        token=cfg.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    dp.include_router(start.router)
    dp.include_router(sources.router)
    dp.include_router(targets.router)
    dp.include_router(billing.router)
    dp.include_router(forward.router)
    dp.include_router(channels.router)
    # Admin router — только для ADMIN_USER_ID
    admin.router.message.middleware(AdminOnlyMiddleware(cfg.admin_user_id))
    dp.include_router(admin.router)

    twitter_client = TwitterClient(cfg.twitter_api_key)
    cache = TwitterCache(twitter_client, ttl_seconds=1800)
    llm_default = OpenRouterClient(cfg.openrouter_api_key, cfg.openrouter_model_default)
    llm_pro = OpenRouterClient(cfg.openrouter_api_key, cfg.openrouter_model_pro)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_collect_cycle,
        trigger=IntervalTrigger(minutes=cfg.collect_interval_minutes),
        kwargs={
            "bot": bot,
            "cache": cache,
            "llm_default": llm_default,
            "llm_pro": llm_pro,
        },
    )
    scheduler.add_job(
        run_publish_cycle,
        trigger=IntervalTrigger(hours=1),
        kwargs={"bot": bot, "llm_default": llm_default, "llm_pro": llm_pro},
    )
    scheduler.add_job(
        run_expiry_check,
        trigger=IntervalTrigger(hours=24),
    )
    scheduler.add_job(
        run_viral_picker_cycle,
        trigger=IntervalTrigger(hours=1),
        kwargs={"bot": bot, "llm_default": llm_default},
    )
    scheduler.add_job(
        run_channel_health_cycle,
        trigger=IntervalTrigger(hours=1),
        kwargs={"bot": bot},
    )
    scheduler.start()
    logging.info(
        "Scheduler started: collect every %d min, publish every 1 hour.",
        cfg.collect_interval_minutes,
    )

    me = await bot.get_me()
    logging.info("Bot @%s started. Polling...", me.username)

    # Первый цикл сбора сразу — чтобы не ждать 30 минут после рестарта
    async def _startup_cycle():
        await run_collect_cycle(bot, cache, llm_default, llm_pro)
        await run_viral_picker_cycle(bot, llm_default)
        await run_publish_cycle(bot, llm_default, llm_pro)
    asyncio.create_task(_startup_cycle())

    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
