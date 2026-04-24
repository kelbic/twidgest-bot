# Building TwidgestBot: from idea to shipped SaaS in one day

This is the story of how TwidgestBot went from a completely unrelated idea to a production SaaS. Written as a reference for myself and as context for anyone evaluating this project.

---

## Starting point

I wanted to build games on Roblox. That was the whole plan. I asked for help turning Roblox game development into a source of income.

## The reality check

The analysis didn't confirm my idea — it challenged it:
- Roblox takes 75% of creator revenue (creators get 25%)
- Success requires 12+ months of marathon development
- The market is saturated with established developers
- Without an audience or a team, odds of breaking even are very low

Hard truth upfront saved weeks of misdirected work.

## Exploring alternatives

We compared platforms realistically:
- **Fortnite UEFN** — 100% revenue share window through 2026, but requires 3D skills and Unreal Engine
- **CurseForge / Overwolf** — 70% to creators, best entry for solo developers
- **Discord bots** — nichey, direct payments, 100% revenue
- **Telegram bots** — native Russian-speaking audience, easy payment integration

## Russia-specific constraints

Being a developer in Russia rules out Stripe, PayPal, and most Western payment rails. That narrows the options to:
- Telegram Stars (native currency, global reach)
- Crypto (technically works but low conversion for subscriptions)
- Relocating and registering abroad (out of scope right now)

Telegram + Stars won on every axis: payments work, audience is already there, distribution is native.

## Nailing down the product

Initial idea: a bot that auto-publishes translated X/Twitter content to Russian Telegram channels. Not glamorous, but:
- Clear pain point (Russian news channels manually translate Western content daily)
- Competitive moat (access to twitterapi.io at low cost vs. official Twitter API)
- Audience exists (channel admins already pay TGStat for analytics, would pay for this)

One important pivot mid-build: instead of selling a DIY bot "that users configure," shift toward **selling ready-made channels on any topic**. User says "I want a cricket channel" → bot creates it with AI-suggested sources. That turns a technical tool into a viral product.

## Technical decisions and why

**Multi-tenant from day one.** I was tempted to build a single-user bot first "just to test the idea." Resisted it. Refactoring single-user code into multi-tenant is one of the most painful engineering tasks, and the upfront cost was small (extra foreign keys, session-per-request).

**SQLite, not Postgres.** For the first 100+ paying users, SQLite handles everything. SQLAlchemy makes the migration to Postgres a one-line change when needed.

**APScheduler in the same process as the bot.** Could have gone with Celery + Redis for "proper" async workers. Didn't. One process is simpler to deploy, debug, and reason about. If I hit scheduler contention with 50+ users, I'll migrate. Not before.

**OpenRouter instead of direct OpenAI/Anthropic.** Russia-compatible billing, model switching via config change, automatic fallback if a provider goes down. Costs a few percent in API overhead for massive operational flexibility.

**Shared Twitter cache.** If 50 users monitor `@elonmusk`, the API gets called once per cycle, not 50 times. In-memory TTL cache with per-username async locks to prevent thundering herd on cold starts. Simple but crucial for unit economics.

**LLM as a filter, not a writer.** The LLM's primary job is deciding whether a tweet deserves to be published — quality gate, legal filter, deduplicator. The actual "writing" is secondary. This framing led to a much more robust prompt: explicit SKIP rules for Russian legal risks (military critique, drug mentions, medication dosages), value filters (no personal complaints, no self-promotion).

## Safety and legal considerations

The bot posts into Russian channels. That means it must not generate content that violates Russian law:
- No military/political criticism of RF institutions
- No drug or psychoactive substance mentions
- No specific medication dosages
- No LGBT content positioned positively (2023 RF law)

This is encoded directly in the system prompt, and defended in code: the rewrite function returns `None` if the LLM signals SKIP, which prevents any content from being posted. Multiple layers of defense, not just trust in the model.

## What broke during development (and how)

1. **Heredoc truncation in bash.** Several long `cat > file << 'EOF'` blocks silently failed, leaving files partially written. Solution: always `grep` for expected content after a patch, never trust that the command succeeded.

2. **Case-sensitive filenames on Linux.** Created `Config.py` when Python imports expected `config.py`. 10 minutes lost. Lesson: always use lowercase for Python modules.

3. **TwitterAPI.io response format.** My initial assumption of the JSON structure was wrong. Fix: instrumented the parser with a one-line log that dumps the raw response, then adjusted.

4. **LLM ignoring prompt rules.** Llama 3.3 70B passed through drugs and dosages despite explicit rules. Swap to Claude Haiku 4.5 immediately resolved it. Sometimes model quality matters more than prompt tuning.

5. **LLM meta-responses.** When passed a tweet with empty text, LLM occasionally replied "I don't see the tweet text, please paste it" — directly to the user's channel. Fixed by (a) short-circuiting empty inputs before they reach the LLM, (b) adding post-processing to detect and reject meta-responses.

6. **Python module cache vs systemd.** After a code change, `systemctl restart` didn't always pick up the new code. Resolution: verify the running file with `grep` before expecting new behavior.

## Things I deferred on purpose

Didn't do, and it was correct:
- Unit tests — for a solo MVP with no users, tests slow you down. Will add them when I have paying customers and fear regressions.
- Proper web dashboard — Telegram bot UI is enough for now.
- Content moderation dashboard — users manage their own channels.
- Multi-language support — one market (RU) first.
- Horizontal scaling — single process handles hundreds of users.

## Day-by-day metric

- Started: idea for Roblox game development
- Ended: shipped Telegram SaaS, 3000 LOC, deployed on systemd, public on GitHub
- Time: ~8 hours focused work
- Users: 1 (me)
- Payments: 0 (billing tested via admin grant)

## What I'd do differently

1. **Start the README earlier.** Writing documentation forces architectural clarity. I wrote it at the end and realized several design decisions needed retroactive justification.

2. **Test payments with a small real purchase.** Admin grant skipped the actual Stars flow. The first real purchase will reveal issues I haven't seen yet.

3. **More caution with long heredocs.** Several times a block was truncated silently. Should have split into `/tmp/new_file.py` + `cp` from the start, not as a recovery pattern.

## What's next

In order of priority:

1. Test the product for 2-3 days. Watch digests arrive, adjust prompts based on real output quality.
2. Write the landing page and launch content.
3. Build in Public — daily thread on what I'm fixing and learning.
4. Reach out to 5-10 channel admins in relevant niches for early feedback.
5. First real payment. That's when the SaaS is technically proven.

---

If you're reading this as a recruiter: this project was designed as proof that I can take a product from ambiguous idea through market analysis to shipped, production code with real architectural decisions. The code is open, the commit history is honest, and the bot is live. Happy to discuss any part of it.
