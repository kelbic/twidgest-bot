"""TwidgestBot — multi-tenant SaaS для автодайджестов из X в Telegram.

Запускает в одном процессе:
- aiogram polling для пользовательских команд
- APScheduler с двумя джобами: collector (30 мин) и publisher (1 час)
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from bot.handlers import (
    admin, billing, channels, costs, forward, freetext, legal, onboarding,
    scout, sources, start, targets,
)
from bot.middlewares.admin_check import AdminOnlyMiddleware
from bot.middlewares.rate_limit import RateLimitMiddleware
from config import Config
from core.llm_client import OpenRouterClient
from core.vk_client import VKClient
from core.twitter_cache import TwitterCache
from core.twitter_client import TwitterClient
from db.session import init_db, init_engine
from workers.collector import run_collect_cycle
from workers.expiry_check import run_expiry_check
from workers.weekly_report import run_weekly_report
from workers.publisher import run_publish_cycle
from workers.channel_health import run_channel_health_cycle
from workers.viral_picker import run_viral_picker_cycle
from workers.queue_cleanup import run_queue_cleanup


def build_dispatcher(admin_user_id: int) -> Dispatcher:
    """Все роутеры и middleware. Вынесено из main(), чтобы тесты гоняли
    апдейты через тот же диспетчер, что и прод (роутер подключается к
    родителю один раз — строить диспетчер в процессе можно лишь однажды)."""
    dp = Dispatcher()

    # ПЕРВЫМ: команды бота в группах не исполняются — кнопка «настроить в личке»
    dp.include_router(onboarding.router)
    dp.include_router(start.router)
    dp.include_router(sources.router)
    dp.include_router(scout.router)
    dp.include_router(targets.router)
    dp.include_router(billing.router)
    # Подключение канала: my_chat_member (кнопка startchannel) + форвард
    dp.include_router(forward.router)
    dp.include_router(channels.router)

    # Rate limiting на все user-команды (admin исключается через ADMIN_USER_ID)
    dp.message.middleware(RateLimitMiddleware(admin_user_id=admin_user_id))
    # Admin router — только для ADMIN_USER_ID
    admin.router.message.middleware(AdminOnlyMiddleware(admin_user_id))
    dp.include_router(costs.router)
    dp.include_router(legal.router)
    dp.include_router(admin.router)
    # ПОСЛЕДНИМ: catch-all «текст как тема» ловит только то,
    # что не поймал никто выше
    dp.include_router(freetext.router)
    return dp


def allowed_updates(dp: Dispatcher) -> list[str]:
    """Типы апдейтов для getUpdates. Telegram помнит последний переданный
    список, поэтому задаём явно; my_chat_member (бота сделали админом канала
    по кнопке «Подключить Telegram-канал») — обязательно."""
    return sorted(set(dp.resolve_used_update_types()) | {"my_chat_member"})


async def run_polling(dp: Dispatcher, bot: Bot) -> None:
    await dp.start_polling(bot, allowed_updates=allowed_updates(dp))


async def main() -> None:
    cfg = Config()

    logging.basicConfig(
        level=cfg.log_level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    init_engine(cfg.database_url)
    await init_db()
    logging.info("DB initialized at %s", cfg.database_url)

    # Способы оплаты — в лог при старте: единственный способ снаружи увидеть,
    # подхватился ли PAYMENT_PROVIDER_TOKEN (сам токен, разумеется, не пишем).
    _tok = cfg.payment_provider_token
    _mode = _tok.split(":")[1] if _tok.count(":") >= 2 else ("set" if _tok else "off")
    logging.info(
        "Payments: stars on, rub %s, receipt %s, sbp %s",
        _mode, "on" if cfg.payment_receipt else "off",
        "on" if (cfg.yookassa_shop_id and cfg.yookassa_secret_key) else "off",
    )

    bot = Bot(
        token=cfg.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = build_dispatcher(cfg.admin_user_id)

    twitter_client = TwitterClient(cfg.twitter_api_key)
    cache = TwitterCache(twitter_client, ttl_seconds=1800)
    llm_default = OpenRouterClient(cfg.openrouter_api_key, cfg.openrouter_model_default)
    llm_pro = OpenRouterClient(cfg.openrouter_api_key, cfg.openrouter_model_pro)
    vk_client = VKClient(cfg.vk_access_token) if cfg.vk_access_token else None
    if vk_client:
        logging.info("VK client initialized")
    else:
        logging.info("VK_ACCESS_TOKEN not set, VK sources disabled")

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_collect_cycle,
        trigger=IntervalTrigger(minutes=cfg.collect_interval_minutes),
        kwargs={
            "bot": bot,
            "cache": cache,
            "llm_default": llm_default,
            "llm_pro": llm_pro,
            "vk_client": vk_client,
        },
    )
    scheduler.add_job(
        run_publish_cycle,
        trigger=IntervalTrigger(hours=1),
        kwargs={"bot": bot, "llm_default": llm_default, "llm_pro": llm_pro},
    )
    scheduler.add_job(
        run_expiry_check,
        # CronTrigger, НЕ IntervalTrigger: привязка к времени суток (4:00 UTC),
        # иначе отсчёт 24ч сбрасывается при КАЖДОМ рестарте бота, и в дни активных
        # деплоев джоба не прогоняется вообще (напоминания об оплате + архивация
        # каналов не работают). Окна внутри expiry_check шириной 24ч = периоду
        # запуска (суточный), дедуп без флагов сохраняется.
        trigger=CronTrigger(hour=4, minute=0),
        kwargs={"bot": bot},
    )
    scheduler.add_job(
        run_weekly_report,
        trigger=CronTrigger(day_of_week="mon", hour=9, minute=0),
        kwargs={"bot": bot},
    )
    scheduler.add_job(
        run_viral_picker_cycle,
        # 15 мин, НЕ час: цикл обязан ходить не реже самого быстрого
        # разрешённого пейсинга — это активационный буст (15 мин в первые
        # сутки канала). Дешёвые проверки (пейсинг, квота, очередь) стоят
        # до LLM-вызовов, вхолостую цикл почти бесплатен.
        trigger=IntervalTrigger(minutes=15),
        kwargs={"bot": bot, "llm_default": llm_default},
    )
    scheduler.add_job(
        run_channel_health_cycle,
        trigger=IntervalTrigger(hours=1),
        kwargs={"bot": bot},
    )
    scheduler.add_job(
        run_queue_cleanup,
        trigger=IntervalTrigger(hours=24),
    )
    scheduler.start()
    logging.info(
        "Scheduler started: collect every %d min, publish every 1 hour.",
        cfg.collect_interval_minutes,
    )

    me = await bot.get_me()
    await bot.set_my_commands([
        BotCommand(command="channels", description="Мои дайджесты и каналы"),
        BotCommand(command="createchannel", description="Создать дайджест по теме"),
        BotCommand(command="sources", description="Источники дайджеста"),
        BotCommand(command="scout", description="AI-скаут: подобрать источники"),
        BotCommand(command="status", description="Детальная статистика дайджеста"),
        BotCommand(command="setinterval", description="Как часто публиковать"),
        BotCommand(command="me", description="Тариф и лимиты"),
        BotCommand(command="upgrade", description="Тарифы и покупка"),
        BotCommand(command="help", description="Все команды и FAQ"),
    ])
    logging.info("Bot @%s started. Polling...", me.username)

    # Самопроверка рублёвого провайдера: молчаливо сломанный токен иначе
    # обнаружится только первым живым плательщиком.
    if cfg.payment_provider_token:
        from bot.handlers.billing import check_rub_provider
        try:
            await check_rub_provider(bot)
            logging.info("Rub provider check: OK")
        except Exception as exc:  # noqa: BLE001 — текст ошибки и есть диагноз
            logging.error("Rub provider check FAILED: %s", exc)

    # СБП (прямой API ЮKassa): ссылка возврата — на чат бота; ключ проверяем
    # тем же принципом «сломанное находит рестарт, а не плательщик»; хвосты
    # pending-платежей, осиротевшие при рестарте, дочитываем поллером.
    import bot.handlers.billing as billing_module
    billing_module.BOT_USERNAME = me.username
    # Подпись атрибуции витрин («Собрано @бот» + deep-link src_c<id>) — тот же username
    import core.attribution as attribution_module
    attribution_module.BOT_USERNAME = me.username
    if cfg.yookassa_shop_id and cfg.yookassa_secret_key:
        from bot.handlers.billing import check_sbp_provider, resume_pending_sbp
        try:
            logging.info("SBP provider check: OK (%s)", await check_sbp_provider())
        except Exception as exc:  # noqa: BLE001 — текст ошибки и есть диагноз
            logging.error("SBP provider check FAILED: %s", exc)
        resumed = await resume_pending_sbp(bot)
        if resumed:
            logging.info("SBP: resumed polling for %d pending payment(s)", resumed)

    # Первый цикл сбора сразу — чтобы не ждать 30 минут после рестарта
    async def _startup_cycle():
        await run_collect_cycle(bot, cache, llm_default, llm_pro, vk_client)
        await run_viral_picker_cycle(bot, llm_default)
        await run_publish_cycle(bot, llm_default, llm_pro)
    asyncio.create_task(_startup_cycle())

    try:
        await run_polling(dp, bot)
    finally:
        scheduler.shutdown()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
