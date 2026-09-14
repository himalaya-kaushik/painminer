"""Ranking stage (§9).

    score = 2*|sources_seen| + 1.5*|days_seen| + log2(1 + mention_count)
            + (2 if last_seen within 7 days else 0)

Breadth and persistence are linear (bounded, hard to fake); mentions are
log2-capped (the weakest signal); recency is a flat bonus, not decay. Sets are
counted raw (§9). `confidence` is deliberately NOT an input — it did not
discriminate. `score_version` is written on every run; the FEATURE SET is kept
stable even though the weights are disposable (§9.2).

Single-shot kinds (`research`, `read`) will never rank under this formula and
are ranked by recency in the digest instead (§9.1); their score is still
computed here for completeness.

    .venv/bin/python -m painminer.pipeline.rank
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from painminer.db import DB

SCORE_VERSION = "v1-recurrence"
RECENT_DAYS = 7
SINGLE_SHOT_KINDS = frozenset({"research", "read"})   # ranked by recency (§9.1)


def recurrence_score(
    n_sources: int, n_days: int, mention_count: int, recent: bool
) -> float:
    """The §9 formula. Pure and unit-testable."""
    return (
        2.0 * n_sources
        + 1.5 * n_days
        + math.log2(1 + mention_count)
        + (2.0 if recent else 0.0)
    )


def cluster_features(cluster: dict, *, now: datetime | None = None) -> dict:
    """The stable feature set logged for ranking / future model fitting (§9.2).

    Keep these keys stable across versions; weights change, features do not.
    """
    now = now or datetime.now(tz=timezone.utc)
    n_sources = len(cluster.get("sources_seen") or [])
    n_days = len(cluster.get("days_seen") or [])
    mention_count = cluster.get("mention_count") or 0
    recent = _is_recent(cluster.get("last_seen"), now)
    return {
        "n_sources": n_sources,
        "n_days": n_days,
        "mention_count": mention_count,
        "recent": recent,
        "kind": cluster.get("kind"),
    }


def _is_recent(last_seen: str | None, now: datetime) -> bool:
    if not last_seen:
        return False
    ts = datetime.fromisoformat(last_seen)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (now - ts) <= timedelta(days=RECENT_DAYS)


@dataclass
class RankSummary:
    ranked: int = 0


def rank_clusters(db: DB) -> RankSummary:
    """Recompute and write score + score_version for every cluster. Idempotent."""
    summary = RankSummary()
    now = datetime.now(tz=timezone.utc)
    start = 0
    PAGE = 1000
    while True:
        rows = (
            db.table("clusters")
            .select("id, kind, sources_seen, days_seen, mention_count, last_seen")
            .order("id")
            .range(start, start + PAGE - 1)
            .execute()
            .data
        )
        if not rows:
            break
        for c in rows:
            f = cluster_features(c, now=now)
            score = recurrence_score(f["n_sources"], f["n_days"],
                                     f["mention_count"], f["recent"])
            db.table("clusters").update(
                {"score": score, "score_version": SCORE_VERSION}
            ).eq("id", c["id"]).execute()
            summary.ranked += 1
        if len(rows) < PAGE:
            break
        start += PAGE
    return summary


def main() -> None:
    db = DB.from_config()
    s = rank_clusters(db)
    print(f"rank: {s.ranked} clusters scored ({SCORE_VERSION})")


if __name__ == "__main__":
    main()
