"""Phase 3 acceptance test (brief §Phase 3 verify).

Covers structural dedupe (rules + idempotency) and the queue: claim moves
ready->processing, a mid-run 'crash' loses nothing (remaining work still
claims, in-flight rows recover), stuck rows are reclaimed, and a row out of
attempts is retired to 'failed'.

    python test_phase3.py
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from adapters import content_hash
from db import DB
import dedupe
import queue_ops

SRC = "phase3_test"
LONG_A = ("Solo freelancers manually copy Stripe payouts into accounting "
          "spreadsheets every single week, which wastes hours.")
LONG_B = ("Teams running self-hosted Postgres keep hitting connection limits "
          "because pgbouncer defaults are never tuned for their workload.")


def _rows_by_state(db: DB) -> dict[str, list[dict]]:
    resp = db.table("items").select("source_id, state, attempts").eq("source", SRC).execute()
    out: dict[str, list[dict]] = {}
    for r in resp.data:
        out.setdefault(r["state"], []).append(r)
    return out


def _processing_ids(db: DB) -> list[int]:
    resp = db.table("items").select("id").eq("source", SRC).eq("state", "processing").order("id").execute()
    return [r["id"] for r in resp.data]


def setup(db: DB) -> None:
    db.upsert("sources", {"name": SRC, "adapter": "json_api", "enabled": False,
                          "config_json": {}}, on_conflict="name")
    # Isolate the queue: dedupe and claim are global by design, so clear items
    # for every existing source first (scoped, dev/test data only) so only our
    # controlled rows are in play.
    existing = {r["source"] for r in db.table("items").select("source").execute().data}
    for src in existing | {SRC}:
        db.table("items").delete().eq("source", src).execute()

    def item(sid, text):
        return {"source": SRC, "source_id": sid, "url": None, "raw_text": text,
                "content_hash": content_hash(text), "state": "fetched"}

    # Insert ok1 before its duplicate so ok1 wins the content_hash race.
    db.insert("items", item("ok1", LONG_A))
    db.table("items").insert([
        item("ok2", LONG_B),
        item("too_short", "too short"),                       # < MIN_CHARS -> duplicate
        item("link_only", "https://example.com/some/article"),# bare URL   -> duplicate
        item("dup_of_ok1", LONG_A),                           # same hash  -> duplicate
    ]).execute()


def test_dedupe(db: DB) -> None:
    summary = dedupe.dedupe(db)
    print(f"dedupe: ready={summary.ready} duplicate={summary.duplicate}")

    states = _rows_by_state(db)
    ready = {r["source_id"] for r in states.get("ready", [])}
    dups = {r["source_id"] for r in states.get("duplicate", [])}
    assert ready == {"ok1", "ok2"}, f"ready set wrong: {ready}"
    assert dups == {"too_short", "link_only", "dup_of_ok1"}, f"dup set wrong: {dups}"
    assert not states.get("fetched"), "fetched rows left after dedupe"

    # Idempotent: nothing left to do.
    again = dedupe.dedupe(db)
    assert again.total == 0, f"second dedupe touched {again.total} rows"
    print("dedupe idempotent OK")


def test_queue(db: DB) -> None:
    # Claim one row, then simulate a crash (just stop). One row in flight.
    first = queue_ops.claim(db, 1)
    assert len(first) == 1, f"expected 1 claimed, got {len(first)}"
    assert first[0]["state"] == "processing" and first[0]["attempts"] == 1
    assert len(_processing_ids(db)) == 1
    print("claim moved ready->processing (attempts=1)")

    # Next run: in-flight row is not stuck yet, so it is NOT reclaimed; the
    # remaining ready row is picked up. Nothing lost.
    second = queue_ops.claim(db, 10)
    claimed_ids = {r["id"] for r in second}
    assert first[0]["id"] not in claimed_ids, "reclaimed a row that was not stuck"
    assert len(_processing_ids(db)) == 2, "did not resume remaining work"
    print("resume-after-crash OK (remaining work claimed, in-flight untouched)")

    # Reclaim: age one processing row past the stuck window; it must reclaim
    # and its attempts must increment.
    victim = _processing_ids(db)[0]
    stale = (datetime.now(tz=timezone.utc) - timedelta(hours=2)).isoformat()
    db.table("items").update({"processing_at": stale}).eq("id", victim).execute()
    reclaimed = queue_ops.claim(db, 10)
    assert victim in {r["id"] for r in reclaimed}, "stuck row was not reclaimed"
    row = db.get("items", id=victim)
    assert row["attempts"] == 2, f"attempts not incremented on reclaim: {row['attempts']}"
    print("reclaim of stuck row OK (attempts incremented)")

    # Three strikes: a stuck row out of attempts is retired to 'failed', not
    # returned.
    db.table("items").update({"attempts": 3, "processing_at": stale}).eq("id", victim).execute()
    out = queue_ops.claim(db, 10)
    assert victim not in {r["id"] for r in out}, "poison row was re-claimed"
    assert db.get("items", id=victim)["state"] == "failed", "poison row not failed"
    print("three-strikes -> failed OK")


def main() -> None:
    db = DB.from_config()
    setup(db)
    test_dedupe(db)
    test_queue(db)
    db.table("items").delete().eq("source", SRC).execute()   # clean up
    print("PHASE 3 OK — structural dedupe + queue (claim, resume, reclaim, strikes)")


if __name__ == "__main__":
    main()
