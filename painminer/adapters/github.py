"""GitHub adapter over the Issues Search API (§3.2, §5.1).

Fetches issues matching configured `queries` (unmet-need / demand signals like
wontfix and feature requests), newest-updated first, bounded by the watermark
via a server-side `updated:>=DATE` qualifier. Reuses the JsonApiAdapter engine
for HTTP; overrides mapping (title+body, 👍 reactions as engagement) and
iteration (GitHub's page/per_page + its 1000-result search cap).

Timestamp is `updated_at` — an issue that gained reactions/comments this week
resurfaces (§5.1). Unauthenticated search is rate-limited (~10/min), so a small
throttle sits between requests.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

import httpx

from .base import FetchedItem, content_hash
from .json_api import JsonApiAdapter, _to_epoch

_GITHUB_SEARCH_CAP_PAGES = 10   # 10 * per_page(100) = 1000, GitHub's search ceiling


class GithubAdapter(JsonApiAdapter):
    def __init__(self, source: dict[str, Any], client: httpx.Client) -> None:
        super().__init__(source, client)
        self.queries: list[str] = self.config.get("queries", ["label:wontfix"])
        self.overlap_seconds: int = int(self.config.get("overlap_hours", 0) * 3600)
        self.throttle_seconds: float = float(self.config.get("throttle_seconds", 2))

    def _make_item(self, hit: dict[str, Any]) -> FetchedItem | None:
        title = (hit.get("title") or "").strip()
        body = (hit.get("body") or "").strip()
        text = f"{title}\n\n{body}".strip()
        if not text or hit.get("id") is None:
            return None
        created_at_i = _to_epoch(hit.get("updated_at"), True)
        if created_at_i is None:
            return None
        source_id = str(hit["id"])
        reactions = hit.get("reactions") or {}
        metadata = {"points": reactions.get("+1"), "num_comments": hit.get("comments")}
        metadata = {k: v for k, v in metadata.items() if v is not None}
        return FetchedItem(
            source=self.name,
            source_id=source_id,
            url=hit.get("html_url"),
            raw_text=text,
            content_hash=content_hash(text),
            created_at_i=created_at_i,
            thread_id=source_id,               # each issue is its own thread
            metadata=metadata or None,
        )

    def iter_items(
        self, since_ts: int, until_ts: int | None = None
    ) -> Iterator[list[FetchedItem]]:
        lower = since_ts - self.overlap_seconds
        since_date = datetime.fromtimestamp(lower, tz=timezone.utc).strftime("%Y-%m-%d")
        for q in self.queries:
            yield from self._search(q, lower, until_ts, since_date)

    def _search(
        self, query: str, lower: int, until: int | None, since_date: str
    ) -> Iterator[list[FetchedItem]]:
        full_q = f"{query} updated:>={since_date}"
        page = 1
        while page <= _GITHUB_SEARCH_CAP_PAGES:
            params = {
                "q": full_q,
                "sort": "updated",
                "order": "desc",
                self.hits_per_page_param: self.hits_per_page,
                self.page_param: page,
            }
            data = self._get(params)
            raw = data.get("items", []) or []
            if not raw:
                return
            items = self._page_to_items(raw)
            in_window = [
                it for it in items
                if it.created_at_i >= lower and (until is None or it.created_at_i < until)
            ]
            if in_window:
                yield in_window
            if len(raw) < self.hits_per_page:
                return
            if items and min(it.created_at_i for it in items) < lower:
                return                         # sorted desc: past the window
            page += 1
            if self.throttle_seconds:
                time.sleep(self.throttle_seconds)
