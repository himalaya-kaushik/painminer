"""Pass 3 — synthesis (v3 brief §3). The product; everything before is feedstock.

One LLM call. Input: all of tonight's findings together, plus the recurring
cluster shortlist from previous nights (statement, mentions, sources, days).
Output: a briefing about the night — patterns visible only across items, things
worth reading, things someone built — allowed to say it was a quiet night.

This stage does not touch the item state machine; it reads findings/clusters
and produces a structured briefing. Rendering it to Markdown and delivering it
live in delivery/ (§4).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from painminer.config import Config
from painminer.db import DB
from painminer.llm import LLM
from painminer.prompt import (
    SYNTHESIS_SCHEMA,
    SYNTHESIS_SECTIONS,
    build_synthesis_messages,
)
from painminer.pipeline.rank import SINGLE_SHOT_KINDS


@dataclass
class SynthesisResult:
    night_summary: str = ""
    # section name -> list of {headline, body, finding_ids}
    sections: dict[str, list[dict]] = field(default_factory=dict)
    # finding id -> {url, cluster_id, source, publication, kind, statement}
    findings_index: dict[int, dict] = field(default_factory=dict)
    n_findings: int = 0
    raw: str = ""

    @property
    def is_empty(self) -> bool:
        return not any(self.sections.get(s) for s in SYNTHESIS_SECTIONS)


def _since(run_started_at: str | None) -> str:
    if run_started_at:
        return run_started_at
    return (datetime.now(tz=timezone.utc) - timedelta(hours=24)).isoformat()


def gather_tonight(db: DB, since_iso: str) -> tuple[list[dict], dict[int, dict]]:
    """Findings created since `since_iso`, joined with item url/source and the
    recurrence of their cluster. Returns (findings, index)."""
    findings: list[dict] = []
    start = 0
    PAGE = 1000
    while True:
        rows = (
            db.table("findings")
            .select("id, kind, statement, why_it_matters, evidence_quote, "
                    "domain, confidence, item_id, cluster_id, created_at")
            .gte("created_at", since_iso)
            .order("id")
            .range(start, start + PAGE - 1)
            .execute()
            .data
        )
        findings.extend(rows)
        if len(rows) < PAGE:
            break
        start += PAGE

    # Resolve item url/source in chunks.
    item_ids = sorted({f["item_id"] for f in findings if f.get("item_id") is not None})
    item_meta: dict[int, dict] = {}
    for i in range(0, len(item_ids), 100):
        chunk = item_ids[i:i + 100]
        for it in db.table("items").select("id, url, source, metadata").in_("id", chunk).execute().data:
            item_meta[it["id"]] = it

    # Resolve cluster recurrence in chunks.
    cluster_ids = sorted({f["cluster_id"] for f in findings if f.get("cluster_id") is not None})
    cluster_meta: dict[int, dict] = {}
    for i in range(0, len(cluster_ids), 100):
        chunk = cluster_ids[i:i + 100]
        for c in (
            db.table("clusters")
            .select("id, mention_count, sources_seen, days_seen")
            .in_("id", chunk)
            .execute()
            .data
        ):
            cluster_meta[c["id"]] = c

    index: dict[int, dict] = {}
    for f in findings:
        it = item_meta.get(f.get("item_id"), {})
        f["_url"] = it.get("url")
        f["_source"] = it.get("source")
        f["_cluster"] = cluster_meta.get(f.get("cluster_id"))
        index[f["id"]] = {
            "url": it.get("url"),
            "cluster_id": f.get("cluster_id"),
            "source": it.get("source"),
            # Newsletter items carry no url; their publication is the
            # attribution the digest shows instead ("via TLDR AI").
            "publication": (it.get("metadata") or {}).get("publication"),
            "kind": f.get("kind"),
            "statement": f.get("statement"),
        }
    return findings, index


def _finding_line(f: dict) -> str:
    parts = [f"[{f['id']}] {f.get('kind', '?')}: {f.get('statement', '').strip()}"]
    why = (f.get("why_it_matters") or "").strip()
    if why:
        parts.append(f"why: {why}")
    src = f.get("_source")
    if src:
        parts.append(f"source: {src}")
    url = f.get("_url")
    if url:
        parts.append(url)
    cl = f.get("_cluster")
    if cl and (cl.get("mention_count") or 0) > 1:
        parts.append(
            f"recurring: {cl.get('mention_count')} mentions across "
            f"{len(cl.get('sources_seen') or [])} sources, "
            f"{len(cl.get('days_seen') or [])} days"
        )
    return " | ".join(parts)


def findings_block(findings: list[dict]) -> str:
    return "\n".join(_finding_line(f) for f in findings)


def shortlist_block(db: DB, config: Config) -> str:
    """Top recurring clusters by score (excludes single-shot kinds and muted),
    feeding pass 3 cross-night context (§7)."""
    rows = (
        db.table("clusters")
        .select("canonical_statement, kind, mention_count, sources_seen, "
                "days_seen, last_seen, score")
        .eq("muted", False)
        .order("score", desc=True)
        .limit(config.synthesis_shortlist)
        .execute()
        .data
    )
    lines = []
    for c in rows:
        if c["kind"] in SINGLE_SHOT_KINDS:
            continue
        if (c.get("mention_count") or 0) <= 1:
            continue   # only genuinely recurring clusters add cross-night value
        last = (c.get("last_seen") or "")[:10]
        lines.append(
            f"- {c['canonical_statement']} ({c['kind']}) — "
            f"{c.get('mention_count')} mentions, "
            f"{len(c.get('sources_seen') or [])} sources, "
            f"{len(c.get('days_seen') or [])} days, last seen {last}"
        )
    return "\n".join(lines)


def _parse(raw: str, index: dict[int, dict]) -> SynthesisResult:
    result = SynthesisResult(findings_index=index, n_findings=len(index), raw=raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        result.night_summary = "(synthesis returned unparseable output)"
        return result
    if not isinstance(data, dict):
        result.night_summary = "(synthesis returned a non-object)"
        return result
    result.night_summary = str(data.get("night_summary") or "").strip()
    for section in SYNTHESIS_SECTIONS:
        items = data.get(section) or []
        clean: list[dict] = []
        if isinstance(items, list):
            for it in items:
                if not isinstance(it, dict):
                    continue
                headline = str(it.get("headline") or "").strip()
                if not headline:
                    continue
                fids = it.get("finding_ids") or []
                fids = [int(x) for x in fids if isinstance(x, (int, float))]
                clean.append({
                    "headline": headline,
                    "body": str(it.get("body") or "").strip(),
                    "finding_ids": fids,
                })
        result.sections[section] = clean
    return result


def apply_caps(
    result: SynthesisResult, *, someone_built_cap: int, total_cap: int
) -> SynthesisResult:
    """Enforce the digest size limits deterministically (brief fixes 2 & 3).

    "Someone built" is capped first, then the whole briefing is capped in
    SYNTHESIS_SECTIONS priority order (patterns, worth_reading, shipped,
    someone_built) so the highest-value sections survive a total-cap cut.
    Mutates and returns the result.
    """
    if result.sections.get("someone_built"):
        result.sections["someone_built"] = result.sections["someone_built"][:someone_built_cap]
    remaining = total_cap
    for section in SYNTHESIS_SECTIONS:
        items = result.sections.get(section) or []
        result.sections[section] = items[:max(0, remaining)]
        remaining -= len(result.sections[section])
    return result


def select_for_prompt(
    findings: list[dict], max_findings: int, max_source_share: float = 1.0
) -> list[dict]:
    """The subset of tonight's findings that goes into the pass-3 prompt.

    Highest confidence first, then newest, so a hard cap drops the weakest
    material rather than an arbitrary slice. A backlog night can produce
    hundreds of findings; feeding all of them risks both the synthesis
    timeout and the model's context window, and a capped digest can only use
    a handful anyway. Returns them in id order so the block reads stably.

    Source balance: when the cap binds, no single source may take more than
    `max_source_share` of it on the first pass, so one high-volume source
    (arXiv, HN, newsletters) cannot crowd every other source out of the
    prompt. Each source keeps its own strongest findings. Slots the small
    sources don't use are then refilled from the best remaining findings of
    any source, so the prompt is never smaller than it would have been. Under
    the cap nothing is dropped and this does nothing.
    """
    if max_findings <= 0 or len(findings) <= max_findings:
        return findings
    ranked = sorted(
        findings,
        key=lambda f: (f.get("confidence") or 0.0, f.get("created_at") or ""),
        reverse=True,
    )
    per_source = max(1, int(max_findings * max_source_share))
    taken: list[dict] = []
    left: list[dict] = []
    counts: dict[str | None, int] = {}
    for f in ranked:
        src = f.get("_source")
        if len(taken) < max_findings and counts.get(src, 0) < per_source:
            taken.append(f)
            counts[src] = counts.get(src, 0) + 1
        else:
            left.append(f)
    taken.extend(left[: max_findings - len(taken)])   # refill unused slots
    return sorted(taken, key=lambda f: f["id"])


def synthesize_night(
    db: DB,
    llm: LLM,
    config: Config,
    *,
    run_started_at: str | None = None,
) -> SynthesisResult:
    """Run pass 3 over tonight's findings + the recurring shortlist."""
    since = _since(run_started_at)
    findings, index = gather_tonight(db, since)
    # Index keeps every finding (so a cited id still resolves its url/cluster
    # even if it was cut from the prompt); only the prompt block is capped.
    selected = select_for_prompt(findings, config.synthesis_max_findings,
                                 config.synthesis_max_source_share)
    messages = build_synthesis_messages(
        findings_block(selected), shortlist_block(db, config)
    )
    raw = llm.synthesize(messages, SYNTHESIS_SCHEMA)
    result = _parse(raw, index)
    return apply_caps(
        result,
        someone_built_cap=config.digest_someone_built_cap,
        total_cap=config.digest_total_cap,
    )
