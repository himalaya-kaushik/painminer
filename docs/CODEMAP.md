# painminer — CODEMAP

Debugging-oriented map of the codebase. Authoritative design:
`docs/painminer-design-v2.md`; architecture overview: `docs/ARCHITECTURE.md`.
This file exists so you can open one page when something breaks and know
which function to look at.

---

## 1. Overview

painminer is a personal aggregation pipeline. Machine A (an 8GB laptop) fetches
public developer/startup text from a registry of sources (Hacker News, GitHub
Issues, Lobsters, Stack Overflow — more addable as config, not code), performs
cheap structural dedupe, and hands surviving raw text to a local LLM ("Machine
B", served by LM Studio) that judges what's worth surfacing and extracts
structured findings (`kind`, `statement`, `why_it_matters`, `evidence_quote`,
`confidence`). Findings are embedded with `bge-small-en-v1.5`, clustered by
cosine similarity (with an LLM tiebreak in the ambiguous band) into recurring
"clusters" that accumulate mentions across sources and days, ranked by a
breadth/persistence/recency formula, and delivered as a capped Telegram
digest with inline 🔥 (interesting) / 🗑 (mute) / 👀 (pin) feedback buttons.
Everything is stored in Supabase Postgres + pgvector; every stage is
idempotent and safe to rerun; a crash anywhere leaves data in a well-defined,
resumable state.

---

## 2. Pipeline data flow

```
fetch → structural dedupe → LLM judge+extract → embed → cluster → rank → deliver
```

| # | Stage | Module / function | Reads | Writes | Notes |
|---|---|---|---|---|---|
| 1 | Fetch | `painminer/pipeline/fetch.py :: fetch_source()` | `sources` row (registry + watermark); the adapter's remote API | `items` rows, `state='fetched'`, via `upsert(..., on_conflict="source,source_id", ignore_duplicates=True)` | Never advances the watermark. Pages written and dropped one page at a time (`Adapter.iter_items` is a generator). |
| 2 | Structural dedupe | `painminer/pipeline/dedupe.py :: dedupe()` (decision: `classify()`) | `items` where `state='fetched'` | `items.state` → `'ready'` or `'duplicate'` | Only filter left in v2: too-short (<40 chars), link-only, or `content_hash` already accepted (across sources/runs). |
| 3 | Judge | `painminer/pipeline/judge.py :: run_judge()` (per item: `judge_item()`, commit: `commit_judgement()`) | `items` where `state='ready'` (claimed via `claim_items` RPC → `'processing'`) | `findings` (0..n rows), `items.raw_text = null`, `items.state = 'done'` (or `'failed'`) — via the `record_judgement` SQL RPC, one transaction | Preflights Machine B first (`judge.preflight`). Retries a parse/validation failure once; two failures → `failed`. Per-thread cap of 2 findings/run (`MAX_FINDINGS_PER_THREAD`). Bounded by `config.max_run_minutes`. |
| 4 | Embed | `painminer/pipeline/embed.py :: embed_findings()` | `findings` where `embedding is null` | `findings.embedding` (halfvec 384) via `set_embedding` RPC | Loads `bge-small-en-v1.5` once (MPS→CPU fallback), batched, normalized vectors (cosine == dot). |
| 5 | Cluster | `painminer/pipeline/cluster.py :: cluster_findings()` (decision: `decide()`) | `findings` where embedded and `cluster_id is null`; `clusters` centroids of the same `kind` | new `clusters` rows (`create_cluster` RPC) or `findings.cluster_id` set; `mentions` row + cluster's `sources_seen`/`days_seen`/`mention_count`/`last_seen` (`record_mention` RPC) | sim ≥ `cluster_merge_threshold` (0.90) → auto-merge; `cluster_tiebreak_low` (0.75) ≤ sim < merge → LLM tiebreak (`llm.same_underlying_thing`); below → new cluster. |
| 6 | Rank | `painminer/pipeline/rank.py :: rank_clusters()` (formula: `recurrence_score()`) | `clusters` (all rows) | `clusters.score`, `clusters.score_version` | Idempotent full recompute every run. `research`/`read` kinds never rank well here — digest ranks those by recency instead. |
| 7 | Deliver | `painminer/delivery/telegram_bot.py`, `painminer/delivery/digest.py :: top_clusters()` / `format_card()` | `clusters` (top by score, and single-shot by recency), `findings` (representative quote), `items` (url), `runs` (footer) | Telegram messages; button presses write `feedback` rows and flip `clusters.muted`/`pinned` (`painminer/delivery/actions.py`) | Hard cap 5 recurring + 3 single-shot cards. |

Orchestrator: `painminer/pipeline/scan.py :: run_scan()` runs fetch (all
enabled sources) → dedupe → judge → embed → cluster → rank in one call,
writes a `runs` row, and — **only after judge succeeds** — advances each
fetched source's `last_successful_fetch_at` to the timestamp its fetch
*started*. A source whose fetch throws is recorded in `summary.source_errors`
and does not abort the run.

---

## 3. The `items` state machine

States (checked by a Postgres `check` constraint in `sql/schema.sql`):

```
fetched → ready ─────────────→ processing → done
              └─→ duplicate         │            (raw_text nulled here)
                                     └──→ failed  (3 strikes, or 2 judge failures)
```

- **`fetched → ready | duplicate`** — `painminer/pipeline/dedupe.py :: dedupe()` / `classify()`. Pure structural rule, no LLM involved.
- **`ready → processing`** — `painminer/pipeline/queue_ops.py :: claim()`, which calls the `claim_items` SQL RPC (`sql/queue.sql`). Atomic `FOR UPDATE SKIP LOCKED`; increments `attempts`, stamps `processing_at`. Also reclaims `processing` rows stuck longer than `stuck_seconds` (default 3600s = 1h), and first retires any stuck row already at `max_attempts` (default 3) straight to `failed`.
- **`processing → done`** — `painminer/pipeline/judge.py :: commit_judgement()` → `record_judgement` SQL RPC (`sql/judge.sql`): inserts the findings, sets `raw_text = null`, `state = 'done'`, all one transaction.
- **`processing → failed`** — `painminer/pipeline/queue_ops.py :: mark_failed()`, called from `run_judge()` when `judge_item()` fails twice (bad JSON / schema / empty statement, see `parse_and_validate()`).
- **`processing → ready`** — `painminer/pipeline/queue_ops.py :: release()`. Used when the run's time budget expires mid-batch, or when a genuine `openai.APIError` occurs mid-run (LLM/network trouble that is not the item's fault) — does **not** burn an attempt.

Every stage only touches rows in its own input state, so re-running any stage
after a crash is safe (idempotent) — see `docs/painminer-design-v2.md` §4.2.

---

## 4. Module reference

### `painminer/` (top level)

| Module | Purpose | Key functions/classes |
|---|---|---|
| `config.py` | Loads and validates `.env` into a frozen `Config` dataclass; fails loudly listing every missing required key. | `Config` (6 required fields + tuning defaults), `load_config()` |
| `db.py` | Thin typed wrapper over the Supabase client; hands back the raw PostgREST builder for anything beyond basic CRUD. Forces IPv4 on import. | `DB` (`.table()`, `.insert()`, `.upsert()`, `.get()`), `DB.from_config()`, `TABLES` |
| `net.py` | Monkeypatches `socket.getaddrinfo` so all outbound DNS resolves to IPv4 only (Machine A has no IPv6 egress and several HTTP clients hang on an IPv6-first DNS answer). | `force_ipv4()` (idempotent) |
| `llm.py` | LM Studio (Machine B) client: temperature 0, `reasoning_effort: "none"`, schema-constrained JSON decoding, 30s timeout, up to 3 retries on transient network errors. | `LLM` class — `.list_models()` (preflight probe), `.warmup()`, `.complete()` (judge call), `.same_underlying_thing()` (cluster tiebreak) |
| `notify.py` | Minimal one-shot Telegram push, used only for the preflight-failure alarm. Never raises. | `send_telegram(config, text)` |
| `prompt.py` | The verbatim judge system prompt, few-shot examples (negatives + one multi-finding positive), and the JSON schema that constrains decoding. | `SYSTEM_PROMPT`, `FINDINGS_SCHEMA`, `REQUIRED_KEYS`, `build_messages()` |

### `painminer/adapters/`

| Module | Purpose | Key functions/classes |
|---|---|---|
| `base.py` | Adapter ABC and the normalized item shape every adapter must yield. | `Adapter` (abstract `iter_items()`), `FetchedItem` dataclass, `clean_html()`, `content_hash()` |
| `__init__.py` | Dynamic registry: resolves a `sources` row's `config_json.adapter_impl` (or `adapter` column) to a Python module `painminer.adapters.<impl>` and instantiates its `Adapter` subclass. Adding a source is a config row, not new wiring here. | `get_adapter(source, client)` |
| `json_api.py` | Generic config-driven JSON-API engine: one HTTP GET helper + hit→`FetchedItem` mapping + a default offset-paginated `iter_items()`. Drives Lobsters and Stack Overflow config-only (no custom code). | `JsonApiAdapter` — `._get()`, `._make_item()`, `.iter_items()` |
| `hackernews.py` | HN via Algolia. Runs prioritized `searches` (Ask HN, Show HN, phrase-targeted pain/build queries) instead of the raw comment firehose, and windows past Algolia's ~1000-result-per-query cap by moving the upper time bound down and re-querying. | `HackerNewsAdapter` — `.iter_items()`, `._fetch_search()`, `._page_window()` |
| `github.py` | GitHub Issues Search API. Maps title+body to text, 👍 reactions + comment count to `metadata` (engagement for the `build` rule), threads = the issue itself, paginates with a `throttle_seconds` delay for the ~10/min unauth limit. | `GithubAdapter` — `._make_item()`, `.iter_items()`, `._search()` |

### `painminer/pipeline/`

| Module | Purpose | Key functions/classes |
|---|---|---|
| `fetch.py` | Runs one source's adapter, writes pages to `items(state=fetched)`. Never advances the watermark. | `fetch_source()`, `FetchSummary`, `_watermark_epoch()` |
| `dedupe.py` | Structural dedupe only (too short / link-only / content-hash seen). Pure decision function is unit-tested without a DB. | `dedupe()`, `classify()` (pure), `DedupeSummary` |
| `queue_ops.py` | Claim/release/terminal-state wrappers around the `claim_items` SQL RPC. | `claim()`, `mark_done()`, `mark_failed()`, `release()` |
| `judge.py` | The LLM judge stage: preflight, warm-up, per-item judge+validate+retry, per-thread cap, transactional commit, wall-clock run budget. | `preflight()`, `parse_and_validate()`, `judge_item()`, `commit_judgement()`, `run_judge()`, `thread_key()`, `cap_for_thread()`, `PreflightError` |
| `embed.py` | Embeds finding statements with `bge-small-en-v1.5` for clustering only. | `Embedder` class, `embed_findings()`, `format_vector()`/`parse_vector()` |
| `cluster.py` | Cosine-search each new finding against same-`kind` cluster centroids; merge, LLM-tiebreak, or start a new cluster; record deduped mentions. | `cluster_findings()`, `decide()` (pure), `ClusterSummary` |
| `rank.py` | Recomputes every cluster's score from breadth/persistence/mentions/recency. | `recurrence_score()` (pure formula), `cluster_features()`, `rank_clusters()`, `SINGLE_SHOT_KINDS` |
| `scan.py` | Full orchestrator: fetch all enabled sources → dedupe → judge → embed → cluster → rank, with a `runs` row and post-judge watermark advance. Used by the headless CLI and the bot's `/scan`. | `run_scan()`, `ScanSummary` |

### `painminer/delivery/`

| Module | Purpose | Key functions/classes |
|---|---|---|
| `digest.py` | Builds the leaderboard (top 5 recurring + top 3 single-shot cards), `/status`, `/kinds`, `/why <id>` text — DB-only, no Telegram import, so it's reusable and unit-testable. | `top_clusters()`, `format_card()`, `status_text()`, `kinds_text()`, `why_text()`, `run_footer()`, `feedback_features()`, `Card` dataclass |
| `actions.py` | 🔥/🗑/👀 button dispatch: writes `feedback`, and for mute/pin also flips cluster state. | `fire()`, `mute()`, `pin()`, `record_feedback()`, `DISPATCH` |
| `telegram_bot.py` | The operator interface: `/scan /top /why /status /kinds` commands, inline buttons, streams `/scan` progress by editing one message. Only the configured chat is served. | `build_app()`, `scan_cmd()`, `_send_digest()`, `on_button()` |
| `findings_report.py` | Dumps all findings (grouped by kind, sorted by confidence) to `findings.md` for manual reading/QA. | `build_report()` |

### `painminer/tools/`

| Module | Purpose |
|---|---|
| `migrate.py` | Applies a `.sql` file to Postgres directly via `psycopg`, falling back from the (IPv6-only) direct connection to the Supavisor pooler by probing regions. `parse_db_url()` hand-splits the URL since the password may contain raw `/`/`%`. |
| `seed_sources.py` | Idempotent upsert of the 4 registry rows (`hackernews`, `github`, `lobsters`, `stackoverflow`) with their full `config_json`. |
| `run_pipeline.py` | Headless full pipeline run (`scan.run_scan`) + Telegram digest delivery + a printed operator report (per-source findings, empty-array rate, s/item). |
| `smoke.py` | Phase 1: insert a disabled `smoke_test` source, read it back, delete it — proves `.env`/client/schema work. |
| `test_phase2.py` | Live check: fetch the same fixed window twice — 2nd run inserts 0 rows (unique constraint), watermark doesn't move. |
| `test_phase3.py` | Live check: structural dedupe rules + full queue lifecycle (claim → resume-after-crash → stuck-row reclaim → three-strikes-to-failed). |
| `verify_phase4.py` | Live check against Machine B: judges 20 real recent HN items *without committing*, prints raw model output + quote-fidelity check, so you can eyeball the empty-array rate before trusting the prompt. Ends with one real transactional commit to prove that path. |
| `verify_phase5.py` | Live check: two paraphrases of the same pain must land in the same cluster; an unrelated finding must not — inserts synthetic findings, embeds, clusters, asserts, cleans up. |

### `tests/` (offline unit suite, run via `run_tests.py`)

Pure-logic tests, no network/DB: `test_clean_html.py`, `test_cluster.py`
(`decide()`), `test_config.py`, `test_dedupe.py` (`classify()`),
`test_digest.py`, `test_embed.py`, `test_hn_adapter.py` (windowing against a
fake Algolia client), `test_judge_cap.py` (`cap_for_thread()`),
`test_judge_parse.py` (`parse_and_validate()`), `test_migrate.py`
(`parse_db_url()`), `test_prompt.py`, `test_rank.py` (`recurrence_score()`),
`test_registry.py` (`get_adapter()`).

---

## 5. Database

All tables defined in `sql/schema.sql`, extended by the additive migrations in
`sql/`. Vector columns (`clusters.centroid`, `findings.embedding`) are
**`halfvec(384)`** (half-precision, matching `bge-small-en-v1.5`'s 384 dims),
each with an HNSW cosine index.

| Table | Role | Key columns |
|---|---|---|
| `sources` | The source registry (§3.1). `name` is the primary key; `config_json` drives the adapter entirely. | `name` (PK), `adapter` (`json_api`\|`rss`\|`crawl`\|`bulk`), `config_json`, `cursor_field`, `last_successful_fetch_at` (the watermark — advanced only after judge succeeds), `enabled` |
| `items` | One row per fetched piece of text; the queue. `raw_text` is nulled the moment judging succeeds. | `id`, `source`, `source_id` (unique together), `url`, `raw_text`, `content_hash`, `state` (checked enum, §3 above), `attempts`, `processing_at`, `thread_id` (added by `add_thread_id.sql`), `metadata` jsonb (added by `add_metadata.sql`, e.g. HN points/comments) |
| `findings` | One row per thing the model surfaced; 0..n per item. This is what gets embedded/clustered. | `id`, `item_id` (FK, cascade delete), `kind` (free text, open taxonomy), `statement`, `why_it_matters`, `domain`, `evidence_quote`, `confidence` (0-1), `embedding` (halfvec 384), `cluster_id` (FK, set null on cluster delete) |
| `clusters` | Recurring findings grouped by centroid similarity. `sources_seen`/`days_seen` are **sets**, not counters. | `id`, `kind`, `canonical_statement`, `centroid` (halfvec 384, anchored on first member — never a running mean), `sources_seen` text[], `days_seen` date[], `mention_count`, `first_seen`, `last_seen`, `score`, `score_version`, `muted`, `pinned`/`pinned_mention_count` (added by `add_pin.sql`) |
| `mentions` | Dedup ledger for cluster mentions (added by `cluster.sql`), enforcing "one mention per cluster per source per day". | `id`, `cluster_id` (FK cascade), `source`, `seen_on`, unique `(cluster_id, source, seen_on)` |
| `feedback` | Append-only training-label log written by button presses. | `id`, `cluster_id` (FK cascade), `verdict` (`interesting`\|`kill`\|`watch`), `features_json` (stable feature snapshot for future model fitting), `score_version`, `created_at` |
| `runs` | One row per `/scan` / pipeline run — the observability backbone. | `id`, `started_at`, `finished_at`, `items_fetched`, `findings_created`, `errors` jsonb (per-source fetch error strings) |

### RPC functions (defined via `painminer/tools/migrate.py`, in `sql/`)

| RPC | File | Called from | Does |
|---|---|---|---|
| `claim_items(batch_size, stuck_seconds, max_attempts)` | `sql/queue.sql` | `pipeline/queue_ops.py :: claim()` | Atomically (`FOR UPDATE SKIP LOCKED`) retires stuck+exhausted rows to `failed`, then claims `ready` rows plus stuck `processing` rows into `processing`, incrementing `attempts` and stamping `processing_at`. |
| `record_judgement(p_item_id, p_findings)` | `sql/judge.sql` | `pipeline/judge.py :: commit_judgement()` | One transaction: inserts the findings array, nulls `items.raw_text`, sets `items.state = 'done'`. Returns the number of findings written (0 is valid). |
| `create_cluster(p_kind, p_statement, p_centroid)` | `sql/cluster.sql` | `pipeline/cluster.py :: cluster_findings()` | Inserts a new cluster row anchored on the given member's embedding (text→halfvec cast happens here since PostgREST can't send a halfvec param directly). |
| `set_embedding(p_id, p_vec)` | `sql/cluster.sql` | `pipeline/embed.py :: embed_findings()` | Casts the text-form vector to `halfvec` and writes `findings.embedding`. |
| `record_mention(p_cluster_id, p_source, p_seen_on)` | `sql/cluster.sql` | `pipeline/cluster.py :: cluster_findings()` | Inserts into `mentions` with `on conflict do nothing` (the per-day-per-source dedup); only if genuinely new does it bump `mention_count`, append to `sources_seen`/`days_seen`, and update `last_seen`. Returns whether it was new. |

---

## 6. Config & environment

`painminer/config.py :: load_config()` reads `.env` (via `python-dotenv`) and
raises `RuntimeError` listing every missing key if any required key is absent.

**Required (no defaults — `.env` must set all six):**

| Env var | Config field |
|---|---|
| `SUPABASE_URL` | `supabase_url` |
| `SUPABASE_SERVICE_KEY` | `supabase_service_key` (bypasses RLS — Machine A only) |
| `TELEGRAM_TOKEN` | `telegram_token` |
| `TELEGRAM_CHAT_ID` | `telegram_chat_id` |
| `LLM_BASE_URL` | `llm_base_url` (the one coupling point to Machine B, e.g. `http://192.168.1.104:1234/v1`) |
| `LLM_MODEL` | `llm_model` |

`SUPABASE_DB_URL` is also needed in `.env` for migrations (`tools/migrate.py`
reads it directly via `os.getenv`, not through `Config`).

**Optional tuning knobs** (env var → default):

| Env var | Field | Default |
|---|---|---|
| `MAX_RUN_MINUTES` | `max_run_minutes` | 60 — wall-clock judge budget |
| `LLM_TIMEOUT_SECONDS` | `llm_timeout_seconds` | 30.0 |
| `LLM_API_KEY` | `llm_api_key` | `"lm-studio"` (ignored by LM Studio; the OpenAI SDK requires *something*) |
| `BUILD_MIN_POINTS` | `build_min_points` | 50 — HN points threshold for the `build` engagement rule |
| `BUILD_MIN_COMMENTS` | `build_min_comments` | 30 |
| `CLUSTER_MERGE_THRESHOLD` | `cluster_merge_threshold` | 0.90 |
| `CLUSTER_TIEBREAK_LOW` | `cluster_tiebreak_low` | 0.75 |

`embed_model` (`BAAI/bge-small-en-v1.5`) and `embed_batch_size` (64) are
Config fields but have no env override currently — change the dataclass
default to change them.

**Networking notes:**

- Supabase's *direct* DB connection (`db.<ref>.supabase.co`) is **IPv6-only**.
  Machine A has no IPv6 egress, so `painminer/tools/migrate.py :: connect()`
  tries the direct connection first, and on `psycopg.OperationalError` falls
  back to discovering a working **Supavisor pooler** host by probing known
  region/prefix combinations (`_discover_pooler()`).
- Every other code path (the Supabase SDK, httpx-based adapters, Telegram)
  goes through `painminer/net.py :: force_ipv4()`, called once at import time
  in `db.py`, which monkeypatches `socket.getaddrinfo` to prefer IPv4 A
  records — this avoids IPv6-first DNS answers stalling connections on a host
  with no IPv6 route.
- `fetch.py` additionally binds its own `httpx.Client` transport to
  `local_address="0.0.0.0"` for the same reason, with `retries=2`.
- A VPN on Machine A blocks LAN access to Machine B; this is caught by the
  judge preflight (see §8 below), not by `force_ipv4()`.

---

## 7. How to run

All commands use the module form (`-m`) from the repo root, with the project
venv.

**Migrations (order matters — run once, or after a schema change):**
```
.venv/bin/python -m painminer.tools.migrate sql/schema.sql
.venv/bin/python -m painminer.tools.migrate sql/queue.sql
.venv/bin/python -m painminer.tools.migrate sql/judge.sql
.venv/bin/python -m painminer.tools.migrate sql/add_thread_id.sql
.venv/bin/python -m painminer.tools.migrate sql/add_metadata.sql
.venv/bin/python -m painminer.tools.migrate sql/cluster.sql
.venv/bin/python -m painminer.tools.migrate sql/add_pin.sql
```
`sql/cleanup.sql` is a manual reset (truncates `mentions, feedback, findings,
clusters, items, runs`, drops non-production test sources, rewinds enabled
sources' watermark to 6h ago) — run it deliberately, not as part of setup.

**Seed the source registry:**
```
.venv/bin/python -m painminer.tools.seed_sources
```

**Run individual stages (useful for debugging one link in the chain):**
```
.venv/bin/python -m painminer.pipeline.fetch hackernews   # one source
.venv/bin/python -m painminer.pipeline.dedupe
.venv/bin/python -m painminer.pipeline.judge
.venv/bin/python -m painminer.pipeline.embed
.venv/bin/python -m painminer.pipeline.cluster
.venv/bin/python -m painminer.pipeline.rank
```

**Full pipeline (headless, plus Telegram digest):**
```
.venv/bin/python -m painminer.tools.run_pipeline
```

**The Telegram bot (interactive, `/scan /top /why /status /kinds` + buttons):**
```
.venv/bin/python -m painminer.delivery.telegram_bot
```

**Dump findings to a readable file:**
```
.venv/bin/python -m painminer.delivery.findings_report        # -> findings.md
```

**Tests:**
```
python run_tests.py                                  # offline unit suite, no net/DB
.venv/bin/python -m painminer.tools.smoke             # Phase 1: insert/read-back
.venv/bin/python -m painminer.tools.test_phase2       # Phase 2: rerun writes zero, watermark held
.venv/bin/python -m painminer.tools.test_phase3       # Phase 3: dedupe + queue lifecycle
.venv/bin/python -m painminer.tools.verify_phase4     # Phase 4: judge 20 real HN items (live, dry-run + one commit)
.venv/bin/python -m painminer.tools.verify_phase5     # Phase 5: embed + cluster co-clustering check
```

---

## 8. Debugging guide

| Symptom | Look at |
|---|---|
| Run aborts immediately with a Telegram message about Machine B / VPN | `painminer/pipeline/judge.py :: preflight()` — calls `LLM.list_models()` (`GET /v1/models`). On any exception, or if `config.llm_model` isn't in the returned list, it sends via `painminer/notify.py :: send_telegram()` and raises `PreflightError`, aborting *before* any fetching (`scan.run_scan()` calls preflight first thing). Check: is LM Studio running on Machine B and is the model loaded? Is a VPN active on Machine A (VPN blocks LAN access to Machine B — see `docs/painminer-design-v2.md` §12)? Confirm `LLM_BASE_URL` in `.env` matches Machine B's current LAN IP (DHCP reservation recommended). |
| Intermittent `ConnectTimeout` / hangs on fetch or DB calls | `painminer/net.py :: force_ipv4()` — Machine A has no IPv6 egress; if a client bypasses this patch (e.g. a new dependency with its own resolver) you'll see stalls to IPv6-first DNS answers. Also check `painminer/pipeline/fetch.py :: _make_client()` (binds `local_address="0.0.0.0"`, `retries=2`). Because every stage is idempotent (unique `(source, source_id)` on `items`, `record_judgement`/`claim_items` are transactional), the fix for a one-off network blip is usually just: rerun the stage or `run_pipeline` again — nothing is lost. |
| A run stops with `[stopped on time budget]` and items are stuck | `painminer/pipeline/judge.py :: run_judge()` — the `deadline = time.time() + config.max_run_minutes * 60` check. On expiry, in-flight items are `release()`d back to `ready` (not burned as an attempt) and the loop breaks; leftover claimed-but-unprocessed rows stay `ready`/get released to `ready`. This is expected — the next run (`/scan` or another `judge` invocation) picks them up from `ready` via `claim_items`. Raise `MAX_RUN_MINUTES` in `.env` if runs are consistently truncated. |
| A source returns zero items / looks broken | `painminer/delivery/digest.py :: status_text()` (`/status` in the bot) prints per-source item counts and `last_successful_fetch_at`. A source stuck at "never" or a stale timestamp for days means its adapter is broken — check the adapter module (`painminer/adapters/hackernews.py`, `github.py`, or the source's `config_json` for the generic `json_api.py` engine) against the live API (endpoint changed, auth now required, response shape changed). `scan.run_scan()` catches a fetch exception per-source into `summary.source_errors` / `runs.errors` rather than aborting the whole run — check the latest `runs` row. |
| Findings are empty for one whole source even though it fetched fine | The **per-thread cap**: `MAX_FINDINGS_PER_THREAD = 2` in `painminer/pipeline/judge.py`, enforced via `thread_key()`/`cap_for_thread()`. Combined with the **id-ordered claim** (`claim_items` orders by `id`), a source whose items happen to sort late within the run can be starved entirely if earlier sources/threads consume the whole `max_run_minutes` budget before its `ready` rows are ever claimed. Check `/status` queue depth (`ready` count) and whether the run hit `[stopped on time budget]`. |
| GitHub adapter throttling / 403s | `painminer/adapters/github.py` — unauthenticated Search API is rate-limited to ~10/min; `throttle_seconds` (default 2, from `seed_sources.py`'s `GITHUB["config_json"]`) sits between paged requests. If you see 403/422, the query likely exceeded GitHub's undocumented complexity limits or the unauth rate window was hit — increase `throttle_seconds` or reduce `queries`. |
| Stack Overflow adapter stops early / quota errors | `painminer/tools/seed_sources.py :: STACKOVERFLOW["config_json"]["max_pages"] = 3` — unauthenticated Stack Exchange API quota is ~300 requests/day; `max_pages` caps how many pages `json_api.py :: JsonApiAdapter.iter_items()` will walk per run to stay well under quota. A `quota_remaining` near 0 in the raw API response (not currently surfaced in code) is the underlying signal — reduce `max_pages` or the `hits_per_page` if you hit it. |
| Dedupe throws a PostgREST 400 / URL too long | `painminer/pipeline/dedupe.py` — `content_hash`es are 64 hex chars each; a naive `in.(...)` filter over a large batch overflows PostgREST's request URL. `_chunks()` / `IN_CHUNK = 100` splits both the accepted-hash lookup (`_existing_accepted_hashes`) and the state-update (`_apply`) into chunks of 100 ids/hashes. If you see this error, something bypassed the chunking (e.g. a hand-rolled query) — chunk it the same way. |
| Clusters look wrong — everything merging into one, or near-duplicates never merging | `painminer/pipeline/cluster.py :: decide()` and the two config knobs `cluster_merge_threshold` (0.90) / `cluster_tiebreak_low` (0.75) in `painminer/config.py`. Below `tiebreak_low` → always a new cluster; at/above `merge_threshold` → always auto-merged; in between → `llm.same_underlying_thing()` decides. These thresholds are explicitly called out in the design doc as unvalidated starting points — tune via `CLUSTER_MERGE_THRESHOLD`/`CLUSTER_TIEBREAK_LOW` env overrides, then re-run `painminer/tools/verify_phase5.py` to sanity-check co-clustering behavior. Remember the centroid is anchored on a cluster's *first* member and never recomputed — a cluster can "drift" from later members without its centroid ever reflecting them. |
| Judge keeps returning findings for near-everything (empty-array rate too low) | `painminer/prompt.py :: SYSTEM_PROMPT` and few-shot `_NEGATIVES`/`_POSITIVE_*`. This is the single highest-leverage failure mode per the design doc (§15) — there is no lexical/embedding filter left, so prompt drift directly becomes feed noise. Diagnose with `painminer/tools/verify_phase4.py` (dry-run judges 20 real items, prints raw model output next to source text, reports the empty-array rate and evidence-quote verbatim-fidelity) before trusting a live run. |
| A judged item's `evidence_quote` doesn't match the source | `painminer/tools/verify_phase4.py :: _print_case()` explicitly checks this (`verbatim` vs `NOT FOUND IN SOURCE`) — a model hallucinating quotes despite the schema/prompt instruction. Check `painminer/adapters/base.py :: clean_html()` too — if HTML wasn't cleaned before judging (`config_json.clean_html` not set for a source), the model may see raw markup that doesn't match a "clean" evidence quote, or vice versa. |
| A digest card is missing, or a previously-seen finding vanished | `painminer/delivery/digest.py :: _visible()` — excluded if `clusters.muted` (permanent, via the 🗑 button, `painminer/delivery/actions.py :: mute()`) or if `pinned` and `mention_count` hasn't moved past `pinned_mention_count` since the 👀 button was pressed (`actions.py :: pin()`). Check the cluster row directly (`/why <id>` or a DB query) for `muted`/`pinned`/`pinned_mention_count`. |
| Bot doesn't respond / responds to the wrong chat | `painminer/delivery/telegram_bot.py :: _authorized()` — every handler checks `update.effective_chat.id == CONFIG.telegram_chat_id` and silently no-ops otherwise. Confirm `TELEGRAM_CHAT_ID` in `.env` matches the chat you're messaging from. |
| `/scan` from the bot seems to hang or double-run | `telegram_bot.py :: scan_cmd()` uses a module-level `_SCANNING` flag to reject a second concurrent `/scan`; the actual pipeline runs in a worker thread (`asyncio.to_thread(scan.run_scan, ...)`) while the handler polls a shared `holder["text"]` every 2s to edit the status message. If the bot process restarts mid-scan, `_SCANNING` resets to `False` in memory but the DB-side run is still whatever state it left `items`/`runs` in — check the latest `runs` row's `finished_at`. |
| Migration fails to connect | `painminer/tools/migrate.py :: connect()` / `_discover_pooler()` — confirms `SUPABASE_DB_URL` is set, tries the direct (IPv6-only) host first, then probes pooler regions/prefixes. If it exhausts all regions, `RuntimeError("no reachable Supabase pooler found...")` — verify the Supabase project is not paused (free tier pauses after ~7 days idle) and that `SUPABASE_DB_URL`'s host/ref is correct. |

---

*Generated by reading every `.py` file under `painminer/` and every `.sql`
file under `sql/` against `docs/painminer-design-v2.md` and
`docs/ARCHITECTURE.md`. If code and this map disagree, trust the code and
update this file.*
