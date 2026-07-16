"""Маркетинг-скаут — entrypoint. Запуск: python -m marketing.main

Отдельный процесс с отдельным ботом и отдельной БД marketing.db:
падение эксперимента не задевает прод. Один инстанс TwitterCache и один
OpenRouterClient на процесс.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from core.llm_client import OpenRouterClient
from core.twitter_cache import TwitterCache
from core.twitter_client import TwitterClient

from marketing import handlers
from marketing.config import MarketingConfig
from marketing.db import init_db, init_engine
from marketing.enrich import TelegramPreviewClient
from marketing.llm import MarketingLLM
from marketing.workers import Deps, run_crm_cycle, run_enrich_cycle


async def main() -> None:
    cfg = MarketingConfig()

    logging.basicConfig(
        level=cfg.log_level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    init_engine(cfg.database_url)
    await init_db()
    logging.info("Marketing DB initialized at %s", cfg.database_url)

    # Один инстанс на процесс (в проде есть анти-паттерн трёх независимых
    # кэшей — здесь не повторяем)
    twitter_client = TwitterClient(cfg.twitter_api_key)
    cache = TwitterCache(twitter_client, ttl_seconds=1800)
    llm = MarketingLLM(
        OpenRouterClient(cfg.openrouter_api_key, cfg.openrouter_model),
        daily_cap=cfg.daily_llm_cap,
    )
    deps = Deps(cfg=cfg, llm=llm, cache=cache, preview=TelegramPreviewClient())

    bot = Bot(
        token=cfg.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    admin_mw = handlers.AdminOnlyMiddleware(cfg.admin_user_id)
    dp.message.middleware(admin_mw)
    dp.callback_query.middleware(admin_mw)
    dp.include_router(handlers.router)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_enrich_cycle,
        trigger=IntervalTrigger(hours=1),
        kwargs={"bot": bot, "deps": deps},
    )
    scheduler.add_job(
        run_crm_cycle,
        # 10:00 МСК = 07:00 UTC — фоллоу-апы и утренняя сводка
        trigger=CronTrigger(hour=7, minute=0),
        kwargs={"bot": bot, "deps": deps},
    )
    scheduler.start()
    logging.info("Scheduler started: enrich hourly, CRM daily 07:00 UTC")

    me = await bot.get_me()
    await bot.set_my_commands([
        BotCommand(command="queue", description="Следующая карточка"),
        BotCommand(command="addlead", description="Добавить лид: @channel [ниша]"),
        BotCommand(command="import", description="Импорт списка лидов"),
        BotCommand(command="leads", description="Лиды по статусам"),
        BotCommand(command="stats", description="Воронка и конверсии"),
        BotCommand(command="export", description="CSV всех лидов"),
        BotCommand(command="pause", description="Пауза воркеров"),
        BotCommand(command="resume", description="Возобновить воркеры"),
        BotCommand(command="help", description="Все команды"),
    ])
    logging.info("Marketing bot @%s started. Polling...", me.username)

    # Первый цикл сразу — чтобы не ждать час после рестарта. Ссылку держим:
    # asyncio хранит task слабой ссылкой, без неё task может собрать GC;
    # done-callback вытаскивает исключение, иначе оно потеряется молча.
    startup_task = asyncio.create_task(run_enrich_cycle(bot, deps))
    startup_task.add_done_callback(
        lambda t: t.exception() and logging.error(
            "startup enrich cycle failed", exc_info=t.exception()
        )
    )

    try:
        await dp.start_polling(bot, deps=deps)
    finally:
        scheduler.shutdown()
        await deps.preview.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
