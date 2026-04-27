# TwidgestBot — продолжение работы в новом чате

## Кто я
Solo-разработчик из РФ. До этого 3 дня работал с Claude над production-ready 
Telegram SaaS. Прошлый чат упёрся в лимит контекста, переношу сюда.

## Что за продукт
**TwidgestBot** — multi-tenant Telegram SaaS, автоматизирует публикацию 
X/Twitter контента в Telegram-каналы пользователей. Каждый юзер заводит 
свои каналы, выбирает темы, бот сам собирает твиты, переводит на русский, 
фильтрует и публикует.

## Ссылки
- GitHub: https://github.com/kelbic/twidgest-bot
- Бот: https://t.me/TwidgestBot
- Лендинг: https://kelbic.github.io/twidgest-bot/
- Privacy/ToS: https://kelbic.github.io/twidgest-bot/legal/

## Tech stack
- Python 3.12, aiogram 3.27, SQLAlchemy 2.0 + SQLite (aiosqlite)
- OpenRouter API → Claude Haiku 4.5 (default), Claude Sonnet 4.5 (digest + ranking)
- twitterapi.io для Twitter (с retry + exp backoff)
- Unsplash API для картинок (cached 6h TTL)
- Telegram Stars billing (idempotent)
- systemd на VPS (Aeza), один процесс с aiogram dispatcher + APScheduler

## Архитектура
- collector — раз в 30 мин: фетчит твиты, фильтрует, кладёт в digest_queue
- viral_picker — раз в час: топ из очереди → single пост (для hybrid каналов)
- publisher — раз в час: собирает digest когда пора публиковать
- channel_health — раз в час: уведомляет в DM если канал молчит >24ч
- expiry_check — раз в день: даунгрейд истёкших Pro-подписок

## Текущее состояние (на момент завершения прошлого чата)
✅ Production-ready, всё работает 24/7  
✅ 2 рабочих канала: новости политики (strict filter), хорошие новости (loose)  
✅ 4 пункта ревью senior-разработчика закрыты:
   - Privacy Policy + ToS на GitHub Pages
   - Retry с exp backoff для twitterapi.io
   - Rate limiting middleware на user команды
   - 17 unit-тестов на topic_dedup + integration test stubs  
✅ Filter presets refactored: 2 пресета (strict/loose), один файл prompts.py  
✅ Платежи идемпотентные (UniqueConstraint + try/except IntegrityError)  
✅ TG fallback hint в /createchannel ai (без кода TGStat)  

## Что в TODO (docs/TODO.md)
- Маркетинг: 3 скрипта холодных сообщений готовы (DM админам, Kwork, SMM-чаты)
- TGStat integration — только если 3+ юзера попросят (сейчас manual через /tg_help)
- Alembic + автобэкапы — после 50+ юзеров
- AI Interview Coach как side-project

## Главный текущий блокер
**Маркетинг.** Продукт готов, но 0 платящих юзеров. План на ближайшие дни:
1. Найти 10 малых TG-каналов в нишах AI/crypto/longevity (200-1000 подписчиков, 
   видно что вручную ведут)
2. Написать DM их админам по готовому скрипту с предложением Pro 30 дней бесплатно
3. Зарегистрироваться на Kwork/fl.ru, откликаться на заказы 
   "контент-менеджер для TG-канала"
4. После первых 5 платящих — собирать кейсы для лендинга

## Ключевые продуктовые инсайты из прошлой работы
1. LLM плохо как источник фактов (галлюцинирует имена), отлично как фильтр 
   реальных данных
2. Whitelist в промпте делает фильтр **жёстче**, blocklist — мягче
3. Twitter не покрывает русский региональный контент — гипотеза TGStat 
   валидируется через manual /tg_help до полной интеграции
4. Стандартные ниши (AI, crypto, F1, longevity) работают на Twitter Search 
   на 90%+, узкие русские (маникюр, Чебоксары, Таро на русском) — 
   через ручную настройку

## С чем нужна помощь сейчас
[Здесь напиши свой конкретный запрос — 
маркетинг / новая фича / отладка / что-то ещё]
