"""Thread-context fetching for pass 2 (v3 brief §3, step 2).

A comment read alone is why v2 produced meaningless findings; the same comment
read inside its thread is a different object. Before the deep read, we pull the
surrounding discussion so pass 2 sees the item in context.

Best-effort by contract: any failure returns None and the deep read proceeds
without context. Currently implemented for Hacker News (the Algolia item
endpoint returns the whole story tree); other sources return None until their
own context shape is wired in. GitHub issues already carry their body in
`raw_text`, so they need no extra fetch.
"""

from __future__ import annotations

from typing import Any

import httpx

from painminer.adapters.base import clean_html

_HN_ITEM_URL = "https://hn.algolia.com/api/v1/items/{id}"
_MAX_COMMENTS = 40      # cap the tree walk regardless of char budget


def fetch_thread_context(
    item: dict[str, Any], client: httpx.Client, *, max_chars: int = 6000
) -> str | None:
    """Return the item's surrounding thread as plain text, or None.

    Never raises: a fetch/parse failure just means the deep read runs on the
    item alone.
    """
    source = item.get("source")
    if source == "hackernews":
        return _hackernews_context(item, client, max_chars)
    return None


def _hackernews_context(
    item: dict[str, Any], client: httpx.Client, max_chars: int
) -> str | None:
    story_id = item.get("thread_id") or item.get("source_id")
    if not story_id:
        return None
    try:
        resp = client.get(_HN_ITEM_URL.format(id=story_id), timeout=20)
        resp.raise_for_status()
        root = resp.json()
    except Exception:
        return None
    if not isinstance(root, dict):
        return None

    parts: list[str] = []
    title = (root.get("title") or "").strip()
    if title:
        parts.append(f"Story: {title}")
    story_text = _clean(root.get("text"))
    if story_text:
        parts.append(story_text)

    # Breadth-first walk of the comment tree (newest stories nest replies).
    comments: list[str] = []
    queue = list(root.get("children") or [])
    while queue and len(comments) < _MAX_COMMENTS:
        node = queue.pop(0)
        if not isinstance(node, dict):
            continue
        text = _clean(node.get("text"))
        if text:
            author = (node.get("author") or "someone").strip()
            comments.append(f"- {author}: {text}")
        queue.extend(node.get("children") or [])

    if comments:
        parts.append("Comments:")
        parts.extend(comments)

    if not parts:
        return None
    context = "\n\n".join(parts)
    if len(context) > max_chars:
        context = context[:max_chars].rstrip() + "\n…[thread truncated]"
    return context


def _clean(text: Any) -> str:
    if not text or not isinstance(text, str):
        return ""
    return clean_html(text).strip()
