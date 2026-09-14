"""Hacker News adapter over the Algolia API (§3.2, §5.1).

Uses the generic JSON-API engine (JsonApiAdapter) for HTTP and item mapping,
and overrides `iter_items` for two things Algolia forces on us:

1. Prioritized, targeted passes. Instead of draining the general comment
   firehose, it runs a configured list of `searches` — each a `tags` filter
   plus an optional `query` — so Ask HN, Show HN, and phrase-targeted pain/
   build searches take priority over raw comment volume. (Falls back to a
   plain `tags` list when no `searches` are configured.)
2. Cap-busting windowing. `search_by_date` will not paginate past ~1000
   results (nbPages tops out), so within a window we page until the cap, then
   move the upper bound down to the oldest timestamp seen and query again,
   walking the whole window newest→oldest in ~1000-row steps.

Watermark handling: the caller passes the watermark as `since_ts`; this adapter
subtracts the configured overlap (default 6h, §5.1) to form the lower bound.
`until_ts` is the optional upper bound (open to newest when None).

The watermark itself is advanced only after the judge stage succeeds, never
here (§5.1) — this adapter never writes it.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx

from .base import FetchedItem
from .json_api import JsonApiAdapter

# Algolia stops paginating around this many results per query.
_ALGOLIA_RESULT_CAP = 1000


class HackerNewsAdapter(JsonApiAdapter):
    def __init__(self, source: dict[str, Any], client: httpx.Client) -> None:
        super().__init__(source, client)
        self.overlap_seconds: int = int(self.config.get("overlap_hours", 6) * 3600)
        # A search is {tags?, query?}. Prefer the configured list; otherwise
        # fall back to the older `tags`-only behaviour for compatibility.
        searches = self.config.get("searches")
        if searches is None:
            searches = [{"tags": t} for t in self.config.get("tags", ["comment", "story"])]
        self.searches: list[dict[str, str]] = searches

    def iter_items(
        self, since_ts: int, until_ts: int | None = None
    ) -> Iterator[list[FetchedItem]]:
        lower = since_ts - self.overlap_seconds
        for search in self.searches:
            yield from self._fetch_search(search, lower, until_ts)

    def _fetch_search(
        self, search: dict[str, str], lower: int, until: int | None
    ) -> Iterator[list[FetchedItem]]:
        """Walk one search's results in [lower, until) newest→oldest."""
        upper = until
        while True:
            window_min, fetched, nb_hits = yield from self._page_window(
                search, lower, upper
            )
            if fetched == 0 or nb_hits is None:
                return                                  # window empty; search done
            if fetched >= nb_hits:
                return                                  # covered the whole window
            if window_min is None or window_min <= lower:
                return                                  # reached the lower bound
            # Algolia's cap was hit and more remain below window_min: move the
            # ceiling down. +1 so boundary-second items are re-queried (the
            # unique constraint makes the duplicates free) rather than skipped.
            next_upper = window_min + 1
            if upper is not None and next_upper >= upper:
                return                                  # no progress; stop
            upper = next_upper

    def _page_window(
        self, search: dict[str, str], lower: int, upper: int | None
    ) -> Iterator[list[FetchedItem]]:
        """Page a single [lower, upper) window until Algolia stops.

        Yields item pages; returns (min_ts_seen, hits_fetched, nb_hits).
        """
        numeric = [f"{self.timestamp_field}>={lower}"]
        if upper is not None:
            numeric.append(f"{self.timestamp_field}<{upper}")
        numeric_filters = ",".join(numeric)

        page = 0
        fetched = 0
        min_ts: int | None = None
        nb_hits: int | None = None

        while True:
            params: dict[str, Any] = {
                "numericFilters": numeric_filters,
                self.hits_per_page_param: self.hits_per_page,
                self.page_param: page,
            }
            if search.get("tags"):
                params["tags"] = search["tags"]
            if search.get("query"):
                params["query"] = search["query"]

            data = self._get(params)
            hits = data.get(self.hits_path, []) or []
            if not hits:
                break

            items = self._page_to_items(hits)
            if items:
                yield items

            fetched += len(hits)
            nb_hits = int(data.get("nbHits", fetched))
            page_min = min(int(h[self.timestamp_field]) for h in hits)
            min_ts = page_min if min_ts is None else min(min_ts, page_min)

            nb_pages = int(data.get(self.pages_path, 1) or 1)
            page += 1
            if page >= nb_pages or page * self.hits_per_page >= _ALGOLIA_RESULT_CAP:
                break

        return min_ts, fetched, nb_hits
