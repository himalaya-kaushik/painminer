"""Clustering stage (§8): group findings that describe the same thing.

For each embedded, unclustered finding: cosine-search against the centroids of
existing clusters of the SAME kind.

    similarity >= merge_threshold      -> auto-merge
    tiebreak_low <= sim < merge        -> LLM tiebreak ("same underlying thing?")
    sim < tiebreak_low                 -> new cluster

Centroids are anchored on a cluster's first member and never recomputed as a
running mean (§8). Mentions are deduped by (cluster, source, date) before the
cluster's sets/count move (record_mention). Embeddings serve clustering only.

    .venv/bin/python -m painminer.pipeline.cluster
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np

from painminer.config import Config, load_config
from painminer.db import DB
from painminer.pipeline.embed import parse_vector
from painminer.llm import LLM

PAGE = 200


@dataclass
class ClusterSummary:
    clustered: int = 0
    new_clusters: int = 0
    auto_merges: int = 0
    tiebreaks: int = 0
    tiebreak_merges: int = 0
    new_mentions: int = 0


@dataclass
class _Cluster:
    id: int
    centroid: np.ndarray
    statement: str


def decide(best_sim: float | None, merge_threshold: float, tiebreak_low: float) -> str:
    """Pure clustering decision (§8). Returns 'merge', 'tiebreak', or 'new'."""
    if best_sim is None or best_sim < tiebreak_low:
        return "new"
    if best_sim >= merge_threshold:
        return "merge"
    return "tiebreak"


def _normalize(vec: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(vec)
    return vec / norm if norm else vec


def _load_kind(db: DB, kind: str) -> list[_Cluster]:
    rows = (
        db.table("clusters")
        .select("id, centroid, canonical_statement")
        .eq("kind", kind)
        .execute()
        .data
    )
    out = []
    for r in rows:
        if r["centroid"]:
            out.append(_Cluster(r["id"], _normalize(parse_vector(r["centroid"])),
                                r["canonical_statement"]))
    return out


def _source_map(db: DB, item_ids: list[int]) -> dict[int, str]:
    out: dict[int, str] = {}
    for i in range(0, len(item_ids), 100):
        chunk = item_ids[i:i + 100]
        rows = db.table("items").select("id, source").in_("id", chunk).execute().data
        out.update({r["id"]: r["source"] for r in rows})
    return out


def _unclustered(db: DB) -> list[dict]:
    rows: list[dict] = []
    start = 0
    while True:
        page = (
            db.table("findings")
            .select("id, item_id, kind, statement, embedding")
            .not_.is_("embedding", "null")
            .is_("cluster_id", "null")
            .order("id")
            .range(start, start + PAGE - 1)
            .execute()
            .data
        )
        rows.extend(page)
        if len(page) < PAGE:
            return rows
        start += PAGE


def cluster_findings(db: DB, llm: LLM, config: Config) -> ClusterSummary:
    """Assign every embedded, unclustered finding to a cluster. Idempotent."""
    summary = ClusterSummary()
    findings = _unclustered(db)
    if not findings:
        return summary

    sources = _source_map(db, [f["item_id"] for f in findings])
    today = datetime.now(tz=timezone.utc).date().isoformat()
    cache: dict[str, list[_Cluster]] = {}          # kind -> clusters (grows in-run)

    for f in findings:
        vec = _normalize(parse_vector(f["embedding"]))
        kind = f["kind"]
        if kind not in cache:
            cache[kind] = _load_kind(db, kind)
        candidates = cache[kind]

        best: _Cluster | None = None
        best_sim = -1.0
        for c in candidates:
            sim = float(np.dot(vec, c.centroid))
            if sim > best_sim:
                best_sim, best = sim, c

        sim = best_sim if best is not None else None
        action = decide(sim, config.cluster_merge_threshold, config.cluster_tiebreak_low)

        merge_into: _Cluster | None = None
        if action == "merge":
            merge_into = best
            summary.auto_merges += 1
        elif action == "tiebreak":
            summary.tiebreaks += 1
            if llm.same_underlying_thing(f["statement"], best.statement):
                merge_into = best
                summary.tiebreak_merges += 1

        if merge_into is None:
            cid = db.client.rpc("create_cluster", {
                "p_kind": kind,
                "p_statement": f["statement"],
                "p_centroid": f["embedding"],       # anchor on this member
            }).execute().data
            cache[kind].append(_Cluster(cid, vec, f["statement"]))
            summary.new_clusters += 1
        else:
            cid = merge_into.id

        db.table("findings").update({"cluster_id": cid}).eq("id", f["id"]).execute()
        source = sources.get(f["item_id"], "unknown")
        is_new = db.client.rpc("record_mention", {
            "p_cluster_id": cid, "p_source": source, "p_seen_on": today,
        }).execute().data
        if is_new:
            summary.new_mentions += 1
        summary.clustered += 1

    return summary


def main() -> None:
    config = load_config()
    db = DB.from_config()
    llm = LLM(config)
    s = cluster_findings(db, llm, config)
    print(
        f"cluster: {s.clustered} findings -> {s.new_clusters} new clusters, "
        f"{s.auto_merges} auto-merges, {s.tiebreak_merges}/{s.tiebreaks} tiebreak merges, "
        f"{s.new_mentions} new mentions"
    )


if __name__ == "__main__":
    main()
