# Claude Code — painminer build brief

Paste this as your first message in Claude Code, with
`painminer-design-v2.md` in the repo root.

---

## Prompt

Read `painminer-design-v2.md` in the repo root before writing any code. It is
the authoritative spec. Where this brief and the doc disagree, ask me.

You are building `painminer`: a personal aggregation system that fetches raw
text from public developer and startup sources, hands it to a local LLM that
decides what is worth surfacing, clusters recurring findings, and delivers a
short ranked digest to Telegram.

### Ground rules

- **Python 3.11+.** Standard tooling. No frameworks I didn't ask for — no
  LangChain, no LangGraph, no Celery, no Airflow. This is a staged pipeline
  with a Postgres-backed queue, not an agent system.
- **Machine A is an 8GB MacBook Air.** Page and write; never hold a full
  fetch in memory.
- **Every stage idempotent.** Re-running must produce the same result.
  Counts are the product; double-counting corrupts them.
- **No secrets in code.** Everything from `.env`, which already exists with
  `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `TELEGRAM_TOKEN`,
  `TELEGRAM_CHAT_ID`, `LLM_BASE_URL`, `LLM_MODEL`.
- **Do not invent API endpoints.** If you are not certain an endpoint,
  parameter or response shape is correct, say so and stop. I would rather
  verify than debug a hallucinated schema.
- Ask before adding any dependency beyond: `supabase`,
  `python-telegram-bot`, `openai`, `sentence-transformers`, `httpx`,
  `pydantic`, `python-dotenv`.

### Build in this order. Stop after each phase and tell me how to verify it.

**Phase 1 — schema and scaffolding**

Produce the SQL for `sources`, `items`, `findings`, `clusters`, `feedback`,
`runs` per §6 of the doc. Give it to me as a single script I paste into the
Supabase SQL editor — do not attempt to run DDL yourself.

Requirements: `pgvector` already enabled; `findings.embedding` is
`halfvec(384)`; unique constraint on `items(source, source_id)`;
`sources_seen` and `days_seen` on `clusters` are sets, not counters.

Also: a `db.py` with a thin typed wrapper over the Supabase client, and a
`config.py` loading `.env`. Nothing else.

*Verify:* I paste the SQL, it runs clean, and a smoke script inserts and
reads back one row.

**Phase 2 — source registry and the HN adapter**

Implement the registry from §3.1: an `adapters/` package with a base class
and one generic `json_api` adapter driven by a config row, not a
hand-written fetcher per site.

Then the Hacker News adapter using the Algolia API:
- `search_by_date`, `tags=comment` and `tags=story`
- Watermark from `sources.last_successful_fetch_at`, fetched from
  `watermark - 6h` (§5.1)
- Paged: fetch 100 → dedupe → write → drop page → advance
- Writes `items` with `state=fetched`

**The watermark advances only after the judge stage succeeds — not here.**

*Verify:* run it twice. The second run writes zero new rows. That proves the
unique constraint and the watermark both work.

**Phase 3 — dedupe and queue**

Structural dedupe only (§4.1): unique `(source, source_id)`, `content_hash`
match, minimum length, skip link-only. **No lexical filter, no embedding
filter** — these were deliberately removed in v2.

The queue per §4.2: claim with `state=processing` and a timestamp, reclaim
rows stuck over an hour, attempt counter with three strikes then `failed`.

*Verify:* kill the process mid-run; the next run picks up where it left off.

**Phase 4 — the judge**

One LLM call per `ready` item against Machine B. Use the exact prompt in the
appendix below — do not rewrite it, and do not "improve" it.

- Preflight `GET /v1/models` before any fetching; abort with a Telegram
  message naming VPN and Machine B if it fails
- One warm-up call before the batch (cold start is 20–30s)
- `reasoning_effort: "none"`, `temperature: 0`, 30s timeout
- `response_format` with a JSON schema — constrain at decode time
- Parse, validate, retry once with the error appended, then mark `failed`
- Null `items.raw_text` in the same transaction as judge success
- Respect `max_run_minutes` from config; on expiry stop cleanly and leave
  the watermark

*Verify:* run against 20 real HN items and print the raw model output next to
the source text. **I need to see empty arrays.** If every item produces a
finding, the prompt is broken and we fix it before continuing.

**Phase 5 — embedding and clustering**

`bge-small-en-v1.5` via `sentence-transformers`, MPS with CPU fallback,
batches of 32–64, model loaded once per run.

Embeddings are used for clustering only — never for filtering.

Clustering per §8: cosine search against centroids of the same `kind`,
auto-merge above 0.90, LLM tiebreak in the 0.75–0.90 band, new cluster below.
Anchor centroids on the first member; do not recompute as a running mean.
Dedupe mentions by `(cluster_id, source, date)`.

Thresholds go in config, not hardcoded.

*Verify:* feed two differently-worded versions of the same problem; they land
in one cluster.

**Phase 6 — ranking**

The formula in §9, `score_version` written on every run. Single-shot kinds
(`research`, `read`) ranked separately by recency and confidence per §9.1 —
they will never score under the recurrence formula.

**Phase 7 — Telegram bot**

`/scan`, `/top`, `/why <id>`, `/status`, `/kinds`. Inline buttons 🔥 / 🗑 / 👀
writing to `feedback`. Muted clusters excluded from `/top`. Run-status footer
on every message.

`/scan` runs the full pipeline and streams progress so I can see it working
rather than staring at silence for two hours.

*Verify:* I can run the whole thing from my phone.

**Phase 8 — second adapter**

Add GitHub as a registry entry plus one adapter file. **If this requires
touching anything else, the registry abstraction failed** — tell me and we
fix it before adding a third source.

### What not to do

- Don't build all sources at once. HN, then GitHub, then stop.
- Don't add a web dashboard. Telegram is the whole interface.
- Don't use browser automation or store platform logins.
- Don't write to `.env` or commit it.
- Don't silently swallow exceptions. A source returning zero must be visible
  in `/status`, not invisible.

---

## Appendix — the judge prompt

Use verbatim as the system prompt.

```
You are reading raw text from public developer and startup communities on
behalf of one person: a machine learning engineer who is building toward
founding a company within a few years, thinks seriously about system
architecture, and follows research.

Your job is to decide whether this text contains anything genuinely useful
to that person, and if so, to extract it.

MOST TEXT CONTAINS NOTHING USEFUL. Returning an empty array is the normal,
correct answer. Do not manufacture a finding because you were asked to look
for one. A plausible-sounding finding that would waste this person's time is
worse than no finding at all.

Things that may be worth surfacing:

- pain: a specific problem, manual workaround, or unmet need someone
  describes in their actual work
- build: something being built — a repo, launch, side project, proof of
  concept
- arbitrage: something working in one market that is absent in another
  (e.g. exists in the US, not in India)
- research: a paper, result, or research direction worth knowing about
- pattern: an architectural or engineering pattern worth internalising
- read: an article genuinely worth this person's time
- signal: something shifting — funding, hiring, deprecation, a platform
  changing its terms

This list is not exhaustive. If something is clearly valuable but fits none
of these, use your own short kind label.

One piece of text may contain several findings of different kinds. A post
saying "I was so frustrated with X that I built Y" is both a pain and a
build. Emit both.

Rules for statements:

- Be specific. "Solo freelancers manually copy Stripe payouts into
  accounting spreadsheets every week" is useful. "Invoicing is painful" is
  not.
- Write the statement in your own words, normalized, so that two people
  describing the same underlying thing produce similar statements.
- evidence_quote must be copied verbatim from the source text. Never
  invent, paraphrase, or reconstruct a quote.
- confidence reflects how sure you are this is real and worth surfacing,
  not how confident the author sounded.

Return a JSON array. Each element:

{
  "kind": "pain",
  "statement": "...",
  "why_it_matters": "one line",
  "domain": "...",
  "evidence_quote": "verbatim from source",
  "confidence": 0.0-1.0
}

Return [] when there is nothing worth surfacing.
```

### Few-shot negatives to append

Include these as examples that correctly return `[]`:

- A comment arguing about programming language syntax preferences
- A comment complaining that a framework's documentation is bad
- A general observation that hiring engineers is hard right now
- A joke, a pun, or off-topic banter
- A comment agreeing with another comment and adding nothing
- Career advice with no specific problem attached

### Few-shot positive

Source text:

> I was so frustrated with GitHub that I rebuilt it as a Chrome extension.
> You arrive on your main page, you see your PRs with different statuses,
> the CI, the number of comments, the diff. I reverse engineered the
> queries and now everything feels instant.

Expected output: **two** findings — a `pain` about GitHub's PR review
requiring separate page loads for CI, diff and comments, and a `build` about
the Chrome extension. This example exists to teach multi-finding extraction
from a single item.
