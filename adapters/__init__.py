"""Adapter registry.

Maps a source's `adapter` type to an adapter class. Adding a source of an
existing type is a registry row; adding a new *kind* of source is one entry
here plus one adapter file (§3.1, §13 step 8).
"""

from __future__ import annotations

from typing import Any

import httpx

from .base import Adapter, FetchedItem, content_hash
from .hackernews import HackerNewsAdapter
from .json_api import JsonApiAdapter

# Adapter type -> class. `json_api` is the generic engine; specific sources
# that need custom querying register their own class here.
_BY_TYPE: dict[str, type[Adapter]] = {
    "json_api": JsonApiAdapter,
    "hackernews": HackerNewsAdapter,
}


def get_adapter(source: dict[str, Any], client: httpx.Client) -> Adapter:
    """Instantiate the adapter for a `sources` registry row."""
    adapter_type = source.get("config_json", {}).get("adapter_impl") or source["adapter"]
    try:
        cls = _BY_TYPE[adapter_type]
    except KeyError as exc:
        raise ValueError(
            f"no adapter registered for {adapter_type!r} "
            f"(source {source.get('name')!r})"
        ) from exc
    return cls(source, client)


__all__ = [
    "Adapter",
    "FetchedItem",
    "content_hash",
    "get_adapter",
    "HackerNewsAdapter",
    "JsonApiAdapter",
]
