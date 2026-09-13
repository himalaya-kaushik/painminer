"""Hacker News adapter over the Algolia API (§3.2, §5.1).

Uses the same generic JSON-API engine (JsonApiAdapter) for HTTP and item
mapping, and overrides `iter_items` for two things Algolia forces on us:

1. Two passes — `tags=comment` and `tags=story` — so we get both discussion
   text and submissions.
2. Cap-busting windowing. `search_by_date` will not paginate past ~1000
   results (nbPages tops out), so within a single time window we page until
   the cap, then move the upper bound down to the oldest timestamp we saw and
   query again. This walks the whole window newest→oldest in 1000-row steps.

Watermark handling: the caller passes the watermark as `since_ts`; this
adapter subtracts the configured overlap (default 6h, §5.1) to form the lower
bound. `until_ts` is the optional upper bound (open to newest when None).

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
        self.tags: list[str] = self.config.get("tags", ["comment", "story"])
        self.overlap_seconds: int = int(self.config.get("overlap_hours", 6) * 3600)

    def iter_items(
        self, since_ts: int, until_ts: int | None = None
    ) -> Iterator[list[FetchedItem]]:
        lower = since_ts - self.overlap_seconds
        for tag in self.tags:
            yield from self._fetch_tag(tag, lower, until_ts)

    def _fetch_tag(
        self, tag: str, lower: int, until: int | None
    ) -> Iterator[list[FetchedItem]]:
        """Walk one tag's results in [lower, until) newest→oldest."""
        upper = until
        while True:
            window_min, fetched, nb_hits = yield from self._page_window(
                tag, lower, upper
            )
            if fetched == 0 or nb_hits is None:
                return                                  # window empty; tag done
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
        self, tag: str, lower: int, upper: int | None
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
            params = {
                "tags": tag,
                "numericFilters": numeric_filters,
                self.hits_per_page_param: self.hits_per_page,
                self.page_param: page,
            }
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
