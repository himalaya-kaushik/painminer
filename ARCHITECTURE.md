# painminer — architecture & operations

An aggregator where **the LLM is the judge** (design v2). Machine A fetches
public developer/startup text, dedupes it structurally, and hands raw text to a
local model that decides what is worth surfacing. Everything downstream is
bookkeeping. See `painminer-design-v2.md` for the authoritative spec.

## Pipeline

```
fetch → structural dedupe → LLM judge+extract → embed → cluster → rank → deliver
        (state machine over one Postgres-backed queue; every stage idempotent)
```

| Stage  | Reads            | Writes                     | Module            |
|--------|------------------|----------------------------|-------------------|
| Fetch  | registry sources | `items(state=fetched)`     | `fetch.py`, `adapters/` |
| Dedupe | `fetched`        | `ready` / `duplicate`      | `dedupe.py`       |
| Judge  | `ready`          | `findings` (0..n), `done`  | `judge.py`, `llm.py`, `prompt.py` |
| Embed  | `findings`       | `embedding`                | *(Phase 5)*       |
| Cluster| `findings`       | `clusters`                 | *(Phase 5)*       |
| Rank   | `clusters`       | `score`                    | *(Phase 6)*       |
| Deliver| leaderboard      | Telegram                   | *(Phase 7)*       |

## Two machines, one coupling point

- **Machine A** (8GB Air): fetch, dedupe, queue, embeddings, clustering,
  ranking, Telegram, all DB access. Pages and writes — never holds a full fetch
  in memory.
- **Machine B** (LM Studio): serves the judge model, stateless. The only
  coupling is `LLM_BASE_URL`. If B is unreachable, A still fetches and dedupes;
  rows accumulate in `ready`. The pipeline degrades, it does not fail.

## Module map

```
config.py        .env loader + validation (6 required keys, optional overrides)
db.py            thin typed Supabase wrapper (forces IPv4 on import)
net.py           force_ipv4(): no IPv6 egress on Machine A -> resolve A records
migrate.py       applies *.sql over the Supavisor pooler (direct DB is IPv6-only)
schema.sql       tables (§6): sources, items, findings, clusters, feedback, runs
queue.sql        claim_items(): atomic claim, reclaim stuck, three strikes
judge.sql        record_judgement(): findings + null raw_text + done, one txn

adapters/
  base.py        Adapter ABC, FetchedItem, content_hash, clean_html
  json_api.py    generic config-driven JSON-API engine
  hackernews.py  HN over Algolia; windows past the ~1000-result cap
  __init__.py    registry: adapter type / config.adapter_impl -> class

fetch.py         fetch one source -> items(fetched); never advances watermark
dedupe.py        structural dedupe; classify() is the pure decision
queue_ops.py     claim / mark_done / mark_failed / release
llm.py           LM Studio client: temp 0, reasoning_effort none, 30s, retries
prompt.py        the verbatim judge system prompt + few-shot + output schema
judge.py         preflight, warm-up, judge+validate+retry, commit, run budget
notify.py        minimal Telegram push (preflight-failure alarm)
seed_sources.py  registry seed (Hacker News)
```

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# .env holds SUPABASE_URL, SUPABASE_SERVICE_KEY, SUPABASE_DB_URL,
# TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, LLM_BASE_URL, LLM_MODEL (never committed)

.venv/bin/python migrate.py schema.sql
.venv/bin/python migrate.py queue.sql
.venv/bin/python migrate.py judge.sql
.venv/bin/python seed_sources.py
```

## Running & verifying

```bash
.venv/bin/python run_tests.py        # fast, offline unit suite (no net/db)

.venv/bin/python smoke.py            # Phase 1: insert/read-back
.venv/bin/python test_phase2.py      # Phase 2: rerun writes zero, watermark held
.venv/bin/python test_phase3.py      # Phase 3: dedupe + queue (claim/resume/reclaim/strikes)
.venv/bin/python verify_phase4.py    # Phase 4: judge 20 real HN items, empty-array rate

.venv/bin/python fetch.py hackernews # fetch one source
.venv/bin/python dedupe.py           # promote fetched -> ready/duplicate
.venv/bin/python judge.py            # drain the ready queue against Machine B
```

## Testing strategy

- **`run_tests.py`** — hermetic unit tests (`tests/`): pure logic only, no
  network, no database, sub-second. Covers `clean_html`, dedupe `classify`,
  judge parse/validate, prompt assembly, `parse_db_url`, config validation, and
  the HN cap-busting windowing against a fake Algolia client.
- **`test_phase*.py` / `verify_phase4.py`** — live acceptance checks against
  Supabase, Hacker News, and Machine B.

## Design decisions worth knowing

- **IPv6.** Supabase's direct DB is IPv6-only and Machine A has no IPv6 egress;
  `migrate.py` falls back to the Supavisor pooler and `net.force_ipv4()` pins
  every client to IPv4.
- **Watermark advances only after judge success**, never after fetch — a
  mid-run crash must not mark unprocessed data as seen. Fetch overlaps its
  window by 6h; the unique `(source, source_id)` makes re-fetches free.
- **Atomicity.** The claim (`FOR UPDATE SKIP LOCKED`) and the judge commit
  (findings + `raw_text` null + `done`) are each a single SQL transaction.
- **The prompt is the system.** `prompt.SYSTEM_PROMPT` is verbatim from the
  brief; empty-array is the correct default answer for most items. Output is
  schema-constrained at decode time.
- **Open taxonomy.** `kind` is free text end to end; new kinds are accepted at
  write time, reviewed later — not rejected by an enum.

## Status

Phases 1–4 built and tested. 5 (embed/cluster), 6 (rank), 7 (Telegram bot),
8 (GitHub adapter — the registry-abstraction test) are next.
