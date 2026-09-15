# painminer — status (v3)

_TLDR of where the system stands after the v3 three-pass rebuild._
_Last updated: 2026-09-15._

---

## What painminer is now

A nightly analyst, not a per-post classifier. It fetches public dev/startup
text, triages the haystack, deep-reads the survivors in thread context, then
writes **one briefing about the night** and sends it to Telegram as a
Markdown document. Everything runs against a local LLM (Machine B / LM Studio).

Pipeline:

```
fetch → dedupe → PASS 1 triage → PASS 2 deep read → embed → cluster → rank → PASS 3 synthesis → digest.md → Telegram
```

---

## Extraction sources (current)

| Source | Adapter | Status | Notes |
|---|---|---|---|
| Hacker News | json_api (custom) | ✅ active | Algolia; the dominant source tonight (47 of 75 findings) |
| GitHub Issues | json_api (custom) | ✅ active | **unscoped** — still pulls non-ML repos (scoping = step 6) |
| Lobsters | json_api (config only) | ✅ active | low volume |
| Stack Overflow | json_api (config only) | ⚠️ active but **to be dropped** | effectively dead; removal = step 5 |

Planned (not built yet): Product Hunt, arXiv, Hugging Face, YC — via a new
Atom/RSS adapter (steps 5 & 7).

---

## The three passes

1. **Triage** (`qwen-extract`, reasoning off) — per item, `{worth_reading, one_line}`.
   Kills the haystack. Live rate: **97%** killed, deep-read empty rate **2%**.
2. **Deep read** (`qwen-extract`, reasoning off) — survivors only, with **thread
   context** fetched first (HN via Algolia item endpoint). Emits the v2 findings
   schema. Producing genuinely on-profile findings.
3. **Synthesis** (`qwen3.6-35b`, reasoning off) — one call over all of tonight's
   findings + recurring-cluster shortlist. Writes the briefing
   (patterns / worth reading / someone built) + a plain night summary. **This is
   the product.**

Output: `digests/YYYY-MM-DD.md` (committed to the repo as a greppable archive)
delivered via Telegram `sendDocument`, plus a companion message listing the top
headlines with 🔥 / 🗑 / 👀 feedback buttons.

---

## Cleared ✅ (build order steps 1–3, + the budget fix)

- **Step 1 — three-pass restructure.** Triage / deep read / synthesis replace the
  single v2 judge. Reader profile (the brief's persona) in passes 2 & 3.
- **Step 2 — thread-context fetching.** HN comments read inside their thread.
- **Step 3 — document output.** `.md` digest + `sendDocument` + companion buttons.
- **Run budget removed.** `max_run_minutes` now defaults to **0 = no cap**: the
  judge drains the whole ready queue (dedicated local box, nothing to budget).
- **Tests:** 149 offline unit tests, all green (105 baseline + 44 new).
- **End to end: run + digest delivered to Telegram.** First judgeable output shipped.

### Two bugs found & fixed during the first live run
- Synthesis returned **empty output** with reasoning on → `qwen-extract` spent the
  whole token budget thinking. Fix: reasoning **off** for synthesis.
- Synthesis **under-reported** (1 item from 75 findings) → it was copying the
  quiet-night *example sentence* in the prompt, and `qwen-extract` is too weak for
  cross-item synthesis. Fix: **de-parroted the prompt** + **switched pass 3 to
  `qwen3.6-35b`** + made "someone built" a shortlist. Now produces a balanced,
  real briefing.

---

## Not done yet ⏳ (steps 4–7)

- **Step 4 — queue fairness.** `claim()` is still id-ordered, so high-volume HN
  starves Lobsters/SO/GitHub-tail. Needs round-robin claim across sources.
  (Budget half of step 4 is already done.)
- **Step 5 — Atom/RSS adapter** → Product Hunt, arXiv. Drop Stack Overflow & dev.to.
- **Step 6 — scope GitHub** to ML/infra/dev-tools repos (config allowlist/topics).
- **Step 7 — Hugging Face + YC** sources.
- Ranking tweak (drop `confidence` from scoring; recurrence as pass-3 shortlist)
  — partially in place; recurrence shortlist wired, will discriminate once
  clusters accrue across nights.

---

## Current queue state (post first run)

- ~1600 HN + ~106 SO + a few Lobsters items left **`ready`** (the old 60-min cap
  stopped the run mid-queue). The next **uncapped** `run_pipeline` will drain them.
- 74 new clusters created; recurrence not yet discriminating (needs multiple nights).

## How to run

```
.venv/bin/python -m painminer.tools.run_pipeline     # full pipeline + Telegram digest
python run_tests.py                                   # 149 offline tests
```

Config knobs added in v3 (all env-overridable): `SYNTHESIS_MODEL`,
`SYNTHESIS_REASONING`, `DEEP_READ_REASONING`, `TRIAGE_MAX_CHARS`,
`THREAD_CONTEXT_CHARS`, `MAX_RUN_MINUTES` (0 = uncapped).

> Note: `docs/CODEMAP.md` still describes the v2 single-judge pipeline and is now
> partially stale — pending update.
