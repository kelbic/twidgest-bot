# VK Integration — статус: ЗАВЕРШЕНО (ветка vk-sources)

## Что реализовано
- core/vk_client.py — VK API клиент (wall.get, groups.search, groups.getById)
- /addsource <id> vk:domain — добавление VK источника с валидацией
- /removesource — работает для VK источников
- /admin addsource/removesource — admin override для любого канала
- collector.py — раздельный сбор Twitter и VK, build_vk_prompt для русских постов
- Картинки берутся из VK-поста (не Unsplash)
- source_type колонка в channel_sources (twitter/vk)
- Фильтр единый на канал (filter_preset)

## Тарифы (обновлено)
- Free: 30 дней пробный, 10 источников, 3 канала, 50 постов/день
- Pro: 2999 Stars/мес, те же лимиты + Claude LLM, digest каждые 3ч
- Legacy тарифы (starter/agency) → получают лимиты Pro

## Что НЕ сделано (отложено)
- Per-source фильтры (разные фильтры для Twitter vs VK в одном канале)
- AI-подбор VK источников через /createchannel ai

## Статус веток
- vk-sources: готово, ожидает merge в main после тестирования VK постов
- main: актуален, без VK

## Следующий шаг
git checkout main && git merge vk-sources && git push origin main

