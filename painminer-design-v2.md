# painminer — System Design v2

**Supersedes v1.** Core change: the LLM is the decider, not a stage in a
filter chain. Machine A is plumbing.

---

## 0. What changed from v1

| v1 | v2 | Why |
|---|---|---|
| Cascade filter: structural → lexical → embedding | Structural dedupe only | Cost-driven filtering solved a problem that doesn't exist at 4h/night runtime |
| `bge-small` used for filtering **and** clustering | Embeddings used for clustering **only** | Filtering was the LLM's job; clustering is a similarity computation the LLM can't do cheaply |
| 20–30 hand-written pain exemplars | Deleted | Taste belongs in the prompt, not in a vector filter |
| `kind` ∈ {demand, supply} | Open taxonomy | The interesting thing is often neither |
| Fixed source list | Source registry | Adding a source should be a config entry, not code |

---

## 1. Premise

**The system is an aggregator. The LLM is the judge.**

Machine A fetches from as many places as possible, dedupes, and hands raw
text to the model. The model decides what is worth surfacing and why.
Everything downstream is bookkeeping.

**The reader is:** a startup-minded, research-minded ML engineer who also
thinks about system design. Anything genuinely useful to that person is in
scope.

**Success condition:** the operator reads it without effort. Unopened for
three months → kill it.

---

## 2. What counts as a finding

Not just pain points. The model returns anything in these classes:

| `kind` | What it is |
|---|---|
| `pain` | A stated problem, workaround, or unmet need |
| `build` | Something being built — repo, launch, POC, side project |
| `arbitrage` | Works in one market, absent in another (e.g. US → India) |
| `research` | A paper, result, or research direction worth knowing |
| `pattern` | An architectural or engineering pattern worth internalising |
| `read` | An article genuinely worth the operator's time |
| `signal` | Something shifting — funding, hiring, deprecation, platform change |

The taxonomy is **open**. The model may emit a `kind` not on this list; new
values are accepted and reviewed later rather than rejected at write time.
A rigid enum would discard exactly the findings that don't fit existing
categories, which are the ones worth having.

**One item may yield several findings of different kinds.** A post saying
"I was so frustrated with GitHub's PR review that I built a Chrome
extension" is simultaneously a `build` and a `pain`. Do not force one kind
per item.

---

## 3. Sources

### 3.1 Design rule

Sources live in a **registry** — a config table or file, not code. Adding a
source means adding an entry: name, adapter type, endpoint/URL, cursor field,
cadence hint. One generic fetcher per adapter type (`json_api`, `rss`,
`crawl`), not one fetcher per site.

This is the scalability that matters here. Ten sources should not mean ten
codebases.

### 3.2 Starting set

Verified free, no auth:

| Source | Adapter | Notes |
|---|---|---|
| Hacker News (Algolia) | json_api | Comments, stories, Ask HN, Show HN. Primary volume |
| GitHub Issues / Trending | json_api | `wontfix`, high-👍 feature requests, trending repos |
| GH Archive | bulk | Every public GitHub event. Earliest build signal |
| arXiv | json_api | cs.LG / cs.CL / cs.AI. Research direction |
| Hugging Face | json_api | Trending models and datasets |
| Lobsters | rss/json | Lower volume, higher signal-to-noise than HN |
| dev.to | json_api | Practitioner writeups |
| Stack Overflow | json_api | High-vote unanswered questions are pure unmet need |

Needs a crawler (Crawl4AI):

| Source | Notes |
|---|---|
| Y Combinator companies / RFS | What YC thinks is fundable right now |
| Product Hunt | Launches |
| G2 / Capterra | 2–3 star reviews. Paying customers naming the broken feature |
| Upwork postings | Pain quantified in currency |
| Indie Hackers | Small-scale builds and revenue reports |

**Verify each endpoint before building its adapter.** Terms, rate limits and
auth requirements change; several of the above were free historically and
should be re-checked at implementation time, not assumed.

### 3.3 Reddit

Blocked. Reddit closed self-service OAuth registration in November 2025
under the Responsible Builder Policy; approval now requires a support ticket
and is frequently refused. The `/prefs/apps` form still renders but silently
fails. Unauthenticated `.json` endpoints were removed in May 2026.

A request has been filed. **Reddit is not a dependency.** If approved, it is
one registry entry.

### 3.4 Access preference order

1. **Query** — ask the platform for a pattern via its search endpoint
2. **Subscribe** — RSS and feeds. No keys, no limits
3. **Bulk** — GH Archive and similar
4. **Crawl** — Crawl4AI, only where no API exists

No browser agents. No session-based scraping. No stored platform logins.

---

## 4. Pipeline

```
fetch → structural dedupe → LLM judge+extract → embed → cluster → rank → deliver
```

| Stage | Reads | Writes | On failure |
|---|---|---|---|
| Fetch | registry sources | `items(state=fetched)` | retry source next run |
| Dedupe | `fetched` | `ready` / `duplicate` | — |
| Judge | `ready` | `findings` (0..n) | retry once, then `failed` |
| Embed | `findings` | `embedding` | retry |
| Cluster | `findings` | `clusters` | leave unclustered |
| Rank | `clusters` | `score` | idempotent recompute |

### 4.1 Structural dedupe — the only pre-LLM filter

- Unique on `(source, source_id)` — already seen, skip
- `content_hash` match — same content from another source, skip
- Below minimum length (very short comments carry nothing)
- Link-only posts with no body text

That's all. **No lexical filter. No embedding filter.** Everything else
reaches the model.

### 4.2 Queue invariants

1. **Claim, don't read.** Set `state=processing` with a timestamp before
   working. Rows stuck >1h are reclaimed.
2. **Attempt counter on every row.** Three failures → `failed`. One
   malformed item must never block the queue.
3. **Every stage idempotent.** Re-running produces the same result.

### 4.3 Memory discipline

Machine A is 8GB. Page and write: fetch 100 → dedupe → write → drop page →
advance cursor. Never hold a full fetch in memory. This holds regardless of
gap size.

---

## 5. Execution model

**No cron. No cadence.** The operator fires `/scan` when both machines are
up.

### 5.1 Watermarks

`sources.last_successful_fetch_at` per source. Each fetcher asks "what
changed since my watermark?" Ran yesterday → 1 day. Ran last week → 7 days.
Never run → initialised to `now - 30d`, which *is* the backfill. One code
path.

**Rules:**

- **The watermark advances only after the judge stage succeeds**, never
  after fetch. A mid-run crash must not mark unprocessed data as seen.
- **Overlap the window:** fetch from `watermark - 6h`. A post from three days
  ago may gain its first useful comment today. The unique constraint makes
  re-fetched duplicates free.
- **Timestamp field differs per source.** HN comments → `created_at`.
  GitHub issues → `updated_at` (an issue that gained 40 👍 this week must
  resurface). Crawled sources (G2, Upwork, YC) have **no stable timestamp** —
  they use content-hash dedupe against a fixed page set instead. Two
  mechanisms, not one.

### 5.2 Run budget

The operator will start a run and leave the machines running for hours.
So the cap is **wall-clock, not item count**: a configurable
`max_run_minutes`. On expiry: stop cleanly, leave the watermark, next run
continues.

**Resume is free.** Rows persist in `ready`. Closing the laptop mid-run loses
nothing.

---

## 6. Data model

### `sources`
The registry. `name`, `adapter` (json_api|rss|crawl|bulk), `config_json`,
`cursor_field`, `last_successful_fetch_at`, `enabled`

### `items`
`id`, `source`, `source_id`, `url`, `raw_text`, `content_hash`, `state`,
`attempts`, `fetched_at`

- Unique on `(source, source_id)`
- `raw_text` nulled **in the same transaction** as judge success, not by a
  later cleanup job

### `findings`
One row per thing the model surfaced. An item yields zero, one, or many.

`id`, `item_id`, `kind`, `statement`, `why_it_matters`, `domain`,
`evidence_quote`, `confidence`, `embedding` (halfvec 384), `cluster_id`

No author field. Author identity is deliberately not stored.

### `clusters`
`id`, `kind`, `canonical_statement`, `centroid`, `sources_seen` (set),
`days_seen` (date set), `mention_count`, `first_seen`, `last_seen`, `score`,
`score_version`, `muted`

**Sets, not counters.** A counter cannot distinguish five mentions across
five days from five in one afternoon. Set cardinality is the signal.

**Centroid: anchor on the first member.** Do not recompute as a running mean
— means drift and clusters wander until they absorb everything. A periodic
job flags any cluster holding >5% of all findings as a split candidate.

### `feedback`
Append-only. `cluster_id`, `verdict` (interesting|kill|watch),
`features_json`, `score_version`, `created_at`

### `runs`
`id`, `started_at`, `finished_at`, `items_fetched`, `findings_created`,
`errors`

---

## 7. The judge prompt

This is the system. Everything else serves it.

### 7.1 Framing

The prompt describes **the reader**, not a list of things to find:

> You are reading raw text from public developer and startup communities on
> behalf of one person: a machine learning engineer who is building toward
> founding a company, thinks seriously about system architecture, and
> follows research. Surface anything genuinely useful to that person.

Then the `kind` taxonomy from §2 as guidance, explicitly marked non-exhaustive.

### 7.2 Output schema

```json
[
  {
    "kind": "pain",
    "statement": "GitHub's PR review UI requires page loads to see CI status, diff, and comments separately",
    "why_it_matters": "Someone cared enough to reverse-engineer the queries and ship a faster client",
    "domain": "developer-tools",
    "evidence_quote": "verbatim from the source",
    "confidence": 0.8
  }
]
```

- `statement` — normalized, in the model's words. **This is what gets
  embedded and clustered**, so its phrasing determines whether clustering
  works at all.
- `why_it_matters` — one line. This is what makes the feed readable rather
  than a list of facts.
- `evidence_quote` — verbatim. Never invented.
- **Empty array is valid and expected.**

### 7.3 The one hard rule

**Most items are nothing. Returning `[]` is the default, correct answer.**

Nothing upstream protects against false positives any more — the lexical and
embedding filters are gone. A model told to find something interesting will
find something in every post. This single instruction is now the highest-
leverage line in the system.

Enforce with few-shot examples of unremarkable text returning `[]`.

### 7.4 Calibration

- Fine-grained statements. "Solo freelancers manually copy Stripe payouts
  into accounting spreadsheets weekly" — not "invoicing is painful."
- **Accepted consequence:** fine granularity dedupes poorly, so top items may
  have 3 mentions, not 40. This is correct. Three people describing an
  identical broken step beats forty vaguely agreeing.
- One item may yield several findings of different kinds.

### 7.5 Failure contract

1. Parse and validate against schema
2. On failure: retry once with the parse error appended
3. On second failure: mark item `failed`, move on

Use LM Studio's `response_format` with a JSON schema — constrain at decode
time rather than asking nicely. This matters for a low-active-parameter MoE
model, which drifts on strict format adherence more than a dense model of
equivalent size.

Never let one bad response stall the queue.

### 7.6 Network contract

- Preflight `GET /v1/models` before any fetching. Abort with a clear Telegram
  message naming VPN and Machine B if it fails.
- One warm-up call before the batch (cold start is 20–30s).
- 30s timeout, three attempts, then `failed`.
- Always send `reasoning_effort: "none"` — thinking is on by default on this
  build and consumes ~95% of tokens. **Revisit once running:** judgment
  quality may justify the cost on a subset.

---

## 8. Clustering

Embeddings serve **one purpose only**: recognising that two findings describe
the same thing. This is not filtering and cannot be delegated to the LLM —
comparing each new finding against every existing cluster is thousands of
comparisons per run, and it is a similarity computation, not a judgment.

For each new finding: embed `statement`, cosine search against cluster
centroids of the same `kind`.

| Similarity | Action |
|---|---|
| > ~0.90 | Auto-merge |
| ~0.75–0.90 | LLM tiebreak: "same underlying thing, yes/no" |
| < ~0.75 | New cluster |

Thresholds are **starting points to tune against real data**.

Pure threshold matching fails both ways — it fragments everything or
collapses everything into one cluster called "software is frustrating." The
ambiguous band is what the tiebreak is for.

**Mention dedupe:** by `(cluster_id, source, date)` before incrementing.
Two comments in one HN thread describing the same problem is one mention.

---

## 9. Ranking

```
score = (2 × |sources_seen|)
      + (1.5 × |days_seen|)
      + log₂(1 + mention_count)
      + (2 if last_seen within 7 days else 0)
```

- **`log₂` on mentions** caps a runaway thread. 40 mentions scores 5.4, not
  40. Volume nudges; it cannot dominate. It is the weakest signal.
- **Breadth and persistence linear** — bounded and hard to fake.
- **Recency is a flat bonus, not decay.** Decay means constant re-sorting and
  a leaderboard that never feels stable.

Worked example: 3 platforms / 5 days / 8 mentions / recent → 18.7.
1 platform / 1 day / 30 mentions / recent → 10.5.

**Platform counting is raw.** Overlapping populations (HN and GitHub are the
same engineers) accepted, not corrected.

### 9.1 Single-shot findings

Research papers and articles are often `|sources_seen| = 1, |days_seen| = 1`
by nature and will never rank under this formula. Give `research` and `read`
kinds a **separate section in the digest**, ranked by recency and confidence
rather than by recurrence. Recurrence scoring applies to `pain`, `build`,
`arbitrage` and `signal`.

### 9.2 Replacement path

These weights are a guess with no empirical basis; they are meant to be
replaced. Every `/top` writes feature values alongside the cluster ID; every
button press writes a label. After ~200 labels, fit a logistic regression
over the same features.

**Keep the features stable from day one.** Weights are disposable; the
feature set is not.

---

## 10. Delivery

**Channel:** Telegram. Free, no domain, no auth, no frontend, inline buttons,
pushes to phone.

**Design assumption: the operator will not open a dashboard.** Anything
requiring navigation is dead by week three.

### 10.1 Digest format

Hard cap **5 recurring items**, plus up to **3 single-shot** items
(`research` / `read`) in a separate section.

```
🔴 GitHub PR review requires page loads for CI, diff, comments
   Someone reverse-engineered the queries to ship a faster client
   "I was so frustrated with GitHub that I rebuilt it..."
   4 mentions · 2 sources · 3 new this week
   [link] [🔥] [🗑] [👀]

📄 Worth reading
   Retrospectively reverse-engineering Apple's Neural Engine

last run 2h ago · 1,240 fetched · 31 findings
```

### 10.2 Commands

| Command | Behaviour |
|---|---|
| `/scan` | Run now, from watermark |
| `/top` | Current leaderboard |
| `/why <id>` | All evidence and permalinks for a cluster |
| `/status` | Last run, counts, queue depth |
| `/kinds` | Breakdown by kind — also surfaces new kinds the model invented |

### 10.3 Buttons

| Button | Effect |
|---|---|
| 🔥 | Label interesting → `feedback` |
| 🗑 | **Mute the cluster permanently** |
| 👀 | Pin; resurface only when its count changes |

🗑 is the most important. Without permanent muting, rejected items keep
reappearing and trust in the feed collapses.

Reading the feed is how the feed gets trained.

---

## 11. Observability

**The failure mode that matters:** the pipeline silently stops — Wi-Fi
changed, LM Studio closed, a crawled site changed markup — and quiet output
is mistaken for "nothing interesting this week." A month is lost before
anyone notices.

**Minimum viable:** `runs` logs every run. Every Telegram message carries
`last run 2h ago · 1,240 fetched · 31 findings`. Zeros or a stale timestamp
are the alert. No dashboard.

Additionally: a per-source count in `/status`. A source silently returning
zero for a week means its adapter broke, and with a broad source list that
will happen regularly.

---

## 12. Infrastructure

**Machine A** (M2 Air, 8GB/256GB) — fetchers, dedupe, queue, `bge-small`
embeddings (MPS, CPU fallback), clustering, ranking, Telegram bot, all DB
access.

**Machine B** (M5 Pro, 32GB/1TB) — LM Studio serving `qwen-extract`
(Qwen3.6-35B-A3B, MLX 4-bit) at `http://192.168.1.104:1234/v1`. Stateless.

**Coupling is one config value: `LLM_BASE_URL`.** Swap for a friend's laptop,
a rented GPU, or a hosted API without touching anything else.

**Degradation:** if B is unreachable, A still fetches and dedupes; rows
accumulate in `ready`. The pipeline degrades, it does not fail.

| Component | Choice |
|---|---|
| Database | Supabase free tier — Postgres + pgvector ≥0.8.6 |
| Vectors | `bge-small`, 384-dim, `halfvec` |
| Crawling | Crawl4AI, self-hosted |
| Code | Private GitHub repo — code and daily digests only, never data |

**Known constraints:**
- Supabase free tier pauses after ~7 days idle. Given manual `/scan`, add a
  weekly keepalive query.
- Use the connection pooler (Supavisor), not the direct database port.
- Machine B: set a DHCP reservation. The IP will otherwise move and the
  pipeline dies with a connection timeout.
- Machine B: `caffeinate -dimsu` or sleep kills the server.
- **VPN on Machine A blocks LAN access to Machine B.** Preflight catches it.

**Storage:** raw text never persists. ~1.5KB per finding. Well inside 500MB
for years.

---

## 13. Build order

1. Schema + `.env` + source registry table
2. HN adapter (`json_api`) with watermark and paged writes
3. Queue and state machine
4. Judge stage against Machine B
5. Embedding + clustering
6. Ranking
7. Telegram bot — `/scan`, `/top`, buttons
8. Second adapter (GitHub) — **proves the registry abstraction works**
9. Remaining sources as registry entries
10. Reddit, if approved

Step 8 is the real test of §3.1. If adding GitHub requires touching anything
outside the registry and one adapter file, the abstraction failed and should
be fixed before adding source three.

---

## 14. Deliberately unresolved

- **Clustering thresholds** — tune against real findings
- **`max_run_minutes`** — set after timing a real batch on Machine B
- **`reasoning_effort`** — A/B on 20 items once running
- **Which `kind` values survive** — review what the model actually emits
  after two weeks and prune the taxonomy from evidence

---

## 15. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Operator stops reading | **Highest** | 5-item cap, mute button, kill at 3 months |
| Model surfaces something for every item | **High** | §7.3 — the empty-array rule, few-shot negatives |
| Statements too vague to cluster or act on | High | Fine granularity, flag oversized clusters |
| Broad source list means constant adapter breakage | Medium | Per-source counts in `/status` |
| AI-generated launch spam in build sources | Medium | Detect and drop |
| Reddit never approves | Low | Not a dependency |

**The second row is the one that kills this system.** v1 had three filters
standing between raw text and the feed. v2 has one instruction in a prompt.
That is the right trade for capability, but it concentrates all quality risk
in a single place — and the failure is silent, because a feed of plausible
nothing looks exactly like a feed of findings.

First real run: read 30 raw items yourself, compare against what the model
returned, and tune §7.3 before trusting the output.
