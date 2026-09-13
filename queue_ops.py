"""Queue operations over the `items` state machine (§4.2).

Thin Python wrappers around the claim_items() SQL function and the terminal
state transitions. The judge stage (Phase 4) drives these: claim a batch, work
each row, then mark it done or failed.

Claim semantics (enforced in SQL, atomically):
  * takes rows in state 'ready', plus 'processing' rows stuck > stuck_seconds
    (crash recovery),
  * retires rows out of attempts to 'failed' (three strikes),
  * increments attempts and stamps processing_at on claim.
"""

from __future__ import annotations

from typing import Any

from db import DB

STUCK_SECONDS = 3600      # reclaim rows stuck in processing longer than this (§4.2)
MAX_ATTEMPTS = 3          # three strikes -> failed (§4.2)


def claim(
    db: DB,
    batch_size: int,
    *,
    stuck_seconds: int = STUCK_SECONDS,
    max_attempts: int = MAX_ATTEMPTS,
) -> list[dict[str, Any]]:
    """Atomically claim up to batch_size items for processing."""
    resp = db.client.rpc(
        "claim_items",
        {
            "batch_size": batch_size,
            "stuck_seconds": stuck_seconds,
            "max_attempts": max_attempts,
        },
    ).execute()
    return resp.data or []


def mark_done(db: DB, item_id: int) -> None:
    """Terminal success. Phase 4 will instead do this transactionally with
    finding writes and raw_text nulling; this is the plain transition."""
    db.table("items").update({"state": "done"}).eq("id", item_id).execute()


def mark_failed(db: DB, item_id: int) -> None:
    """Terminal failure for this item; never blocks the rest of the queue."""
    db.table("items").update({"state": "failed"}).eq("id", item_id).execute()


def release(db: DB, item_id: int) -> None:
    """Return an item to the queue (e.g. LLM host unreachable, not the item's
    fault). Leaves attempts as-is so a transient outage doesn't burn strikes."""
    db.table("items").update({"state": "ready"}).eq("id", item_id).execute()
