# painminer v3 — architecture change brief

**For a new Claude Code session in the existing repo.**

Read `docs/CODEMAP.md` and `painminer-design-v2.md` first. This brief
supersedes them where they conflict. Phases 1–8 are already built and
working; this is a redefinition of what the system does with what it fetches,
not a rewrite of the plumbing.

---

## 1. What's wrong with v2

The judge is a **classifier**. It sees one post at a time, in isolation, and
decides whether that post contains something. It has no idea what else was in
the thread, what arrived yesterday, or whether five other people said the
same thing this week.

The result is technically-correct, contextually-meaningless output. A real
example from last night's digest: a qBittorrent seeding configuration option.
Accurate. Useless.

**What it needs to be is an analyst** — something that reads the whole night,
notices what recurs and what's genuinely new, and reports on the *night*,
not on individual posts.

That requires seeing multiple items at once. No amount of per-item prompt
tuning gets there. It's a structural change.

---

## 2. The reader

This goes in the system prompt of passes 2 and 3, verbatim.

```
You are reporting to one person. Everything you surface is judged against
whether it is useful to him specifically.

He is a machine learning engineer in India, mid-twenties, with an M.Tech
from IIT Kanpur. He works on LLMs, agents, reinforcement learning, and
retrieval systems, and has shipped agentic RAG in production. He thinks
seriously about system architecture and distributed systems.

He intends to start his own company within two to three years and is
actively looking for what to build. He has no company, no funding, and no
particular domain allegiance — he is looking for where the gaps are.

What he wants to know about:
- Research results that change what is possible, especially in LLMs, RL,
  agents, and training efficiency
- Where the field is moving: what is getting funded, what domains are
  suddenly crowded, what techniques are spreading
- Genuinely new tools and open-source releases in ML and infrastructure
- Problems people hit repeatedly that nothing currently solves

What he does not want:
- Configuration options in consumer software
- Framework and language opinion threads
- Generic startup advice
- Anything he would have already known
```

---

## 3. Three passes

Replaces the single judge stage. Sources, fetch, dedupe and queue stay
as-is.

### Pass 1 — Triage

Per item. Cheap and fast. **Not** structured extraction.

Input: the item text, truncated.
Output: `{"worth_reading": true|false, "one_line": "..."}`

The only question is whether this deserves a closer look. Most items are
`false`. This is haystack reduction — it should kill 85–95% of input.

Keep `reasoning_effort: "none"` here. This pass runs on everything, so speed
matters more than depth.

### Pass 2 — Deep read

Survivors only. This is where quality comes from.

**Fetch thread context before calling.** For an HN comment, pull the parent
story and sibling comments via the Algolia item endpoint. For a GitHub issue,
pull the issue body plus comments. A comment read alone is why v2 produced
meaningless findings — the same comment read inside its thread is a different
object.

Output is the v2 `findings` schema (`kind`, `statement`, `why_it_matters`,
`evidence_quote`, `domain`, `confidence`), with the reader profile in the
system prompt.

**Consider enabling reasoning on this pass** — it runs on far fewer items and
it's the pass where judgment actually matters. A/B it once running.

No web search. Deliberately dropped: the reader follows up himself.

### Pass 3 — Synthesis

**This is the product. Everything before it is feedstock.**

One call. Input: all of tonight's findings together, plus cluster state from
previous nights (statement, mention count, sources, days seen).

The model is not summarizing posts. It is writing a briefing about the night.
It must be able to see across items — that is the entire point of giving it
everything at once.

Three things it should produce, mirroring what the reader asked for:

1. **Patterns.** Things visible only across multiple items. "Five YC
   companies launched in agriculture this batch" is one observation, not five
   findings. Recurrence across nights counts here too — "third time this week
   someone hit this."
2. **Worth reading.** Specific results that change something, with a line on
   why it's worth the time.
3. **Someone built / something shipped.** New tools and releases that matter
   in ML or infrastructure.

**Pass 3 must be allowed to report a quiet night.** "One paper worth your
time, nothing else" is a correct and valuable output. A system that
manufactures five items every night to justify its existence is exactly what
was rejected. Put this in the prompt explicitly, with an example.

---

## 4. Output — document, not messages

### The .md

Write `digests/YYYY-MM-DD.md` and send it to Telegram via `sendDocument`.
Commit it to the repo — free archive, greppable later.

Structure:

```markdown
# 15 September 2026

<2-3 sentences: what kind of night this was. Written like a person
talking, not a summary header.>

## Patterns

### <headline>
<2-3 plain sentences. Which items support this, with links.>

## Worth reading

### <headline>
<what it claims, why it matters to him, link>

## Someone built

### <headline>
<what it is, what problem it came from, link>
```

### Writing rules

These matter as much as the architecture. v2's output was unreadable prose
even when the findings were correct.

- **Headlines name the thing.** "Dolibarr's API can't book rooms" — not "A
  REST API limitation in a manufacturing tool."
- **Headlines have verbs.** Every v2 finding was a 40-word noun phrase. Noun
  phrases are database rows. Sentences are writing.
- **Under 12 words.**
- **Plain sentences underneath.** Write like you're texting someone smart.
- **Ban `why it matters:` as a literal string.** Say why it matters in a
  sentence instead.
- **Quotes only when they add something the summary can't carry.**
- Never open an item with a gerund or an abstract noun phrase.

### Companion message

Also send a short chat message listing the 3–5 headline items by number,
each with 🔥 / 🗑 / 👀 buttons. Documents can't carry inline buttons, and the
feedback loop is what keeps `feedback` populated and muting working.

---

## 5. Sources

Drop **Stack Overflow** — 1,304 questions in July 2026 against 207,000 at its
2014 peak. Effectively dead.

Drop **dev.to** — 403 unauthenticated.

**Build an Atom/RSS adapter.** This is a gap in adapter *types*, not sources,
and it unlocks several at once.

Add:

| Source | Adapter | Notes |
|---|---|---|
| Product Hunt | atom | `/feed` is the only anonymous surface — the rest is behind Cloudflare |
| arXiv | atom | cs.LG / cs.CL / cs.AI. The `research` kind currently has no real source |
| Hugging Face | json_api | Trending models, datasets, papers |
| YC directory + Launches | json_api | Public search infrastructure behind the directory, no key |

**Scope GitHub.** It's currently unscoped and pulled issues from mars-sim,
qBittorrent and RetroArch. Restrict to ML, infra and dev-tools repos via an
allowlist or topic filter, in source config, not code.

Keep: Hacker News, Lobsters, GH Archive.

Verify each endpoint before building its adapter. Do not invent endpoint
shapes — if you can't confirm one is free and unauthenticated, say so and
skip it.

---

## 6. Run budget

`max_run_minutes` currently defaults to 20. **Raise the default to 180.**

The machine is dedicated, actively cooled, and left running overnight
deliberately. The 20-minute cap left ~498 items unprocessed and starved two
sources entirely.

**Also fix queue fairness.** `claim()` is id-ordered, so high-volume sources
consume the whole budget before later ones are reached — that's why Lobsters
and Stack Overflow returned zero despite working adapters. Round-robin
`claim()` across sources.

---

## 7. Ranking

Keep the formula, but it is no longer what picks the digest. Pass 3 selects.

Recurrence score becomes the **shortlist filter** feeding pass 3 — top N
clusters by score, plus everything from tonight. Once clusters accumulate
across nights, recurrence starts discriminating on its own and feeds pass 3
better input.

Drop `confidence` from scoring. It sat at 0.9 for most findings across two
runs — it isn't discriminating.

---

## 8. Build order

1. Three-pass restructure (triage / deep read / synthesis)
2. Thread-context fetching for pass 2
3. Document output + `sendDocument` + companion button message
4. Run budget and queue fairness
5. Atom adapter → Product Hunt, arXiv
6. GitHub scoping
7. Hugging Face, YC

Run end to end after step 3 and send the digest. That's the first point where
the output is judgeable.

---

## 9. What success looks like

The reader opens the .md over coffee and finds something he wants to
investigate further, or a clear statement that nothing much happened.

Failure is not "no findings." Failure is five plausible-sounding items he
scrolls past.
