"""Dedupe stage: promote fetched items to `ready` or `duplicate` (§4.1).

Structural dedupe ONLY. No lexical filter, no embedding filter — those were
deliberately removed in v2; everything else reaches the model.

Rules, applied to each `fetched` item:

1. Too short — raw_text below a minimum carries nothing.
2. Link-only — the text is just a bare URL with no words of its own.
3. content_hash already accepted — same content already seen (this run or a
   previous one), possibly from another source.

Anything else becomes `ready`. Dropped items become `duplicate` (the one
non-ready outcome the documented state machine allows for this stage, §4).

Idempotent: only touches `fetched` rows, decisions are deterministic, and a
crash mid-batch just leaves rows `fetched` for the next run to redecide.
Uniqueness on (source, source_id) is already enforced at insert time (§4.1),
so it needs no work here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from painminer.db import DB

MIN_CHARS = 40                      # below this, a comment carries nothing
ACCEPTED_STATES = ("ready", "processing", "done")
_URL_ONLY = re.compile(r"^\s*https?://\S+\s*$", re.IGNORECASE)
PAGE_SIZE = 500
# content_hashes are 64 chars; too many in one `in.(...)` filter overflows the
# request URL and PostgREST returns 400. Chunk IN-filter lookups.
IN_CHUNK = 100


def _chunks(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


@dataclass
class DedupeSummary:
    ready: int
    duplicate: int

    @property
    def total(self) -> int:
        return self.ready + self.duplicate


def _too_short(text: str) -> bool:
    return len(" ".join(text.split())) < MIN_CHARS


def _link_only(text: str) -> bool:
    return bool(_URL_ONLY.match(text))


def classify(text: str, content_hash: str | None, accepted: set[str]) -> tuple[str, str]:
    """Pure structural-dedupe decision (§4.1).

    Returns (state, reason) where state is 'ready' or 'duplicate'. `accepted`
    is the set of content_hashes already accepted (this run + prior runs).
    Kept pure and side-effect-free so it is unit-testable without a database.
    """
    if _too_short(text):
        return "duplicate", "too_short"
    if _link_only(text):
        return "duplicate", "link_only"
    if content_hash and content_hash in accepted:
        return "duplicate", "content_hash"
    return "ready", "ok"


def _fetched_page(db: DB, limit: int) -> list[dict]:
    resp = (
        db.table("items")
        .select("id, raw_text, content_hash")
        .eq("state", "fetched")
        .order("id")
        .limit(limit)
        .execute()
    )
    return resp.data


def _existing_accepted_hashes(db: DB, hashes: list[str]) -> set[str]:
    found: set[str] = set()
    for chunk in _chunks(hashes, IN_CHUNK):
        resp = (
            db.table("items")
            .select("content_hash")
            .in_("state", list(ACCEPTED_STATES))
            .in_("content_hash", chunk)
            .execute()
        )
        found.update(r["content_hash"] for r in resp.data if r["content_hash"])
    return found


def _apply(db: DB, ids: list[int], state: str) -> None:
    for chunk in _chunks(ids, IN_CHUNK):
        db.table("items").update({"state": state}).in_("id", chunk).execute()


def dedupe(db: DB, page_size: int = PAGE_SIZE) -> DedupeSummary:
    """Process all fetched items, one page at a time. Returns final counts."""
    summary = DedupeSummary(ready=0, duplicate=0)

    while True:
        batch = _fetched_page(db, page_size)
        if not batch:
            break

        hashes = [r["content_hash"] for r in batch if r["content_hash"]]
        accepted = _existing_accepted_hashes(db, hashes)

        ready_ids: list[int] = []
        dup_ids: list[int] = []
        for row in batch:
            state, _reason = classify(row["raw_text"] or "", row["content_hash"], accepted)
            if state == "ready":
                ready_ids.append(row["id"])
                if row["content_hash"]:
                    accepted.add(row["content_hash"])   # dedupe within this run too
            else:
                dup_ids.append(row["id"])

        _apply(db, ready_ids, "ready")
        _apply(db, dup_ids, "duplicate")
        summary.ready += len(ready_ids)
        summary.duplicate += len(dup_ids)

        if len(batch) < page_size:
            break

    return summary


def main() -> None:
    db = DB.from_config()
    summary = dedupe(db)
    print(
        f"dedupe: {summary.ready} ready, {summary.duplicate} duplicate "
        f"({summary.total} processed)"
    )


if __name__ == "__main__":
    main()
