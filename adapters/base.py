"""Adapter base class and the item shape every adapter yields.

An adapter turns a registry row (a `sources` record) into a stream of pages of
fetched items. It only fetches and normalizes; structural dedupe, the queue,
and everything downstream live elsewhere. Pages are yielded so the caller can
write one page and drop it before fetching the next — Machine A is 8GB (§4.3).
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any


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
