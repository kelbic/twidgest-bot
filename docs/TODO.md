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

## Customizable value filter (post-MVP, high impact)

**Problem:** Current value filter is hardcoded for "newsworthy" content.
It rejects 95%+ of content for channels where the natural format is
different (fan accounts, memes, personal opinions, sport reactions).
Real example from production: F1 channel — all 7 sources are official
team accounts that post mostly retweets, sponsor announcements, and
match reactions. Filter rejects all of them as "not newsworthy".

### Filter preset templates
- [ ] `news` — current behavior (factual events, no opinions/reactions)
- [ ] `community` — accepts opinions, reactions, polls, fan content
- [ ] `expert` — accepts personal takes from credible sources
- [ ] `entertainment` — accepts memes, jokes, light content
- [ ] `analytical` — only data-rich, long-form posts

Each preset = different prompt for `rewrite_tweet` LLM call.

### Per-channel filter config
- [ ] `Channel.filter_preset` column (default: 'news' to preserve current)
- [ ] `/setfilter <channel_id> <preset>` command
- [ ] `/filters` command — list available presets with descriptions
- [ ] Hint in `/status` showing current preset

### Custom prompt (Pro+ only)
- [ ] `Channel.custom_filter_prompt` (TEXT, nullable)
- [ ] `/setcustomfilter <channel_id>` — multi-step dialog to enter prompt
- [ ] Validation: max 500 chars, no jailbreak attempts
- [ ] Falls back to preset if custom returns broken output

### Onboarding integration
- [ ] When creating channel via /createchannel ai, ask user about
  desired content type (news / opinions / mixed)
- [ ] AI suggests preset based on description (e.g. "memes" → entertainment preset)
- [ ] Show preset choice in channel creation confirmation

### Migration safety
- [ ] All existing channels get `filter_preset='news'` (preserve current behavior)
- [ ] Document in user notification when feature ships:
  "Your channels keep the strict news filter — change with /setfilter"

## Filter presets (attempted, rolled back — see below)

**Status:** infrastructure prepared, not wired up. Reverted late at night
because regex-based prompt patching kept failing, and risk of breaking
production was too high.

**Already done (safe to use later):**
- `filter_presets.py` module with 3 presets: news/community/entertainment
- `Channel.filter_preset` column in DB (defaults to 'news')
- All existing channels migrated with default 'news' preset

**TODO when picking up:**
- Refactor `core/llm_client.py` so SINGLE_SYSTEM_PROMPT becomes a function
  `build_single_system_prompt(preset_code)` — better to do this cleanly
  with proper string templating, NOT regex on multi-line constant
- Wire up `rewrite_tweet(filter_preset=...)` parameter
- Pass `channel.filter_preset` from collector and viral_picker
- Add `/setfilter <id> <preset>` and `/filters` commands
- Update `/createchannel ai` to suggest preset based on description
- Update landing + USER_GUIDE with filter presets explanation

**Lesson learned:** never refactor multi-line string constants late at night
with regex. Either rewrite the file cleanly in one editor session, or skip.

## TGStat integration for Russian regional/niche content (post-MVP)

**Why:** Twitter API doesn't cover Russian regional or narrow Russian-language
niches (Cheboksary news, Russian astrology, Tarot in RU, etc.) — verified
in production with multiple test channels returning 0 candidates.
TGStat covers Russian Telegram channels, which is where this content lives.

**Pricing options:**
- Free tier: 500 req/day (insufficient for SaaS)
- Standard: 990₽/mo, 5000 req/day (~50-100 active channels)
- Premium: 4990₽/mo, 50K req/day + content search

**Legal considerations:**
- Public channels: OK under ГК РФ ст. 1276 (publicly accessible content)
- Mandatory: source attribution (we already do via "→ Источник" link)
- LLM-rewrite of original content: transformative use, OK
- Risk: closed channels (skip them), full text re-publication (we adapt, not copy)
- Required: add "fair use" clause to /legal
- Required: user agreement that they have rights to selected sources
- Required: DMCA-style complaint handling

**Implementation plan:**
- Separate pipeline `core/tgstat_client.py`
- AI generates Russian keywords for TGStat search instead of English X keywords
- Hybrid mode: try Twitter first, fallback to TGStat if 0 results
- New tier feature: "Pro+ Russia" or "TGStat add-on" — separate paid feature
- Trigger: only after first 3-5 paying users to validate demand

**Trigger to start:** when 3+ paying users specifically request Russian regional
content OR when Twitter coverage gap blocks 30%+ of /createchannel attempts.
