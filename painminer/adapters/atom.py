"""Atom/RSS feed adapter (§3.1), config-driven like json_api.

One engine for both Atom and RSS 2.0 feeds. `feedparser` is not installed in
this project's venv, so this adapter parses with stdlib
`xml.etree.ElementTree`, matching elements by local name so it doesn't care
whether a feed declares the Atom namespace, no namespace at all (RSS), or
extra vendor namespaces (e.g. arXiv's `arxiv:` primary_category).

Each entry/item is normalized into a small field dict with common keys
(id, title, summary, link, published) regardless of which format it came
from, then mapped to a FetchedItem the same way for both — so `text_fields`,
`id_field`, and `url_field` in `config_json` name normalized keys, not raw
XML tags.

Relevant `config_json` keys:

    feed_url          str    single feed URL
    feed_urls         list   multiple feed URLs (checked before feed_url)
    base_url          str    combined with `endpoint` when no feed_url(s) given
    endpoint          str    path appended to base_url
    id_field          str    normalized key used as source_id (default "id",
                              falling back to "link" when empty)
    url_field         str    normalized key used as url (default "link")
    text_fields       list   normalized keys joined (in order) for raw_text
                              (default ["title", "summary"])
    overlap_hours     float  re-fetch window below since_ts (default 6)
    clean_html        bool   strip HTML from joined text fields (default True)
    timeout_seconds   float  per-request timeout (default 20)

A single malformed entry (missing/unparseable timestamp, broken XML for one
item, etc.) is skipped, never raises. A feed that fails to fetch or fails to
parse as XML at all propagates the underlying error, same as json_api's _get.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterator
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from .base import Adapter, FetchedItem, clean_html, content_hash


def _local(tag: str) -> str:
    """Strip a `{namespace}` prefix off an ElementTree tag name."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _child(elem: ET.Element, name: str) -> ET.Element | None:
    for c in elem:
        if _local(c.tag) == name:
            return c
    return None


def _children(elem: ET.Element | None, name: str) -> list[ET.Element]:
    if elem is None:
        return []
    return [c for c in elem if _local(c.tag) == name]


def _text_of(elem: ET.Element | None) -> str | None:
    if elem is None or elem.text is None:
        return None
    text = elem.text.strip()
    return text or None


def _atom_link(entry: ET.Element) -> str | None:
    """Atom links are `<link href="..." rel="...">` (no text content)."""
    links = _children(entry, "link")
    if not links:
        return None
    for link in links:
        if link.get("rel") in (None, "alternate") and link.get("href"):
            return link.get("href")
    for link in links:
        if link.get("href"):
            return link.get("href")
    return None


def _rss_link(item: ET.Element) -> str | None:
    link = _child(item, "link")
    if link is None:
        return None
    text = _text_of(link)
    return text or link.get("href")


def _parse_iso_ts(value: str) -> int | None:
    try:
        text = value.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except (ValueError, TypeError):
        return None


def _parse_rfc822_ts(value: str) -> int | None:
    try:
        dt = parsedate_to_datetime(value.strip())
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except (ValueError, TypeError):
        return None


def _atom_fields(entry: ET.Element) -> dict[str, Any]:
    summary = _text_of(_child(entry, "summary")) or _text_of(_child(entry, "content"))
    published = _text_of(_child(entry, "published")) or _text_of(_child(entry, "updated"))
    ts = _parse_iso_ts(published) if published else None

    authors = [
        _text_of(_child(a, "name")) for a in _children(entry, "author")
    ]
    authors = [a for a in authors if a]
    categories = [c.get("term") for c in _children(entry, "category") if c.get("term")]

    return {
        "id": _text_of(_child(entry, "id")),
        "title": _text_of(_child(entry, "title")),
        "summary": summary,
        "link": _atom_link(entry),
        "created_at_i": ts,
        "authors": authors or None,
        "categories": categories or None,
    }


def _rss_fields(item: ET.Element) -> dict[str, Any]:
    pub_date = _text_of(_child(item, "pubDate"))
    ts = _parse_rfc822_ts(pub_date) if pub_date else None
    categories = [
        _text_of(c) for c in _children(item, "category") if _text_of(c)
    ]

    return {
        "id": _text_of(_child(item, "guid")),
        "title": _text_of(_child(item, "title")),
        "summary": _text_of(_child(item, "description")),
        "link": _rss_link(item),
        "created_at_i": ts,
        "authors": None,
        "categories": categories or None,
    }


class AtomAdapter(Adapter):
    def __init__(self, source: dict[str, Any], client: httpx.Client) -> None:
        super().__init__(source, client)
        cfg = self.config

        urls: list[str] = list(cfg.get("feed_urls") or [])
        if not urls and cfg.get("feed_url"):
            urls = [cfg["feed_url"]]
        if not urls and cfg.get("base_url"):
            base = cfg["base_url"].rstrip("/")
            endpoint = cfg.get("endpoint", "")
            urls = [f"{base}/{endpoint}" if endpoint else base]
        self.feed_urls: list[str] = urls

        self.id_field: str = cfg.get("id_field", "id")
        self.url_field: str = cfg.get("url_field", "link")
        self.text_fields: list[str] = cfg.get("text_fields") or ["title", "summary"]
        self.overlap_seconds: int = int(float(cfg.get("overlap_hours", 6)) * 3600)
        self.clean_html: bool = bool(cfg.get("clean_html", True))
        self.timeout_seconds: float = float(cfg.get("timeout_seconds", 20))
        self.metadata_fields: list[str] = cfg.get("metadata_fields") or [
            "authors",
            "categories",
        ]

    # --- HTTP ---------------------------------------------------------

    def _get(self, url: str) -> bytes:
        resp = self.client.get(url, timeout=self.timeout_seconds)
        resp.raise_for_status()
        content = getattr(resp, "content", None)
        if content:
            return content
        return resp.text.encode("utf-8")

    # --- parsing --------------------------------------------------------

    def _entries(self, raw: bytes) -> tuple[list[ET.Element], Any]:
        """Return (entry elements, per-format field extractor) for one feed."""
        root = ET.fromstring(raw)
        root_local = _local(root.tag)
        if root_local == "rss":
            channel = _child(root, "channel")
            return _children(channel, "item"), _rss_fields
        if root_local in ("feed", "RDF"):
            # `feed` is Atom; bare `RDF` (RSS 1.0) is out of scope but we
            # still try atom-shaped entries rather than raising.
            return _children(root, "entry"), _atom_fields
        return [], _atom_fields

    def _text(self, fields: dict[str, Any]) -> str | None:
        parts = []
        for key in self.text_fields:
            value = fields.get(key)
            if not value:
                continue
            value = str(value)
            if self.clean_html:
                value = clean_html(value)
            value = value.strip()
            if value:
                parts.append(value)
        text = "\n\n".join(parts)
        return text or None

    def _make_item(self, fields: dict[str, Any]) -> FetchedItem | None:
        text = self._text(fields)
        if not text:
            return None

        created_at_i = fields.get("created_at_i")
        if created_at_i is None:
            return None

        source_id = fields.get(self.id_field) or fields.get("id") or fields.get("link")
        if not source_id:
            return None

        url = fields.get(self.url_field) or fields.get("link")

        metadata = {
            f: fields[f] for f in self.metadata_fields if fields.get(f)
        }

        return FetchedItem(
            source=self.name,
            source_id=str(source_id),
            url=url,
            raw_text=text,
            content_hash=content_hash(text),
            created_at_i=int(created_at_i),
            thread_id=None,
            metadata=metadata or None,
        )

    def _page(self, raw: bytes, lower: int, until_ts: int | None) -> list[FetchedItem]:
        entries, extractor = self._entries(raw)
        items: list[FetchedItem] = []
        for entry in entries:
            try:
                fields = extractor(entry)
                item = self._make_item(fields)
            except Exception:
                # A single malformed entry never kills the whole feed.
                continue
            if item is None:
                continue
            if item.created_at_i < lower:
                continue
            if until_ts is not None and item.created_at_i >= until_ts:
                continue
            items.append(item)
        return items

    # --- Adapter interface ------------------------------------------------

    def iter_items(
        self, since_ts: int, until_ts: int | None = None
    ) -> Iterator[list[FetchedItem]]:
        lower = since_ts - self.overlap_seconds
        for url in self.feed_urls:
            raw = self._get(url)
            page = self._page(raw, lower, until_ts)
            if page:
                yield page
