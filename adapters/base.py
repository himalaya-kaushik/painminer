"""Adapter base class and the item shape every adapter yields.

An adapter turns a registry row (a `sources` record) into a stream of pages of
fetched items. It only fetches and normalizes; structural dedupe, the queue,
and everything downstream live elsewhere. Pages are yielded so the caller can
write one page and drop it before fetching the next — Machine A is 8GB (§4.3).
"""

from __future__ import annotations

import hashlib
import html
import re
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

_BLOCK_TAG = re.compile(r"(?i)<\s*/?\s*(p|br|div|li|blockquote)\s*/?>")
_ANY_TAG = re.compile(r"<[^>]+>")
_MULTI_NEWLINE = re.compile(r"\n{3,}")


def clean_html(text: str) -> str:
    """Turn source HTML (e.g. HN comment_text) into plain text.

    Block tags become newlines, other tags are stripped, and HTML entities are
    decoded — so the model reads real text, not `&#x27;`/`<p>` noise, and its
    verbatim evidence_quote can actually match the source (§7.2). This is
    normalization, not filtering.
    """
    t = _BLOCK_TAG.sub("\n", text)
    t = _ANY_TAG.sub("", t)
    t = html.unescape(t)
    t = _MULTI_NEWLINE.sub("\n\n", t)
    return t.strip()


@dataclass(frozen=True)
class FetchedItem:
    """One normalized item, ready to be written to `items` with state=fetched."""

    source: str
    source_id: str          # the source's own id (unique per source)
    url: str | None
    raw_text: str
    content_hash: str
    created_at_i: int        # source timestamp, epoch seconds (for windowing)


def content_hash(text: str) -> str:
    """Stable hash of normalized text, for cross-source structural dedupe (§4.1)."""
    normalized = " ".join(text.lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class Adapter(ABC):
    """Base class for all source adapters."""

    def __init__(self, source: dict[str, Any], client: Any) -> None:
        self.source = source
        self.name: str = source["name"]
        self.config: dict[str, Any] = source.get("config_json") or {}
        self.client = client

    @abstractmethod
    def iter_items(
        self, since_ts: int, until_ts: int | None = None
    ) -> Iterator[list[FetchedItem]]:
        """Yield pages of items with source timestamp in [since_ts, until_ts).

        `since_ts` is the lower bound the caller wants covered (the watermark);
        the adapter applies its own overlap below it (§5.1). `until_ts` is an
        optional upper bound (open — newest — when None); it supports bounded
        backfill chunks and deterministic tests.
        """
        raise NotImplementedError
