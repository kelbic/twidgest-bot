# Technical debt и planned improvements

## High priority (после первых 10 платящих)
- [ ] Alembic для миграций БД
- [ ] Составные индексы на `PostLog(user_id, posted_at)` и `ProcessedTweet(user_id, tweet_id)`
- [ ] Rate limiting middleware по user_id на команды-записи
- [ ] Signal handler для graceful shutdown APScheduler джобов
- [ ] Unit-тесты на LLM safety filter (false positive границы)

## Medium priority (50+ активных юзеров)
- [ ] Миграция SQLite → PostgreSQL
- [ ] Разделение bot process и workers process
- [ ] RSS-fallback источники (вместо только twitterapi.io)
- [ ] Lazy init с asyncio.Lock в session.py

## Low priority (когда понадобится)
- [ ] TypedDict для SendTarget вместо fake_target
- [ ] Кэширование tier в context'е цикла collector
- [ ] Broadcast: HTML validation перед отправкой
- [ ] PAYMENT_TEST_MODE для тестов платёжного flow

## AI-source selection — next iteration
- [ ] Hybrid approach: combine "LLM suggests known experts" + "Twitter Search finds active accounts". Search finds community/outlets, LLM names big-name experts (Paul Graham, Naval for startups — not findable by search).
- [ ] Validate LLM-suggested expert names via twitterapi.io (same pattern as before, just 2-3 names not 15).
- [ ] Cache search results per keyword (same keyword queried multiple times during testing).
- [ ] For mainstream niches (sports, politics, tech) — detect via niche-classifier and raise MIN_FOLLOWERS to 5000+.

## AI source selection — variance reduction
- [ ] Run 2-3 parallel keyword generations with different temperatures (0.3, 0.7, 1.0) and union results. Sometimes LLM generates good queries, sometimes bad ones. Multiple shots → much higher reliability than single shot.

## UX failure modes (before launch to real users)
- [ ] Silent failure: if channel has 0 posts after 24h despite active sources, notify owner with diagnostics (filter rejection rate, possible causes, suggested actions)
- [ ] Pre-create warning: detect "politically risky" topics in description and show warning before channel creation
- [ ] /channels should show last_post_at and rejection_rate per channel
- [ ] /channel_stats <id> command for detailed health check on demand

## Pictures (deferred)
- [ ] Tweet images: twitterapi.io does NOT return media URLs (verified Apr 2026).
  Options:
  - Use Unsplash API by topic keywords (chosen path if needed)
  - Scrape t.co URLs from oEmbed (against Twitter TOS)
  - Use other Twitter API provider that returns media
- [ ] /setbanner <channel_id> command — user uploads custom image to use as banner for all posts in channel

## Image rate limits (scale concern)
- Unsplash free tier: 50 req/hour. Enough for ~25-50 channels at typical activity.
- At 100+ active channels: need either paid Unsplash ($59/mo for 5000 req/h)
  OR cache keyword→URL mapping (24h TTL) to dedupe identical queries
  OR multi-provider fallback (Pexels, Pixabay)
- Current MVP: monitor Unsplash 403 responses, add cache when we hit them regularly

## Phase 2: After first 10 paying users (timeline-based)

### Week 2-3: Stabilization & feedback
- [ ] Insider Club closed Telegram chat for paying users
- [ ] Mini-survey in chat: "What's most useful? What's missing?"
- [ ] Collect 2-3 testimonials/cases for landing

### Week 3-4: Social proof
- [ ] Add "Cases" section to landing with quotes + numbers
- [ ] Update cold scripts: "Already helping N admins save time"
- [ ] Manual referral program: 20% off first month for friends

### Month 2: First paid acquisition
- [ ] Telegram Ads test (3000₽ budget) — only after 3+ public cases
- [ ] Partnerships with 2-3 SMM/automation niche channels
- [ ] vc.ru article: "Как я автоматизировал ведение Telegram-канала"

### Month 2-3: Product evolution based on feedback
Likely requests (in order of probability):
- [ ] More source types: RSS, Telegram-channels, news APIs
- [ ] Custom post style/templates per channel
- [ ] Per-channel analytics (subscriber growth, post engagement)
- [ ] Custom prompts for AI filter (advanced users)
- [ ] Higher tier: dedicated support + custom development
