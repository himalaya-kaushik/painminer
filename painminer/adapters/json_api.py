"""Generic JSON-API adapter engine (§3.1).

One fetcher for the whole `json_api` adapter type, driven by the registry
row's `config_json` — not a hand-written fetcher per site. It provides:

- an HTTP GET helper against a configured base URL,
- config-driven mapping from a raw hit to a FetchedItem,
- a default offset-paginated `iter_items` for simple sources.

Sources whose pagination or query building is special (e.g. Hacker News,
which must window past Algolia's 1000-result cap) subclass this and override
`iter_items`, reusing `_get` and `_make_item`.

Relevant `config_json` keys:

    base_url          str    e.g. "https://hn.algolia.com/api/v1"
    endpoint          str    path appended to base_url, e.g. "search_by_date"
    params            dict   static query params sent on every request
    hits_per_page     int    page size (default 100)
    hits_per_page_param str  param name for page size (default "hitsPerPage")
    page_param        str    param name for the page index (default "page")
    hits_path         str    key holding the array of hits (default "hits")
    pages_path        str    key holding the page count (default "nbPages")
    id_field          str    hit key used as source_id (default "objectID")
    timestamp_field   str    hit key holding epoch seconds (default "created_at_i")
    text_fields       list   hit keys tried in order for raw_text
    url_template      str    str.format template over the hit, for url
    timeout_seconds   float  per-request timeout (default 20)
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

import httpx

from .base import Adapter, FetchedItem, clean_html, content_hash


def _to_epoch(value: Any, is_iso: bool) -> int | None:
    """Coerce a timestamp field to epoch seconds. Handles ints and ISO-8601."""
    if value is None:
        return None
    if not is_iso:
        return int(value)
    text = str(value).replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


class JsonApiAdapter(Adapter):
    def __init__(self, source: dict[str, Any], client: httpx.Client) -> None:
        super().__init__(source, client)
        cfg = self.config
        self.base_url: str = cfg["base_url"].rstrip("/")
        self.endpoint: str = cfg.get("endpoint", "")
        self.static_params: dict[str, Any] = dict(cfg.get("params", {}))
        self.hits_per_page: int = int(cfg.get("hits_per_page", 100))
        self.hits_per_page_param: str = cfg.get("hits_per_page_param", "hitsPerPage")
        self.page_param: str = cfg.get("page_param", "page")
        self.hits_path: str = cfg.get("hits_path", "hits")
        self.pages_path: str = cfg.get("pages_path", "nbPages")
        self.id_field: str = cfg.get("id_field", "objectID")
        self.timestamp_field: str = cfg.get("timestamp_field", "created_at_i")
        self.thread_id_field: str | None = cfg.get("thread_id_field")
        self.metadata_fields: list[str] = cfg.get("metadata_fields", [])
        self.text_fields: list[str] = cfg.get("text_fields", [])
        self.url_template: str | None = cfg.get("url_template")
        self.timeout_seconds: float = float(cfg.get("timeout_seconds", 20))
        self.clean_html: bool = bool(cfg.get("clean_html", False))
        self.timestamp_is_iso: bool = bool(cfg.get("timestamp_is_iso", False))
        self.more_path: str | None = cfg.get("more_path")   # e.g. "has_more"
        self.max_pages: int = int(cfg.get("max_pages", 20))
        self.date_sorted: bool = bool(cfg.get("date_sorted", False))
        self.page_start: int = int(cfg.get("page_start", 0))   # 1 for some APIs

    # --- HTTP -------------------------------------------------------------

    def _get(self, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/{self.endpoint}" if self.endpoint else self.base_url
        resp = self.client.get(url, params=params, timeout=self.timeout_seconds)
        resp.raise_for_status()
        return resp.json()

    # --- mapping ----------------------------------------------------------

    def _text(self, hit: dict[str, Any]) -> str | None:
        for field in self.text_fields:
            value = hit.get(field)
            if value and str(value).strip():
                text = clean_html(str(value)) if self.clean_html else str(value).strip()
                if text:
                    return text
        return None

    def _make_item(self, hit: dict[str, Any]) -> FetchedItem | None:
        text = self._text(hit)
        if text is None:
            return None
        try:
            source_id = str(hit[self.id_field])
            created_at_i = _to_epoch(hit[self.timestamp_field], self.timestamp_is_iso)
        except (KeyError, TypeError, ValueError):
            return None
        if created_at_i is None:
            return None

        url: str | None
        if self.url_template:
            try:
                url = self.url_template.format(**hit)
            except (KeyError, IndexError):
                url = None
        else:
            url = hit.get("url")

        thread_id: str | None = None
        if self.thread_id_field:
            raw_thread = hit.get(self.thread_id_field)
            if raw_thread is not None:
                thread_id = str(raw_thread)

        metadata = {
            f: hit[f] for f in self.metadata_fields if hit.get(f) is not None
        }

        return FetchedItem(
            source=self.name,
            source_id=source_id,
            url=url,
            raw_text=text,
            content_hash=content_hash(text),
            created_at_i=created_at_i,
            thread_id=thread_id,
            metadata=metadata or None,
        )

    def _page_to_items(self, hits: list[dict[str, Any]]) -> list[FetchedItem]:
        items = [self._make_item(h) for h in hits]
        return [it for it in items if it is not None]

    # --- default pagination ----------------------------------------------

    def _hits(self, data: Any) -> list[dict[str, Any]]:
        """Extract the hit list — the response itself when hits_path is empty."""
        if not self.hits_path:
            return data if isinstance(data, list) else []
        return data.get(self.hits_path, []) or []

    def iter_items(
        self, since_ts: int, until_ts: int | None = None
    ) -> Iterator[list[FetchedItem]]:
        """Offset pagination with an in-process [since, until) window.

        Handles object- and array-root responses, int or ISO timestamps, and
        three stop conditions: `more_path` (e.g. has_more), `pages_path` (page
        count), or single-page. `date_sorted` sources stop once a whole page
        falls below the window. Subclasses with special windowing (HN, GitHub)
        override this.
        """
        page = self.page_start
        for fetched_pages in range(1, self.max_pages + 1):
            params = dict(self.static_params)
            if self.hits_per_page_param:
                params[self.hits_per_page_param] = self.hits_per_page
            if self.page_param:
                params[self.page_param] = page
            data = self._get(params)
            hits = self._hits(data)
            if not hits:
                return

            items = self._page_to_items(hits)
            in_window = [
                it for it in items
                if it.created_at_i >= since_ts
                and (until_ts is None or it.created_at_i < until_ts)
            ]
            if in_window:
                yield in_window

            # date-sorted source that has dropped entirely below the window: done
            if self.date_sorted and items and all(it.created_at_i < since_ts for it in items):
                return

            # stop conditions
            if self.more_path is not None:
                if not (isinstance(data, dict) and data.get(self.more_path)):
                    return
            elif self.pages_path and isinstance(data, dict):
                if fetched_pages >= int(data.get(self.pages_path, 1) or 1):
                    return
            else:
                return   # single-page source (e.g. a bare array endpoint)
            page += 1
