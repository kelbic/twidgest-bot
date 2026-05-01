# VK Integration Plan — vk-sources branch

## Цель
Добавить VK как второй тип источника наравне с Twitter.
Фильтр — на уровне канала (Channel.filter_preset), не per-source (MVP).

## Что НЕ меняем
- filter_preset остаётся на Channel, не на ChannelSource
- Биллинг, тиры, лимиты источников — без изменений
- Существующие Twitter-каналы — без изменений

## Ключевое отличие VK от Twitter
VK-посты уже на русском → LLM не переводит, а редактирует/сокращает.
Нужен отдельный промпт-путь: build_vk_prompt(niche, filter_mode).

## Файлы для изменения

### 1. core/vk_client.py — НОВЫЙ (восстановить из коммита 836a284)
Готов на 100%, переиспользуем без изменений.

### 2. config.py — добавить
vk_access_token: str = field(default_factory=lambda: os.getenv("VK_ACCESS_TOKEN", ""))

### 3. db/models.py — добавить поле в ChannelSource
source_type: Mapped[str] = mapped_column(String(16), default="twitter")
# values: "twitter" | "vk"

### 4. prompts.py — добавить
def build_vk_prompt(niche_code, filter_mode) -> str:
    # Аналог build_single_prompt но без "переводи с английского"
    # "Отредактируй русскоязычный пост для Telegram-канала"

### 5. workers/collector.py — разветвить по source_type
for source in channel.channel_sources:
    if source.source_type == "vk":
        posts = await vk_cache.get_posts(source.username)
        # normalize VKPost → общий формат
    else:  # twitter (default)
        tweets = await cache.get_tweets(source.username)

### 6. bot/handlers/sources.py — расширить /addsource
/addsource 5 vk:lentaru      → source_type="vk"
/addsource 5 @elonmusk        → source_type="twitter" (как сейчас)
Добавить validate_community() перед сохранением.

### 7. main.py — инициализировать VKClient если токен задан

## Идентификаторы VK в БД
Хранить в ChannelSource.username как "vk:lentaru" (с префиксом).
parse_identifier() в vk_client.py умеет его разбирать.

## Миграция БД
Добавляем колонку source_type с default="twitter" →
существующие записи получат "twitter" автоматически.
ALTER TABLE channel_source ADD COLUMN source_type VARCHAR(16) DEFAULT 'twitter';

## Порядок реализации
1. core/vk_client.py (восстановить)
2. config.py + .env.example
3. db/models.py + миграция
4. prompts.py — build_vk_prompt
5. workers/collector.py — VK ветка
6. bot/handlers/sources.py — /addsource vk:
7. main.py — VKClient init
8. Тест + коммит + merge в main

## Статус
[ ] vk_client.py восстановлен
[ ] config.py обновлён
[ ] db/models.py + миграция
[ ] prompts.py build_vk_prompt
[ ] collector.py VK ветка
[ ] sources.py /addsource vk:
[ ] main.py init
[ ] протестировано
