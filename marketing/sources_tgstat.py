"""Заглушка TGStat-дискавери. Подключение TGStat API — отдельный этап, НЕ MVP.

TODO (после MVP):
- получить ключ TGStat API (https://api.tgstat.ru), положить в .env
  как TGSTAT_API_KEY (только через .env, не в код);
- реализовать fetch_leads: поиск каналов по нише с фильтром подписчиков
  200–5000, маппинг в (username, niche);
- пропускать результат через тот же repo.add_lead (дедуп + blacklist),
  source='tgstat'.

Kwork/fl.ru в MVP не парсим (у площадок свои правила) — такие лиды
основатель добавляет руками через /addlead.
"""
from __future__ import annotations


async def fetch_leads(niche: str, limit: int = 20) -> list[tuple[str, str]]:
    """Вернёт [(channel_username, niche), ...]. Пока не реализовано."""
    raise NotImplementedError("TGStat API — отдельный этап, см. TODO в модуле")
