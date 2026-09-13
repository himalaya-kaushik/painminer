"""Fetch stage: pull a source's new items into `items` with state=fetched.

Pages are written and dropped one at a time (§4.3). Rows are inserted with
ON CONFLICT DO NOTHING on (source, source_id), so re-fetching an already-seen
item is free — that unique constraint is what makes the 6h overlap and reruns
safe (§4.1, §5.1).

This stage does NOT advance the watermark. `sources.last_successful_fetch_at`
moves only after the judge stage succeeds (§5.1); doing it here would mark
unprocessed data as seen on a mid-run crash.

    python fetch.py <source_name>
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from adapters import get_adapter
from db import DB

DEFAULT_BACKFILL_DAYS = 30       # never-run watermark = now - 30d (§5.1)


def _make_client() -> httpx.Client:
    """HTTP client for fetching.

    Binds the source socket to the IPv4 wildcard so connections use IPv4 only.
    httpcore's sync backend has no Happy Eyeballs: on a dual-stack host with no
    IPv6 route, an IPv6-first DNS answer stalls every request until timeout.
    `retries` also rides out transient connect blips.
    """
    transport = httpx.HTTPTransport(local_address="0.0.0.0", retries=2)
    return httpx.Client(headers={"User-Agent": "painminer/0.1"}, transport=transport)


@dataclass
class FetchSummary:
    source: str
    items_fetched: int
    items_inserted: int
    since_ts: int
    until_ts: int | None
    started_at: str


def _iso_to_epoch(value: str) -> int:
    # Supabase returns e.g. "2026-09-13T06:22:09.301516+00:00"
    return int(datetime.fromisoformat(value).timestamp())


def _watermark_epoch(source: dict) -> int:
    wm = source.get("last_successful_fetch_at")
    if wm:
        return _iso_to_epoch(wm)
    return int(datetime.now(tz=timezone.utc).timestamp()) - DEFAULT_BACKFILL_DAYS * 86400


def fetch_source(
    db: DB,
    source_name: str,
    *,
    since_ts: int | None = None,
    until_ts: int | None = None,
    on_page: Callable[[int, int], None] | None = None,
) -> FetchSummary:
    """Fetch new items for one source. Returns counts; leaves the watermark."""
    source = db.get("sources", name=source_name)
    if source is None:
        raise ValueError(f"unknown source {source_name!r}")
    if not source.get("enabled", True):
        raise ValueError(f"source {source_name!r} is disabled")

    started_at = datetime.now(tz=timezone.utc)
    since = since_ts if since_ts is not None else _watermark_epoch(source)

    fetched = 0
    inserted = 0
    with _make_client() as client:
        adapter = get_adapter(source, client)
        for page in adapter.iter_items(since, until_ts):
            fetched += len(page)
            rows = [
                {
                    "source": it.source,
                    "source_id": it.source_id,
                    "url": it.url,
                    "raw_text": it.raw_text,
                    "content_hash": it.content_hash,
                    "thread_id": it.thread_id,
                    "metadata": it.metadata or {},
                    "state": "fetched",
                }
                for it in page
            ]
            resp = (
                db.table("items")
                .upsert(rows, on_conflict="source,source_id", ignore_duplicates=True)
                .execute()
            )
            inserted += len(resp.data)
            if on_page is not None:
                on_page(fetched, inserted)

    return FetchSummary(
        source=source_name,
        items_fetched=fetched,
        items_inserted=inserted,
        since_ts=since,
        until_ts=until_ts,
        started_at=started_at.isoformat(),
    )


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: python fetch.py <source_name>")
    db = DB.from_config()

    def progress(fetched: int, inserted: int) -> None:
        print(f"  fetched {fetched}, inserted {inserted}", flush=True)

    summary = fetch_source(db, sys.argv[1], on_page=progress)
    print(
        f"{summary.source}: fetched {summary.items_fetched}, "
        f"inserted {summary.items_inserted} new "
        f"(window since {summary.since_ts}, watermark unchanged)"
    )


if __name__ == "__main__":
    main()
