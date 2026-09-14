"""Phase 2 acceptance test (brief §Phase 2 verify).

Run the HN fetch twice over a fixed historical window; the second run must
write zero new rows (proving the unique constraint), and the watermark must
not move (proving fetch never advances it).

Uses a fixed 10-minute window in the past so the result set is stable and the
run is fast. Temporarily sets overlap_hours=0 for that determinism, then
restores the production config.

    .venv/bin/python -m painminer.tools.test_phase2
"""

from __future__ import annotations

from datetime import datetime, timezone

from painminer.db import DB
from painminer.pipeline.fetch import fetch_source
from painminer.tools import seed_sources

# A fixed, historical 10-minute window (well in the past → stable result set).
T0 = int(datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc).timestamp())
WINDOW = 600  # seconds


def main() -> None:
    db = DB.from_config()
    seed_sources.main()

    # Clean slate for this source so run-1 counts are meaningful.
    db.table("items").delete().eq("source", "hackernews").execute()

    # Deterministic window: no overlap, fixed bounds.
    db.table("sources").update(
        {"config_json": {**seed_sources.HACKER_NEWS["config_json"], "overlap_hours": 0}}
    ).eq("name", "hackernews").execute()

    wm_before = db.get("sources", name="hackernews")["last_successful_fetch_at"]

    try:
        run1 = fetch_source(db, "hackernews", since_ts=T0, until_ts=T0 + WINDOW)
        run2 = fetch_source(db, "hackernews", since_ts=T0, until_ts=T0 + WINDOW)
    finally:
        seed_sources.main()  # restore production config (overlap_hours=6)

    wm_after = db.get("sources", name="hackernews")["last_successful_fetch_at"]

    print(f"run 1: fetched={run1.items_fetched} inserted={run1.items_inserted}")
    print(f"run 2: fetched={run2.items_fetched} inserted={run2.items_inserted}")
    print(f"watermark before={wm_before!r} after={wm_after!r}")

    assert run1.items_inserted > 0, "run 1 inserted nothing — window empty or broken"
    assert run2.items_inserted == 0, "run 2 inserted rows — unique constraint not working"
    assert run2.items_fetched == run1.items_fetched, "window not stable across runs"
    assert wm_before == wm_after, "watermark moved — fetch must not advance it"

    # Sanity: rows landed in state=fetched.
    sample = db.get("items", source="hackernews")
    assert sample["state"] == "fetched", f"unexpected state {sample['state']!r}"

    print("PHASE 2 OK — rerun wrote zero, watermark unchanged, state=fetched")


if __name__ == "__main__":
    main()
