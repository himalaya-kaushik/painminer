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
| Fetch  | registry sources | `items(state=fetched)`     | `painminer/pipeline/fetch.py`, `painminer/adapters/` |
| Dedupe | `fetched`        | `ready` / `duplicate`      | `painminer/pipeline/dedupe.py` |
| Judge  | `ready`          | `findings` (0..n), `done`  | `painminer/pipeline/judge.py`, `painminer/llm.py`, `painminer/prompt.py` |
| Embed  | `findings`       | `embedding`                | `painminer/pipeline/embed.py` |
| Cluster| `findings`       | `clusters`                 | `painminer/pipeline/cluster.py` |
| Rank   | `clusters`       | `score`                    | `painminer/pipeline/rank.py` |
| Deliver| leaderboard      | Telegram                   | `painminer/delivery/telegram_bot.py`, `painminer/delivery/digest.py` |

## Two machines, one coupling point

- **Machine A** (8GB Air): fetch, dedupe, queue, embeddings, clustering,
  ranking, Telegram, all DB access. Pages and writes — never holds a full fetch
  in memory.
- **Machine B** (LM Studio): serves the judge model, stateless. The only
  coupling is `LLM_BASE_URL`. If B is unreachable, A still fetches and dedupes;
  rows accumulate in `ready`. The pipeline degrades, it does not fail.

## Module map

```
painminer/
  __init__.py
  config.py          .env loader + validation (6 required keys, optional overrides)
  db.py              thin typed Supabase wrapper (forces IPv4 on import)
  net.py             force_ipv4(): no IPv6 egress on Machine A -> resolve A records
  llm.py             LM Studio client: temp 0, reasoning_effort none, 30s, retries
  notify.py          minimal Telegram push (preflight-failure alarm)
  prompt.py          the verbatim judge system prompt + few-shot + output schema

  adapters/
    __init__.py      registry: adapter type / config.adapter_impl -> class
    base.py          Adapter ABC, FetchedItem, content_hash, clean_html
    json_api.py      generic config-driven JSON-API engine
    hackernews.py    HN over Algolia; windows past the ~1000-result cap
    github.py        GitHub Issues search adapter

  pipeline/
    __init__.py
    fetch.py         fetch one source -> items(fetched); never advances watermark
    dedupe.py        structural dedupe; classify() is the pure decision
    queue_ops.py     claim / mark_done / mark_failed / release
    judge.py         preflight, warm-up, judge+validate+retry, commit, run budget
    embed.py         embedding stage (§8)
    cluster.py       clustering stage (§8)
    rank.py          ranking stage (§9)
    scan.py          full pipeline orchestrator: fetch -> dedupe -> judge -> embed -> cluster -> rank

  delivery/
    __init__.py
    digest.py        leaderboard rendering, feedback features
    actions.py       fire/mute/pin dispatch table
    telegram_bot.py  Telegram bot (§10) — the operator interface
    findings_report.py  dump findings to findings.md

  tools/
    __init__.py
    migrate.py         applies *.sql over the Supavisor pooler (direct DB is IPv6-only)
    seed_sources.py    registry seed (Hacker News, GitHub, Lobsters, Stack Overflow)
    run_pipeline.py    headless full-pipeline run + Telegram digest
    smoke.py           Phase 1 smoke test
    verify_phase4.py   Phase 4 live acceptance check
    verify_phase5.py   Phase 5 live acceptance check

sql/
  schema.sql       tables (§6): sources, items, findings, clusters, feedback, runs
  queue.sql        claim_items(): atomic claim, reclaim stuck, three strikes
  judge.sql        record_judgement(): findings + null raw_text + done, one txn
  add_thread_id.sql, add_metadata.sql, cluster.sql, add_pin.sql, cleanup.sql

docs/
  ARCHITECTURE.md
  painminer-design-v2.md
```

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# .env holds SUPABASE_URL, SUPABASE_SERVICE_KEY, SUPABASE_DB_URL,
# TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, LLM_BASE_URL, LLM_MODEL (never committed)

.venv/bin/python -m painminer.tools.migrate sql/schema.sql
.venv/bin/python -m painminer.tools.migrate sql/queue.sql
.venv/bin/python -m painminer.tools.migrate sql/judge.sql
.venv/bin/python -m painminer.tools.seed_sources
```

## Running & verifying

```bash
.venv/bin/python run_tests.py                      # fast, offline unit suite (no net/db)

.venv/bin/python -m painminer.tools.smoke          # Phase 1: insert/read-back
.venv/bin/python -m painminer.tools.test_phase2    # Phase 2: rerun writes zero, watermark held
.venv/bin/python -m painminer.tools.test_phase3    # Phase 3: dedupe + queue (claim/resume/reclaim/strikes)
.venv/bin/python -m painminer.tools.verify_phase4  # Phase 4: judge 20 real HN items, empty-array rate
.venv/bin/python -m painminer.tools.verify_phase5  # Phase 5: embed + cluster

.venv/bin/python -m painminer.pipeline.fetch hackernews  # fetch one source
.venv/bin/python -m painminer.pipeline.dedupe             # promote fetched -> ready/duplicate
.venv/bin/python -m painminer.pipeline.judge               # drain the ready queue against Machine B

.venv/bin/python -m painminer.tools.run_pipeline           # headless full pipeline + Telegram digest
.venv/bin/python -m painminer.delivery.telegram_bot         # run the Telegram bot
```

## Testing strategy

- **`run_tests.py`** — hermetic unit tests (`tests/`): pure logic only, no
  network, no database, sub-second. Covers `clean_html`, dedupe `classify`,
  judge parse/validate, prompt assembly, `parse_db_url`, config validation, and
  the HN cap-busting windowing against a fake Algolia client.
- **`painminer/tools/test_phase*.py` / `verify_phase4.py` / `verify_phase5.py`**
  — live acceptance checks against Supabase, Hacker News, and Machine B.

## Design decisions worth knowing

- **IPv6.** Supabase's direct DB is IPv6-only and Machine A has no IPv6 egress;
  `painminer/tools/migrate.py` falls back to the Supavisor pooler and
  `painminer.net.force_ipv4()` pins every client to IPv4.
- **Watermark advances only after judge success**, never after fetch — a
  mid-run crash must not mark unprocessed data as seen. Fetch overlaps its
  window by 6h; the unique `(source, source_id)` makes re-fetches free.
- **Atomicity.** The claim (`FOR UPDATE SKIP LOCKED`) and the judge commit
  (findings + `raw_text` null + `done`) are each a single SQL transaction.
- **The prompt is the system.** `prompt.SYSTEM_PROMPT` began verbatim from the
  brief, since tightened by the operator (confidence anchors; geographic-only
  `arbitrage`; `read`/`research` limited to ML / systems / startups). Empty
  array is the correct default; output is schema-constrained at decode time.
- **Targeted fetch over firehose.** The HN adapter runs configured `searches`
  (Ask HN, Show HN, and phrase-targeted pain/build queries) rather than
  draining general comment volume.
- **Per-thread cap.** At most 2 findings per parent thread per run (grouped by
  `items.thread_id`), so one busy discussion can't dominate a digest.
- **Chunked IN-filters.** dedupe splits `content_hash` / id lookups into
  chunks so a large batch never overflows the PostgREST request URL.
- **Open taxonomy.** `kind` is free text end to end; new kinds are accepted at
  write time, reviewed later — not rejected by an enum.

## Status

Phases 1–4 built and tested. 5 (embed/cluster), 6 (rank), 7 (Telegram bot),
8 (GitHub adapter — the registry-abstraction test) are next.
