"""Phase 5 verification (§8): two differently-worded versions of the same
problem must land in one cluster; a clearly different one must not.

Inserts synthetic findings under a throwaway source, embeds, clusters, asserts,
and cleans up everything it created.

    .venv/bin/python -m painminer.tools.verify_phase5
"""

from __future__ import annotations

from painminer.config import load_config
from painminer.db import DB
from painminer.pipeline.embed import Embedder, embed_findings
from painminer.pipeline.cluster import cluster_findings
from painminer.llm import LLM

SRC = "phase5_test"

# Two paraphrases of the same pain, and one unrelated pain.
A1 = ("Developers waste time manually copying production error logs from "
      "dashboards into their local editor to debug issues")
A2 = ("Engineers have to hand-copy production error logs into their IDE to "
      "investigate bugs, which is slow and tedious")
B = ("There is no good open-source tool for real-time collaborative music "
     "composition in the browser")


def _setup(db: DB) -> list[int]:
    db.upsert("sources", {"name": SRC, "adapter": "json_api", "enabled": False,
                          "config_json": {}}, on_conflict="name")
    db.table("items").delete().eq("source", SRC).execute()
    item = db.insert("items", {"source": SRC, "source_id": "p5", "state": "done",
                               "raw_text": None})
    fids = []
    for stmt in (A1, A2, B):
        row = db.insert("findings", {"item_id": item["id"], "kind": "pain",
                                     "statement": stmt, "why_it_matters": "x",
                                     "domain": "test", "evidence_quote": "x",
                                     "confidence": 0.7})
        fids.append(row["id"])
    return fids


def _cleanup(db: DB, fids: list[int]) -> None:
    # cluster_ids created for these findings -> remove their clusters (cascade
    # clears mentions), then the test items/source.
    rows = db.table("findings").select("cluster_id").in_("id", fids).execute().data
    cids = {r["cluster_id"] for r in rows if r["cluster_id"]}
    db.table("items").delete().eq("source", SRC).execute()   # cascades findings
    for cid in cids:
        db.table("clusters").delete().eq("id", cid).execute()  # cascades mentions
    db.table("sources").delete().eq("name", SRC).execute()


def main() -> None:
    config = load_config()
    db = DB.from_config()
    fids = _setup(db)
    try:
        n = embed_findings(db, Embedder.from_config(config), config.embed_batch_size)
        print(f"embedded {n} findings")
        s = cluster_findings(db, LLM(config), config)
        print(f"cluster: {s.new_clusters} new, {s.auto_merges} auto-merge, "
              f"{s.tiebreak_merges}/{s.tiebreaks} tiebreak merges")

        rows = db.table("findings").select("id, statement, cluster_id").in_(
            "id", fids).execute().data
        by_id = {r["id"]: r for r in rows}
        a1, a2, b = by_id[fids[0]], by_id[fids[1]], by_id[fids[2]]
        print(f"A1 cluster={a1['cluster_id']}  A2 cluster={a2['cluster_id']}  "
              f"B cluster={b['cluster_id']}")

        assert a1["cluster_id"] is not None
        assert a1["cluster_id"] == a2["cluster_id"], "paraphrases did NOT co-cluster"
        assert b["cluster_id"] != a1["cluster_id"], "unrelated finding merged in"
        print("PHASE 5 OK — paraphrases co-clustered; unrelated stayed separate")
    finally:
        _cleanup(db, fids)
        print("cleaned up phase5_test data")


if __name__ == "__main__":
    main()
