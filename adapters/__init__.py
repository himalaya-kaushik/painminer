"""Adapter registry (§3.1).

Adapters are resolved DYNAMICALLY by name: a source's `config_json.adapter_impl`
(falling back to its `adapter` column) names a module `adapters.<impl>`, and the
Adapter subclass defined there is used. So adding a new source is exactly one
registry row plus (only if the generic json_api engine doesn't fit) one adapter
file — nothing else here changes (§13 step 8).
"""

from __future__ import annotations

import importlib
import inspect
import re
from typing import Any

import httpx

from .base import Adapter, FetchedItem, content_hash
from .json_api import JsonApiAdapter

_SAFE_NAME = re.compile(r"^[a-z0-9_]+$")


def get_adapter(source: dict[str, Any], client: httpx.Client) -> Adapter:
    """Instantiate the adapter for a `sources` registry row."""
    impl = (source.get("config_json") or {}).get("adapter_impl") or source["adapter"]
    if not _SAFE_NAME.match(impl):
        raise ValueError(f"invalid adapter_impl {impl!r}")
    try:
        module = importlib.import_module(f"adapters.{impl}")
    except ModuleNotFoundError as exc:
        raise ValueError(
            f"no adapter module 'adapters.{impl}' for source {source.get('name')!r}"
        ) from exc

    for _, obj in inspect.getmembers(module, inspect.isclass):
        if issubclass(obj, Adapter) and obj is not Adapter and obj.__module__ == module.__name__:
            return obj(source, client)
    raise ValueError(f"adapters.{impl} defines no Adapter subclass")


__all__ = ["Adapter", "FetchedItem", "content_hash", "get_adapter", "JsonApiAdapter"]
