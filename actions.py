"""Button actions (§10.3): 🔥 interesting, 🗑 mute, 👀 pin.

Each writes an append-only feedback row with the stable feature snapshot (§9.2);
mute and pin also flip cluster state. Reused by the bot's callback handler.
"""

from __future__ import annotations

from db import DB
from digest import feedback_features
from rank import SCORE_VERSION


def record_feedback(db: DB, cluster_id: int, verdict: str) -> None:
    db.insert("feedback", {
        "cluster_id": cluster_id,
        "verdict": verdict,
        "features_json": feedback_features(db, cluster_id),
        "score_version": SCORE_VERSION,
    })


def fire(db: DB, cluster_id: int) -> str:
    """🔥 label interesting."""
    record_feedback(db, cluster_id, "interesting")
    return "🔥 marked interesting"


def mute(db: DB, cluster_id: int) -> str:
    """🗑 mute the cluster permanently (excluded from /top)."""
    db.table("clusters").update({"muted": True}).eq("id", cluster_id).execute()
    record_feedback(db, cluster_id, "kill")
    return "🗑 muted — won't show again"


def pin(db: DB, cluster_id: int) -> str:
    """👀 pin: resurface only when its mention_count changes."""
    rows = db.table("clusters").select("mention_count").eq("id", cluster_id).limit(1).execute().data
    current = (rows[0]["mention_count"] if rows else 0) or 0
    db.table("clusters").update(
        {"pinned": True, "pinned_mention_count": current}
    ).eq("id", cluster_id).execute()
    record_feedback(db, cluster_id, "watch")
    return "👀 pinned — back when its count changes"


DISPATCH = {"fire": fire, "mute": mute, "pin": pin}
