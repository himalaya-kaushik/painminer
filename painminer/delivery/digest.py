"""Digest rendering (§10). Pure-ish: builds text + button specs from the DB,
with no telegram import, so it is reusable by the bot and the headless sender
and testable offline.

Layout (§10.1): up to 5 recurring items + up to 3 single-shot (research/read).
Muted clusters are excluded; pinned clusters are hidden until their
mention_count moves past what it was when pinned (§10.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from painminer.db import DB
from painminer.pipeline.rank import SINGLE_SHOT_KINDS, cluster_features

RECURRING_CAP = 5
SINGLE_SHOT_CAP = 3

KIND_EMOJI = {
    "pain": "🔴",
    "build": "🛠️",
    "signal": "📡",
    "research": "🔬",
    "read": "📄",
    "pattern": "🧩",
    "open_problem": "🧭",
}


@dataclass
class Card:
    cluster_id: int
    kind: str
    statement: str
    why: str | None
    quote: str | None
    url: str | None
    mention_count: int
    n_sources: int
    new_this_week: int


def _visible(cluster: dict) -> bool:
    """Excluded if muted, or pinned and not resurfaced since (§10.3)."""
    if cluster.get("muted"):
        return False
    if cluster.get("pinned"):
        pinned_at = cluster.get("pinned_mention_count") or 0
        if (cluster.get("mention_count") or 0) <= pinned_at:
            return False
    return True


def _new_this_week(days_seen: list[str] | None) -> int:
    if not days_seen:
        return 0
    cutoff = (datetime.now(tz=timezone.utc).date() - timedelta(days=7)).isoformat()
    return sum(1 for d in days_seen if d >= cutoff)


def _representative(db: DB, cluster_id: int) -> tuple[str | None, str | None, str | None]:
    """(why_it_matters, evidence_quote, url) from the cluster's top finding."""
    rows = (
        db.table("findings")
        .select("why_it_matters, evidence_quote, item_id")
        .eq("cluster_id", cluster_id)
        .order("confidence", desc=True)
        .limit(1)
        .execute()
        .data
    )
    if not rows:
        return None, None, None
    f = rows[0]
    url = None
    if f.get("item_id") is not None:
        item = db.table("items").select("url").eq("id", f["item_id"]).limit(1).execute().data
        if item:
            url = item[0].get("url")
    return f.get("why_it_matters"), f.get("evidence_quote"), url


def _card(db: DB, cluster: dict) -> Card:
    why, quote, url = _representative(db, cluster["id"])
    return Card(
        cluster_id=cluster["id"],
        kind=cluster["kind"],
        statement=cluster["canonical_statement"],
        why=why,
        quote=quote,
        url=url,
        mention_count=cluster.get("mention_count") or 0,
        n_sources=len(cluster.get("sources_seen") or []),
        new_this_week=_new_this_week(cluster.get("days_seen")),
    )


def top_clusters(db: DB) -> tuple[list[Card], list[Card]]:
    """(recurring, single_shot) cards for the leaderboard."""
    recurring_rows = (
        db.table("clusters")
        .select("*")
        .eq("muted", False)
        .order("score", desc=True)
        .limit(50)
        .execute()
        .data
    )
    recurring = [
        _card(db, c) for c in recurring_rows
        if c["kind"] not in SINGLE_SHOT_KINDS and _visible(c)
    ][:RECURRING_CAP]

    single_rows = (
        db.table("clusters")
        .select("*")
        .eq("muted", False)
        .in_("kind", list(SINGLE_SHOT_KINDS))
        .order("last_seen", desc=True)          # single-shot ranked by recency (§9.1)
        .limit(30)
        .execute()
        .data
    )
    single = [_card(db, c) for c in single_rows if _visible(c)][:SINGLE_SHOT_CAP]
    return recurring, single


def format_card(card: Card) -> str:
    emoji = KIND_EMOJI.get(card.kind, "•")
    lines = [f"{emoji} {card.statement}"]
    if card.why:
        lines.append(f"   {card.why}")
    if card.quote:
        lines.append(f'   "{card.quote}"')
    lines.append(
        f"   {card.mention_count} mentions · {card.n_sources} sources · "
        f"{card.new_this_week} new this week  ·  #{card.cluster_id}"
    )
    return "\n".join(lines)


def run_footer(db: DB) -> str:
    rows = db.table("runs").select("*").order("started_at", desc=True).limit(1).execute().data
    if not rows:
        return "no runs yet"
    r = rows[0]
    stamp = r.get("finished_at") or r.get("started_at")
    ago = "?"
    if stamp:
        delta = datetime.now(tz=timezone.utc) - datetime.fromisoformat(stamp)
        mins = int(delta.total_seconds() // 60)
        ago = f"{mins}m ago" if mins < 120 else f"{mins // 60}h ago"
    return (
        f"last run {ago} · {r.get('items_fetched', 0)} fetched · "
        f"{r.get('findings_created', 0)} findings"
    )


def status_text(db: DB) -> str:
    lines = ["*Status*", run_footer(db), "", "*Queue*"]
    for state in ("fetched", "ready", "processing", "done", "failed", "duplicate"):
        n = db.table("items").select("id", count="exact").eq("state", state).execute().count
        lines.append(f"  {state}: {n}")
    lines.append("")
    lines.append("*Sources* (last successful fetch)")
    for s in db.table("sources").select("name, last_successful_fetch_at, enabled").order("name").execute().data:
        wm = s.get("last_successful_fetch_at") or "never"
        flag = "" if s.get("enabled") else " (disabled)"
        n = db.table("items").select("id", count="exact").eq("source", s["name"]).execute().count
        lines.append(f"  {s['name']}: {n} items · {wm}{flag}")
    return "\n".join(lines)


def kinds_text(db: DB) -> str:
    counts: dict[str, int] = {}
    start = 0
    while True:
        rows = db.table("findings").select("kind").order("id").range(start, start + 999).execute().data
        for r in rows:
            counts[r["kind"]] = counts.get(r["kind"], 0) + 1
        if len(rows) < 1000:
            break
        start += 1000
    if not counts:
        return "No findings yet."
    lines = ["*Findings by kind*"]
    for kind, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        known = "" if kind in KIND_EMOJI else "  (new kind)"
        lines.append(f"  {kind}: {n}{known}")
    return "\n".join(lines)


def why_text(db: DB, cluster_id: int) -> str:
    clusters = db.table("clusters").select("*").eq("id", cluster_id).limit(1).execute().data
    if not clusters:
        return f"No cluster #{cluster_id}."
    c = clusters[0]
    lines = [f"*#{cluster_id} — {c['canonical_statement']}*",
             f"_{c['kind']} · score {c.get('score', 0):.1f} · "
             f"{c.get('mention_count', 0)} mentions_", ""]
    findings = (
        db.table("findings")
        .select("evidence_quote, item_id")
        .eq("cluster_id", cluster_id)
        .order("confidence", desc=True)
        .limit(20)
        .execute()
        .data
    )
    for f in findings:
        url = ""
        if f.get("item_id") is not None:
            item = db.table("items").select("url").eq("id", f["item_id"]).limit(1).execute().data
            if item and item[0].get("url"):
                url = f"\n  {item[0]['url']}"
        quote = f.get("evidence_quote") or "(no quote)"
        lines.append(f'• "{quote}"{url}')
    return "\n".join(lines)


def feedback_features(db: DB, cluster_id: int) -> dict:
    """Stable feature snapshot logged with a button press (§9.2)."""
    rows = db.table("clusters").select("*").eq("id", cluster_id).limit(1).execute().data
    return cluster_features(rows[0]) if rows else {}
