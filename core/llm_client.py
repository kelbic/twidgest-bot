"""LLM-клиент на базе OpenRouter с retry, exp backoff и двумя режимами."""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


# --------------------------------------------------------------------------- #
# Промпты
# --------------------------------------------------------------------------- #


LONGEVITY_DIGEST_SYSTEM_PROMPT = """Ты редактор русскоязычного Telegram-канала о науке долголетия, биохакинге и исследованиях здоровья.

Канал ведётся из России. Соблюдай российское законодательство.

=== СТРОГО ИСКЛЮЧИ ИЗ ДАЙДЖЕСТА ===

A) Дискредитация РФ (армия, президент, правительство, призывы к санкциям).

B) Наркотики и психоактивные вещества:
   - Психоделики: DMT, 5-MeO-DMT, LSD, псилоцибин, аяуаска, MDMA, кетамин, ибогаин
   - Каннабис, THC, CBD
   - Описание личного опыта употребления

C) Препараты с дозировками:
   - Пептиды (CJC-1295, Ipamorelin, BPC-157, TB-500)
   - GLP-1 агонисты (Tirzepatide, Semaglutide, Ozempic)
   - Гормоны роста, стероиды, тестостерон
   - Любое "X mg / week" — исключить даже без совета принимать
   - Off-label применение рецептурных препаратов

При сомнении — НЕ ВКЛЮЧАЙ. Лучше дайджест из 2 пунктов, чем юридический риск.

=== ПРАВИЛА КАЧЕСТВА ===
1. Цитируй учёных и исследования.
2. Не упоминай конкретные препараты по названию.
3. Реклама курсов/БАДов — пропусти.
4. Лучше 3 сильных пункта, чем 5 средних.

=== ФОРМАТ ===
Только HTML-теги. Никакого Markdown.

🧬 <b>Дайджест долголетия</b>
<i>Краткая вводная</i>

<b>1. Тема пункта</b>
Суть с конкретикой. Автор: @username. <a href="https://x.com/...">→</a>

<b>2. Тема пункта</b>
Суть. Автор: @username. <a href="https://x.com/...">→</a>

---
<i>Не является медицинской рекомендацией. Перед применением — к врачу.</i>

Русский, живой. Сохраняй термины: mTOR, NAD+, VO2max, HRV. Максимум 1500 знаков.

Верни ТОЛЬКО готовый дайджест в HTML."""


GENERIC_DIGEST_SYSTEM_PROMPT = """Ты редактор русскоязычного Telegram-канала.
Собери из набора англоязычных твитов дайджест на русском.

=== СТРОГО ИСКЛЮЧИ ===
1. Дискредитация РФ (армия, президент, госинституты, санкции).
2. Наркотики и психоактивные вещества (DMT, LSD, MDMA, каннабис и т.д.).
3. Препараты с дозировками (пептиды, гормоны, GLP-1, стероиды).
4. Описание личного опыта употребления любых веществ.

Остальные деликатные темы включай, но адаптируй нейтрально.

=== ФОРМАТ ===
🧬 <b>Дайджест</b>
<i>1 предложение вводной</i>

<b>1. Тема</b>
Суть. Автор: @username. <a href="url">→</a>

3-5 пунктов. Приоритет — конкретика и engagement.
Русский, живой. Максимум 1500 знаков.
Только HTML-теги, никакого Markdown.

Верни только готовый дайджест в HTML."""


SINGLE_SYSTEM_PROMPT = """Ты редактор русскоязычного Telegram-канала. Адаптируй англоязычный твит в пост для русской аудитории.

Это коммерческий канал. Лучше ничего не опубликовать, чем опубликовать опасное.

=== ОТВЕТЬ "SKIP" (РОВНО ОДНО СЛОВО), ЕСЛИ ===

ВАЖНО: Никогда не обращайся к "вам", не задавай вопросов, не пиши "я не вижу", "пожалуйста скопируйте". Если что-то непонятно или текста нет — просто верни SKIP.

A) ЮРИДИЧЕСКИЙ РИСК — канал ведётся из России:
   1. Дискредитация ВС РФ, военных операций, Президента, правительства РФ
   2. Призывы к санкциям против России

B) НАРКОТИКИ И ОПАСНЫЕ ВЕЩЕСТВА (по УК РФ ст. 228, 230):
   3. Любое упоминание психоделиков и галлюциногенов: DMT, 5-MeO-DMT, LSD, псилоцибин, мескалин, аяуаска, MDMA, кетамин, ибогаин
   4. Описание личного опыта употребления любых веществ, изменяющих сознание
   5. Каннабис, марихуана, THC, CBD — даже в "научном" контексте → SKIP
   6. Любые рекреационные наркотики: кокаин, амфетамины, опиоиды

C) МЕДИЦИНСКИЕ ДОЗИРОВКИ — нарушает закон о рекламе мед.услуг:
   7. Конкретный препарат С КОНКРЕТНОЙ ДОЗОЙ (мг, мл, IU): "Tirzepatide 0.5 mg/week", "Rapamycin 6 mg" → SKIP
   8. Пептиды, гормоны роста, стероиды, ноотропы с дозировками: CJC-1295, Ipamorelin, BPC-157, TB-500, тестостерон, прогестерон, мелатонин (выше 1 мг)
   9. Off-label применение рецептурных препаратов: "я колю Ozempic для похудения"
   10. Самолечение биодобавками с дозами выше суточной нормы

D) НЕТ ЦЕННОСТИ:
   11. Чистое нытьё/жалобы БЕЗ практического вывода
   12. Обрывок мысли без объяснения
   13. Анонс собственного контента
   14. Реакция без своей мысли
   15. Реклама собственного курса/книги/добавки

При SKIP — верни ровно одно слово SKIP. Без шаблона, без ссылки, без объяснений.

=== АДАПТАЦИЯ ===

Если ничего из A/B/C/D — адаптируй. Деликатные темы (политика, секс, ЛГБТ) — нейтрально, фактологично, без оценок.

=== АБСОЛЮТНО ЗАПРЕЩЕНО ===

❌ "Автор считает", "Автор твита делится", "По мнению автора". Излагай мысль АВТОРА от своего имени, как утверждение.

❌ Превышать 400 знаков. Сокращай.

❌ Markdown. Только HTML: <b>, <i>, <a href="">.

❌ Передавать названия препаратов и веществ из категорий B и C, даже если они уже без дозировки. Это след, по которому подписчик найдёт небезопасную информацию.

=== ПРИМЕРЫ ===

Плохо: "Tirzepatide в дозе 0.5 mg/week вызывает учащение пульса"
Хорошо: SKIP

Плохо: "5-МеО-ДМТ включил у меня переключатель между я и другими"
Хорошо: SKIP

Плохо: "Автор твита Bryan Johnson считает, что..."
Хорошо: "Миф о том, что загар яичек повышает тестостерон, не подтверждён."

=== ПРАВИЛА ОФОРМЛЕНИЯ ===
1. Русский, живой, без канцелярита.
2. Цифры и факты сохраняй (кроме доз препаратов).
3. 200–400 знаков.
4. Без эмодзи, если их не было в оригинале.
5. Убирай хештеги, @mentions.
6. НЕ "Автор считает X", а "X" как утверждение.

В конце ОБЯЗАТЕЛЬНО:
<a href="URL_твита">→ Источник</a>

Верни либо ровно SKIP, либо готовый пост в HTML."""


# HTTP-коды, на которых ретраить имеет смысл
RETRYABLE_HTTP_CODES = {408, 429, 500, 502, 503, 504}


class _RetryableError(Exception):
    pass


class _NonRetryableError(Exception):
    pass


@dataclass
class DigestTweet:
    """Твит для дайджеста."""

    username: str
    text: str
    url: str
    likes: int
    retweets: int


class OpenRouterClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        max_attempts: int = 3,
        base_delay: float = 2.0,
        timeout: int = 120,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.timeout = timeout
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/your-repo",
            "X-Title": "TwidgestBot",
        }

    # ------------------------------------------------------------------ #
    # Public methods
    # ------------------------------------------------------------------ #
    async def rewrite_tweet(
        self, tweet_text: str, tweet_url: str, author: str
    ) -> str | None:
        """Single-режим: один твит → один пост. None если SKIP или ошибка."""
        user_prompt = (
            f"Автор: @{author}\n"
            f"URL: {tweet_url}\n\n"
            f"Текст твита:\n{tweet_text}"
        )
        result = await self._call_with_retry(
            SINGLE_SYSTEM_PROMPT, user_prompt, max_tokens=500
        )
        if not result:
            return None

        clean = result.strip()
        # Проверка на SKIP в любой форме
        first_token = (
            clean.split()[0].upper().strip(".,;:!?<>[]")
            if clean.split()
            else ""
        )
        if first_token == "SKIP":
            return None
        # Защита от "SKIP\n→ Источник" и подобного мусора
        if clean.upper().startswith("SKIP") and len(clean) < 80:
            return None
        # Защита от мета-ответов LLM (когда модель обращается к юзеру)
        meta_markers = (
            "я не вижу", "пожалуйста скопируйте", "пожалуйста, скопируйте",
            "напишите текст", "не вижу текст", "i don't see", "please provide",
            "i cannot", "i'm sorry", "as an ai", "как ии",
        )
        if any(m in clean.lower() for m in meta_markers):
            return None
        return clean

    async def build_digest(
        self, tweets: list[DigestTweet], niche: str = "longevity"
    ) -> str | None:
        """Digest-режим: N твитов → один пост-дайджест."""
        if not tweets:
            return None

        system_prompt = (
            LONGEVITY_DIGEST_SYSTEM_PROMPT
            if niche == "longevity"
            else GENERIC_DIGEST_SYSTEM_PROMPT
        )

        blocks: list[str] = []
        for i, tw in enumerate(tweets, start=1):
            blocks.append(
                f"[Твит #{i}]\n"
                f"Автор: @{tw.username}\n"
                f"URL: {tw.url}\n"
                f"Лайки: {tw.likes}, Ретвиты: {tw.retweets}\n"
                f"Текст: {tw.text}"
            )
        user_prompt = (
            f"Вот {len(tweets)} твитов за последний период. "
            "Составь из них дайджест по формату из системного промпта. "
            "Выбирай лучшие 3–5 пунктов, остальные игнорируй.\n\n"
            + "\n\n---\n\n".join(blocks)
        )

        result = await self._call_with_retry(
            system_prompt, user_prompt, max_tokens=1500
        )
        if not result:
            return None

        # Защита от мета-ответов: LLM может пожаловаться на качество твитов
        # вместо генерации дайджеста — это нельзя постить в канал
        clean = result.strip().lower()
        meta_markers = (
            "к сожалению", "извините", "не могу составить", "не подходят для",
            "пожалуйста, предоставьте", "недостаточно информации", "не вижу",
            "i'm sorry", "i cannot", "i apologize", "unfortunately the tweets",
            "please provide", "the provided tweets", "i am unable",
        )
        if any(m in clean for m in meta_markers):
            logger.warning("Digest LLM returned meta-response, skipping. Preview: %s", result[:200])
            return None

        # Дайджест должен начинаться с эмодзи или <b> заголовка
        # Если первая строка не похожа на заголовок — тоже мета-ответ
        first_line = result.strip().split("\n")[0].strip()
        has_format_marker = any(m in first_line for m in ("🧬", "🌐", "📰", "<b>", "<i>", "**"))
        if not has_format_marker and len(first_line) > 80:
            logger.warning("Digest format invalid (no header marker). Preview: %s", first_line[:100])
            return None

        return result

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    async def suggest_sources(
        self, topic_description: str, count: int = 12
    ) -> list[dict[str, str]] | None:
        """Запрашивает у LLM топ X-аккаунтов по теме. Возвращает [{username, reason}, ...]."""
        system = (
            "Ты эксперт по контенту в социальной сети X (Twitter). "
            "Тебе нужно подобрать топ-аккаунтов по заданной теме. "
            "Отвечай СТРОГО валидным JSON-массивом, без преамбулы и комментариев. "
            "Формат каждого элемента: "
            "{\"username\": \"имя_без_собачки\", \"reason\": \"короткое объяснение\"}. "
            "Только реальные публичные активные аккаунты. Никаких выдуманных."
        )
        user = (
            f"Тема канала: {topic_description}\n\n"
            f"Подбери {count} аккаунтов в X, которые наиболее релевантны теме "
            "и активно постят по ней. Для каждого укажи короткое объяснение (1 предложение).\n\n"
            "Верни ТОЛЬКО JSON-массив, без markdown и комментариев."
        )

        result = await self._call_with_retry(system, user, max_tokens=2000)
        if not result:
            return None

        clean = result.strip()
        if clean.startswith("```"):
            lines = clean.split("\n")
            clean = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])

        try:
            data = json.loads(clean)
        except Exception:
            logger.warning("LLM returned invalid JSON for suggest_sources: %s", clean[:300])
            return None

        if not isinstance(data, list):
            return None

        # Валидация
        result_list = []
        for item in data:
            if not isinstance(item, dict):
                continue
            username = str(item.get("username", "")).lstrip("@").strip()
            reason = str(item.get("reason", "")).strip()
            if username and len(username) <= 32:
                result_list.append({"username": username, "reason": reason or ""})
        return result_list if result_list else None


    async def suggest_search_queries(
        self, topic_description: str, count: int = 6, temperature: float = 0.3
    ) -> list[str] | None:
        """Generates search queries for X. Temperature param allows multi-shot variation."""
        system = (
            "Ты помогаешь искать релевантные аккаунты в X (Twitter). "
            "Для описанной темы канала придумай РАЗНООБРАЗНЫЕ ПОИСКОВЫЕ ЗАПРОСЫ. "
            "Не придумывай username'ы — мы найдём реальные аккаунты через поиск. "
            "\n\n"
            "ОБЯЗАТЕЛЬНО на АНГЛИЙСКОМ (Twitter Search лучше работает с английскими keywords). "
            "Даже если тема на русском — запросы переведи. "
            "\n\n"
            "Каждый запрос — 1-3 слова, короткие, популярные термины из ниши. "
            "Охватывай разные грани темы: общие термины, специфические, жаргон, профессии, издания. "
            "Для спорта добавляй названия команд/лиг. Для IT — технологии и роли. "
            "\n\n"
            "Отвечай строго JSON-массивом строк, без markdown, без преамбулы."
        )
        user = (
            f"Тема канала: {topic_description}\n\n"
            f"Дай {count} ОЧЕНЬ КОРОТКИХ поисковых запросов в X (на английском). "
            f"СТРОГИЕ ПРАВИЛА:\n"
            f"- Каждый запрос: МАКСИМУМ 2 слова, идеально 1 слово\n"
            f"- Это бренды, ОРГАНИЗАЦИИ, ИЗДАНИЯ, имена лиг/инструментов\n"
            f"- Хотя бы 2 запроса про сами организации/издания (для футбола: 'premier league', 'BBC sport')\n"
            f"- Хотя бы 2 запроса про ключевые сущности (команды/имена/технологии)\n"
            f"- Не описания и не действия!\n\n"
            f"ПЛОХО: 'Premier League predictions', 'football match commentary', 'EPL analysis'\n"
            f"ХОРОШО для футбола: 'premier league', 'EPL', 'sky sports', 'BBC sport', 'football news'\n"
            f"(добавь конкретные клубы только если тема узкая, для общих лиг — давай ИЗДАНИЯ и ОФИЦИАЛЬНЫЕ источники)\n\n"
            f"ПЛОХО: 'venture capital insights', 'startup founder advice'\n"
            f"ХОРОШО: 'YC', 'a16z', 'sequoia', 'paul graham', 'startup', 'venture'\n\n"
            f"ПЛОХО: 'machine learning research', 'AI safety news'\n"
            f"ХОРОШО: 'OpenAI', 'Anthropic', 'DeepMind', 'LLM', 'AI'"
        )

        result = await self._call_with_retry(system, user, max_tokens=500, temperature=temperature)
        if not result:
            return None

        clean = result.strip()
        if clean.startswith("```"):
            lines = clean.split("\n")
            clean = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])

        try:
            data = json.loads(clean)
        except Exception:
            logger.warning("LLM returned invalid JSON for suggest_search_queries: %s", clean[:300])
            return None

        if not isinstance(data, list):
            return None

        queries = []
        for item in data:
            if isinstance(item, str) and 2 <= len(item.strip()) <= 80:
                queries.append(item.strip())
        return queries if queries else None

    async def _call_with_retry(
        self, system_prompt: str, user_prompt: str, max_tokens: int,
        temperature: float = 0.3,
    ) -> str | None:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        last_error = "unknown"
        for attempt in range(1, self.max_attempts + 1):
            try:
                result = await self._single_request(payload)
                if result is not None:
                    if attempt > 1:
                        logger.info(
                            "OpenRouter succeeded on attempt %d/%d",
                            attempt,
                            self.max_attempts,
                        )
                    return result
                last_error = "empty or invalid response"
            except _NonRetryableError as exc:
                logger.error("OpenRouter non-retryable: %s", exc)
                return None
            except _RetryableError as exc:
                last_error = str(exc)
                logger.warning(
                    "OpenRouter attempt %d/%d failed: %s",
                    attempt,
                    self.max_attempts,
                    exc,
                )

            if attempt < self.max_attempts:
                delay = self.base_delay * (2 ** (attempt - 1))
                await asyncio.sleep(delay)

        logger.error(
            "OpenRouter exhausted %d attempts. Last: %s",
            self.max_attempts,
            last_error,
        )
        return None

    async def _single_request(self, payload: dict[str, Any]) -> str | None:
        try:
            async with aiohttp.ClientSession(headers=self._headers) as session:
                async with session.post(
                    OPENROUTER_URL, json=payload, timeout=self.timeout
                ) as resp:
                    body = await resp.text()
                    if resp.status == 200:
                        try:
                            data = json.loads(body)
                        except Exception as exc:
                            raise _RetryableError(f"bad JSON: {exc}") from exc
                        return self._extract_content(data)

                    snippet = body[:300]
                    if resp.status in RETRYABLE_HTTP_CODES:
                        raise _RetryableError(f"HTTP {resp.status}: {snippet}")
                    raise _NonRetryableError(f"HTTP {resp.status}: {snippet}")
        except asyncio.TimeoutError as exc:
            raise _RetryableError(f"timeout after {self.timeout}s") from exc
        except aiohttp.ClientError as exc:
            raise _RetryableError(f"network error: {exc}") from exc

    @staticmethod
    def _extract_content(data: dict[str, Any]) -> str | None:
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            logger.warning("Unexpected response shape: %s", str(data)[:300])
            return None
        if not isinstance(content, str):
            return None
        content = content.strip()
        return content or None
