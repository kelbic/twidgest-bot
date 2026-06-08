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
│   └── safe_sender.py            # send_to_target with auto-deactivate
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
│   ├── expiry_check.py           # Every 24h: downgrade expired Pro tiers,
│   │                             #   notify Free users T-1 day before trial expiry
│   └── queue_cleanup.py          # Every 24h: delete digest_queue rows
│                                 #   older than 7 days (TTL, disk hygiene)
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
- Single для пуристого single режима: `workers/collector.py`, после `enqueue_for_digest`
- ⚠️ Все три места независимо проверяют квоты, dedup, фильтры
- ⚠️ Колонки `posted_at_single`, `skipped_at` в `digest_queue` — не удалять без понимания lifecycle

### "Хочу добавить новый тариф или поменять лимиты"

- `tiers.py` — `TIER_LIMITS` dict
- ⚠️ После изменений может понадобиться миграция БД (если есть юзеры со старыми тарифами)

### "Хочу новую LLM-функцию"

- `core/llm_client.py` — все методы вызовов OpenRouter
- Используй `_call_with_retry` для надёжности
- Helper для генерации промптов: `prompts.py`

### "Хочу новый источник данных (RSS, TGStat, etc.)"

- Новый клиент в `core/your_source_client.py`
- Изменения в `db/models.py` (`ChannelSource` нужно обобщить)
- Изменения в `workers/collector.py` (loop по source types)
- ⚠️ Большая работа — спросить юзера про приоритет

### "Хочу починить баг — посмотреть логи"

```bash
journalctl -u twidgest-bot --since "10 minutes ago" --no-pager | tail -50
```

### "Хочу изменить welcome-сообщение или /help"

- `bot/handlers/start.py` — `WELCOME` константа, `/help`, `/legal`, `/tg_help`

## Что трогать не нужно

1. **`prompts.BASE_SAFETY`** — A+B+C разделы. Это юр.защита. Изменения только с владельцем продукта.
2. **`db/models.py`** — изменения требуют миграции существующей БД. Сейчас Alembic нет, мигрируем через `ALTER TABLE` руками.
3. **`tiers.py` структура** — наследие, важна совместимость с активными платежами.
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

8. **Свежесть твитов важнее их виральности.** Без фильтра по `queued_at` старые виральные твиты (накопленные за недели) вытесняют свежие новости в SELECT-запросах с `ORDER BY engagement DESC`. Канал начинает публиковать "новости" 2-недельной давности, подписчики отписываются. Любой SELECT из `digest_queue` для публикации должен иметь `WHERE queued_at > now - N hours`.
