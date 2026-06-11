# TwidgestBot — карта проекта для AI-сессий

Документ для быстрого онбординга новых Claude/Cursor/Copilot-сессий.
Содержит: где какая логика, какие файлы трогать, какие — нет.

## Project structure

```text
twidgest-bot/
├── main.py                       # Entry point: dispatcher + scheduler
├── config.py                     # Env config (.env loading)
├── prompts.py                    # ⭐ Single source of truth: LLM prompts
│                                 #   - BASE_SAFETY (A/B/C — fixed)
│                                 #   - strict/loose filter modes (D)
│                                 #   - NICHES dict
│                                 #   - build_single_prompt(niche, mode)
│                                 #   - build_digest_prompt(niche)
├── tiers.py                      # Pricing tiers (Free/Starter/Pro/Agency)
├── templates.py                  # 15 built-in channel templates
├── engagement_defaults.py        # Per-niche engagement thresholds
│
├── bot/
│   ├── handlers/
│   │   ├── start.py              # /start, /help, /legal, /tg_help
│   │   ├── channels.py           # /channels, /createchannel, /templates
│   │   ├── sources.py            # /sources, /addsource, /removesource,
│   │   │                         #   /regenerate, /setimages, /setfilter,
│   │   │                         #   /filters, /status
│   │   ├── scout.py              # /scout: AI source discovery — HIL card,
│   │   │                         #   prevalidation on author's real tweets
│   │   ├── forward.py            # Auto-bind channel on forwarded message
│   │   ├── admin.py              # /admin (grant/revoke/user/stats/
│   │   │                         #   broadcast/channels/notify/setfilter)
│   │   ├── billing.py            # /upgrade, Stars payment flow
│   │   └── targets.py            # Legacy /target commands
│   └── middlewares/
│       ├── admin_check.py        # ADMIN_USER_ID gate
│       └── rate_limit.py         # Per-user, per-command rate limits
│
├── core/
│   ├── twitter_client.py         # twitterapi.io wrapper with retry+backoff
│   ├── twitter_cache.py          # Shared in-memory TTL cache
│   ├── llm_client.py             # OpenRouter client, rewrite_tweet,
│   │                             #   build_digest, suggest_image_keywords
│   ├── image_picker.py           # Unsplash API + 6h in-memory cache
│   ├── topic_dedup.py            # Jaccard similarity for content dedup
│   ├── candidate_ranker.py       # LLM reviewer-ranker for viral_picker:
│   │                             #   interest 1-10 + junk verdicts, fail-open
│   └── safe_sender.py            # send_to_target(ChannelTarget): channel
│                                 #   auto-deactivate + owner notification
│
├── db/
│   ├── models.py                 # SQLAlchemy models
│   ├── session.py                # Async engine setup
│   └── repositories/
│       ├── users.py              # get_or_create_user, is_tier_active
│       ├── channels.py           # create_channel, get_user_channels,
│       │                         #   delete_channel (cascades cleanup)
│       ├── tweets.py             # is_processed, enqueue_for_digest,
│       │                         #   get_digest_queue, log_post, log_digest
│       ├── billing.py            # activate_tier, record_payment (idempotent)
│       └── admin.py              # get_user_full, get_global_stats
│
├── workers/
│   ├── collector.py              # Every 30 min: fetch tweets,
│   │                             #   filter by engagement, send to digest queue
│   ├── publisher.py              # Every 1h: build & post digests
│   │                             #   (window: last 14h, includes single posts)
│   ├── viral_picker.py           # Every 1h: pick top-1 from queue
│   │                             #   for hybrid channels (single-style post,
│   │                             #   window: last 24h, marks posted_at_single)
│   ├── channel_health.py         # Every 1h: notify owner if channel silent 24h+
│   │                             #   (silent alert carries a "run scout" button)
│   ├── source_scout.py           # On-demand (no schedule): discover and
│   │                             #   prevalidate new X sources for a channel
│   ├── expiry_check.py           # Every 24h: downgrade expired Pro tiers,
│   │                             #   notify Free users T-1 day before trial expiry
│   └── queue_cleanup.py          # Every 24h: TTL cleanup — digest_queue 7d,
│                                 #   rejection_log 30d, scout_suggestions 30d
│
├── tests/
│   ├── test_topic_dedup.py       # 17 unit tests on Jaccard similarity
│   └── test_content_safety.py    # Integration stubs (manual run, costs API calls)
│
└── docs/
    ├── ARCHITECTURE.md           # ← THIS FILE
    ├── PROJECT_STATUS.md         # State snapshot for new AI sessions
    ├── JOURNEY.md                # Project history Roblox → SaaS
    ├── USER_GUIDE.md             # End-user documentation
    ├── TODO.md                   # Live backlog with technical debt
    └── legal/
        ├── privacy.md            # Privacy Policy (RF-compliant)
        └── terms.md              # Terms of Service
```

## Где какая логика — quick lookup

### "Хочу изменить как фильтр контента работает"

- Главный файл: `prompts.py`
- Ключевые константы: `BASE_SAFETY` (A+B+C), `_STRICT_RULES`, `_LOOSE_RULES` (D)
- Функция: `build_single_prompt(niche, filter_mode)`
- ⚠️ A/B/C разделы = фиксированные защиты (юр.риски, наркотики, мед.дозировки), трогать только если знаешь что делаешь

### "Хочу добавить новую команду в бота"

- Создай handler в `bot/handlers/your_command.py`
- Подключи router в `main.py` через `dp.include_router(...)`
- Если команда дорогая (LLM/API calls) — добавь в `bot/middlewares/rate_limit.py` `COMMAND_LIMITS` dict

### "Хочу изменить логику публикации (digest или single)"

- Digest: `workers/publisher.py`, метод `_process_channel`
  - Берёт твиты из `digest_queue` за окно **14 часов** (`queued_at > now - 14h`)
  - Включает твиты, опубликованные в single (digest = "обзор лучшего за период")
  - После публикации удаляет твиты из очереди через `clear_digest_items`
- Single для hybrid режима: `workers/viral_picker.py`, метод `_process_hybrid_channel`
  - Берёт твиты за окно **24 часа** (`queued_at > now - 24h`)
  - Фильтрует `posted_at_single IS NULL AND skipped_at IS NULL`
  - После публикации помечает `posted_at_single = now()` (не удаляет — для digest)
  - Использует `channel.min_likes` из БД (не хардкод)
  - Перед rewrite кандидаты проходят ревьювер-ранкер (`core/candidate_ranker.py`):
    junk → `skipped_at` + RejectionLog `review:*`, остальные сортируются по interest.
    При сбое LLM — fail-open на порядок по engagement (канал не должен молчать)
- Single для пуристого single режима: `workers/collector.py`, после `enqueue_for_digest`
- ⚠️ Все три места независимо проверяют квоты, dedup, фильтры
- ⚠️ Колонки `posted_at_single`, `skipped_at` в `digest_queue` — не удалять без понимания lifecycle

### "Хочу поменять цену, триал, лимиты или биллинг" (слот-модель, июнь 2026)

- **Единственный источник правды — `core/plan.py`**: `channel_active()` / `channel_status()`
  ('admin'|'paid'|'trial'|'inactive'), `PRICE_STARS=999`, `SLOT_DAYS=30`, `TRIAL_DAYS=7`,
  `POSTS_PER_DAY` по статусу, `digest_floor()`, `MAX_CHANNELS_PER_USER`, `extension_base()`.
- Модель: **канал = слот**. Оплата 999⭐/30 дней НА КАНАЛ (`bot/handlers/billing.py`,
  invoice payload `slot:<channel_id>`, продление от `extension_base`). Триал 7 дней —
  только первому каналу юзера (`db/repositories/channels.create_channel` + флаг
  `users.trial_used`). Free-уровня НЕТ: неоплаченный канал молчит полностью.
- Жизненный цикл: `workers/expiry_check.py` — три окна шириной 24ч (= интервалу джобы,
  дедуп без флагов): напоминание об оплате, «чек 5-го дня» триала, уведомление о замолкании.
- Воркеры проверяют ТОЛЬКО `channel_active(channel)` — никаких проверок `user.tier`.
- ⚠️ `tiers.py` — LEGACY: живые потребители только `bot/handlers/admin.py` (/grant) и
  константы капов (`MAX_SOURCES_PER_CHANNEL=15`, `DAILY_EVAL_BUDGET=150`). В новом коде
  не использовать.

### "Хочу поменять юр-фильтр"

- `prompts.py`: фильтр слоёный. `safety_rules(legal_rf)` собирает промпт-защиту:
  слой A (`SAFETY_LEGAL_RF`, RF-риски) — отключаем ПЕР-КАНАЛЬНО; слои B+C
  (`_SAFETY_CORE`: наркотики, мед.дозировки) — НЕ отключаются никогда.
- Канальный флаг: `channels.legal_rf_filter` (default 1) + аудит `legal_optout_at`.
- UI: `bot/handlers/legal.py` — /setlegal с двухшаговым подтверждением отказа.
- Все билдеры промптов принимают `legal_rf=channel.legal_rf_filter` (collector,
  viral_picker, publisher).

### "Хочу метрики, себестоимость или отчёты"

- Себестоимость twidgest: `core/metrics.py` — кумулятивные счётчики, строки
  `cost-totals:` в логах после циклов collector/viral_picker. Снятие: разница
  значений за период ÷ дни ÷ каналы (grep по journalctl).
- AI-бюджет: `core/budget.py` — дневной лимит оценок на канал, при исчерпании
  viral_picker деградирует (без ранкера, likes≥2×min_likes), строка в /status.
- Скоры ранкера: `digest_queue.interest_score` пишется в viral_picker при
  ранжировании — сырьё для отчёта.
- Недельный отчёт владельцу: `workers/weekly_report.py`, CronTrigger пн 09:00 —
  посты/дайджесты, средний interest, junk-rate, часы экономии
  (`MINUTES_PER_POST` из expiry_check).

### "Хочу новую LLM-функцию"

- `core/llm_client.py` — все методы вызовов OpenRouter
- Используй `_call_with_retry` для надёжности
- Helper для генерации промптов: `prompts.py`

### "Хочу новый источник данных (RSS, TGStat, etc.)"

- Новый клиент в `core/your_source_client.py`
- Изменения в `db/models.py` (`ChannelSource` нужно обобщить)
- Изменения в `workers/collector.py` (loop по source types)
- ⚠️ Большая работа — спросить юзера про приоритет

### "Хочу подкрутить скаута источников (/scout)"

- Логика подбора и превалидации: `workers/source_scout.py` (константы порогов вверху файла)
- Команда, колбэки, кулдаун, лимиты тарифа: `bot/handlers/scout.py`
- Кнопка в silent-алерте: `workers/channel_health.py` (callback `scoutrun:<channel_id>`)
- Хранилище предложений: таблица `scout_suggestions` (TTL 30 дней в `queue_cleanup`)

### "Хочу починить баг — посмотреть логи"

```bash
journalctl -u twidgest-bot --since "10 minutes ago" --no-pager | tail -50
```

### "Хочу изменить welcome-сообщение или /help"

- `bot/handlers/start.py` — `WELCOME` константа, `/help`, `/legal`, `/tg_help`

## Что трогать не нужно

1. **`prompts._SAFETY_CORE` (слои B+C: наркотики, мед.дозировки)** — юр.защита, НЕ настройка,
   не отключается никаким флагом. Слой A (`SAFETY_LEGAL_RF`) управляется только через
   /setlegal с фиксацией согласия — не менять семантику без владельца продукта.
   `BASE_SAFETY` оставлен как алиас `safety_rules(True)` для обратной совместимости.
2. **`db/models.py`** — изменения требуют миграции существующей БД. Сейчас Alembic нет, мигрируем через `ALTER TABLE` руками.
3. **`tiers.py`** — LEGACY-модуль (см. его докстринг): не удалять (нужен /grant и константам
   капов), но и не подключать в новый код — активность каналов решает только `core/plan.py`.
4. **`legal/*.md`** — изменения только с юр.консультацией.
5. **`venv/`, `*.db`, `.env`** — никогда не комитить (защищено `.gitignore`).
6. **Engagement thresholds в коде** — пороги `min_likes` / `min_retweets` приходят из `channel.min_likes` / `channel.min_retweets` (per-channel настройка пользователя). НЕ добавлять глобальные константы-пороги в `workers/viral_picker.py` или `workers/collector.py` — они переопределят настройки пользователей.

## Команды для быстрой проверки

```bash
# Все Python-файлы компилируются?
find . -name "*.py" -not -path "*/venv/*" | xargs python3 -m py_compile && echo OK

# Тесты прошли?
source venv/bin/activate && pytest tests/test_topic_dedup.py -v

# Бот живой?
systemctl status twidgest-bot --no-pager | head -5

# Канал работает?
sqlite3 twidgest.db "SELECT id, title, filter_preset FROM channels;"
```

## Workflow для серьёзных изменений

1. **Checkpoint в git перед началом**:

```bash
   git status              # должен быть clean
   git log --oneline -3    # знаем точку отката
```

2. **Атомарные шаги, компиляция после каждого**:

```bash
   # Изменил файл
   python3 -m py_compile файл.py && echo OK
```

3. **Не рестартовать бот до проверки финального состояния**:

```bash
   find . -name "*.py" -not -path "*/venv/*" | xargs python3 -m py_compile && echo "ALL OK"
```

4. **Только после ALL OK — рестарт и наблюдение**:

```bash
   systemctl restart twidgest-bot
   sleep 5
   journalctl -u twidgest-bot --since "30 seconds ago" | grep -E "ERROR|Traceback"
```

5. **Если что-то пошло не так** — `git checkout файл.py` или `git reset --hard HEAD`.

## Lessons learned (важно для AI-сессий)

1. **Regex для multi-line constants — ненадёжен.** Если паттерн не нашёлся 2 раза — не пытаться 3-й. Переписать целиком через `cat > file << 'EOF'` или попросить юзера показать текущий код перед патчем.

2. **Whitelist в LLM-промпте делает фильтр жёстче, blocklist — мягче.** Когда нужно мягкое поведение — описывать что **отбраковывать**, не что **публиковать**.

3. **"Лучше ничего не опубликовать чем опасное" в начале промпта доминирует над любыми последующими "PUBLISH" rules.** LLM обрабатывает промпт кумулятивно.

4. **Default `filter_mode` при создании каналов = "strict".** Юзер может переключить на "loose" через `/setfilter <id> loose` если нужны новости/реакции (BBC headlines).

5. **Twitter не покрывает русский региональный контент** (Чебоксары, Таро на русском, маникюр). Workaround: команда `/tg_help` для manual setup TG-каналов как источников.

6. **Хардкод констант поверх БД-настроек — опасно.** Реальный кейс: `MIN_LIKES_FOR_SINGLE = 100` в `viral_picker.py` переопределял `channel.min_likes` пользователя. Пользователь настраивал в боте порог 5, бот игнорировал и использовал 100. Каналы молчали часами. Правило: при добавлении константы-порога — проверить, нет ли уже соответствующего поля в БД-модели канала/пользователя.

7. **Mark vs delete для пост-публикационных меток.** Когда нужно "запомнить что что-то произошло" — добавить колонку с timestamp (`posted_at_single`, `skipped_at`, `notified_at`), а не удалять запись. Сохраняет историю + гибкость для будущих сценариев (digest как "обзор лучшего за период" использует записи, помеченные `posted_at_single`).

8a. **Кросс-табличные id не взаимозаменяемы.** Реальный кейс: воркеры передавали в `safe_sender` анонимный FakeTarget с `id=channel.id`, а деактивация била в таблицу `targets` по этому id — при потере доступа к каналу гасился ЧУЖОЙ target с совпавшим автоинкрементом, сам канал оставался активным и фейлился каждый цикл. Правило: объект-носитель id обязан явно говорить, к какой таблице id относится (`ChannelTarget.channel_id`); утиная типизация для БД-сущностей запрещена.

8b. **Платные вызовы — после всех дешёвых фильтров, не до.** Реальный кейс: collector фетчил источники всех активных каналов, а tier-проверка шла позже в `_process_channel` — API-кредиты тратились на твиты expired-юзеров, которые гарантированно выбрасывались (−40% запросов после фикса). Правило: дешёвые проверки (тариф, лимиты, is_active) — строго до дорогих операций (API fetch, LLM).

8. **Свежесть твитов важнее их виральности.** Без фильтра по `queued_at` старые виральные твиты (накопленные за недели) вытесняют свежие новости в SELECT-запросах с `ORDER BY engagement DESC`. Канал начинает публиковать "новости" 2-недельной давности, подписчики отписываются. Любой SELECT из `digest_queue` для публикации должен иметь `WHERE queued_at > now - N hours`.

9. **Patcher без dry-run на клоне — лотерея.** Каждый патч-скрипт с assert-якорями
   обязан пройти на свежем клоне репо (компиляция + рантайм-импорт с env-заглушками)
   ДО отправки в прод. Реальные кейсы: якорь моделей не совпал (между `is_active` и
   `mode` были другие поля), порядок строк в лендинге был перепутан — оба пойманы
   клоном, не продом.

10. **`py_compile` и рантайм-импорт НЕ ловят имена внутри функций.** Python резолвит
   их при вызове. Реальные кейсы: publisher использовал `posts_today_channel` без
   импорта (импорт модуля проходил!), weekly_report обращался к несуществующему
   `RejectionLog.created_at` (поле называется `rejected_at`). Правило: после правок —
   AST-проверка (собрать ImportFrom-имена / атрибуты моделей и сверить с фактом).

11. **Дедуп периодических уведомлений — шириной окна, без флагов в БД.** Если джоба
   ходит раз в N часов, окно условия шириной ровно N часов гарантирует ровно одно
   срабатывание на сущность (см. три окна `expiry_check`). Колонка-флаг не нужна.

12. **Активность канала проверяется ТОЛЬКО через `core.plan.channel_active()`.**
   Прямые сравнения `user.tier`, `tier_expires_at` и т.п. в воркерах/хендлерах —
   регрессия к снесённой модели; при ревью такого кода — переписать на plan.
