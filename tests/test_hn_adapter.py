"""Hacker News adapter — cap-busting windowing and mapping, fully offline.

A FakeAlgolia stands in for the HTTP client and faithfully mimics
search_by_date: numericFilters windowing, descending order, page/hitsPerPage
paging, and — crucially — a hard result cap (like Algolia's ~1000) so the
adapter is forced to walk the window in chunks. Proves the adapter recovers
every item with only free boundary re-fetches, and never loops.
"""

import math

from adapters.hackernews import HackerNewsAdapter

CAP_RESULTS = 100          # fake pagination ceiling (real Algolia ~1000)
HPP = 25                   # small page size so the cap bites quickly


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeAlgolia:
    """Serves search_by_date semantics over an in-memory dataset."""

    def __init__(self, by_tag):
        self.by_tag = by_tag           # {"comment": [...hits...], "story": [...]}
        self.calls = 0

    @staticmethod
    def _bounds(numeric_filters):
        lower, upper = None, None
        for cond in numeric_filters.split(","):
            if ">=" in cond:
                lower = int(cond.split(">=")[1])
            elif "<" in cond:
                upper = int(cond.split("<")[1])
        return lower, upper

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        params = params or {}
        data = self.by_tag.get(params["tags"], [])
        lower, upper = self._bounds(params.get("numericFilters", ""))
        rows = [
            h for h in data
            if (lower is None or h["created_at_i"] >= lower)
            and (upper is None or h["created_at_i"] < upper)
        ]
        rows.sort(key=lambda h: h["created_at_i"], reverse=True)
        hpp = int(params["hitsPerPage"])
        page = int(params["page"])
        nb_hits = len(rows)
        nb_pages = min(math.ceil(nb_hits / hpp), CAP_RESULTS // hpp) if nb_hits else 0
        page_rows = rows[page * hpp:(page + 1) * hpp]
        return FakeResponse(
            {"hits": page_rows, "nbHits": nb_hits, "nbPages": nb_pages,
             "page": page, "hitsPerPage": hpp}
        )


def _dataset():
    comments = [
        {"objectID": f"c{i}", "created_at_i": 1000 + i,
         "comment_text": f"comment {i} with &#x27;quote&#x27;<p>and a break"}
        for i in range(230)                       # 1000..1229
    ]
    stories = [
        {"objectID": f"s{i}", "created_at_i": 1000 + i, "title": f"Story number {i}"}
        for i in range(30)                        # 1000..1029
    ]
    return {"comment": comments, "story": stories}


def _adapter(client):
    source = {
        "name": "hackernews",
        "adapter": "json_api",
        "config_json": {
            "adapter_impl": "hackernews",
            "base_url": "https://hn.algolia.com/api/v1",
            "endpoint": "search_by_date",
            "tags": ["comment", "story"],
            "hits_per_page": HPP,
            "overlap_hours": 0,
            "clean_html": True,
            "id_field": "objectID",
            "timestamp_field": "created_at_i",
            "text_fields": ["comment_text", "story_text", "title"],
            "url_template": "https://news.ycombinator.com/item?id={objectID}",
        },
    }
    return HackerNewsAdapter(source, client)


def _collect(since=1000, until=1230):
    client = FakeAlgolia(_dataset())
    adapter = _adapter(client)
    items = [it for page in adapter.iter_items(since, until) for it in page]
    return items, client


def test_recovers_every_item_past_the_cap():
    items, _ = _collect()
    ids = [it.source_id for it in items]
    unique = set(ids)
    assert len(unique) == 260, f"expected 260 unique, got {len(unique)}"


def test_only_boundary_refetches_are_duplicated():
    items, _ = _collect()
    ids = [it.source_id for it in items]
    dups = len(ids) - len(set(ids))
    # One free re-fetch per window rewind (design: boundary +1). Small, bounded.
    assert dups <= 4, f"too many duplicate re-fetches: {dups}"


def test_both_tags_fetched():
    items, _ = _collect()
    assert any(it.source_id.startswith("c") for it in items)
    assert any(it.source_id.startswith("s") for it in items)


def test_clean_html_applied_to_text():
    items, _ = _collect()
    sample = next(it for it in items if it.source_id == "c5")
    assert "&#x27;" not in sample.raw_text and "<p>" not in sample.raw_text
    assert "'quote'" in sample.raw_text


def test_mapping_url_and_hash():
    items, _ = _collect()
    sample = next(it for it in items if it.source_id == "c5")
    assert sample.url == "https://news.ycombinator.com/item?id=c5"
    assert sample.content_hash and len(sample.content_hash) == 64


def test_terminates_and_is_not_pathological():
    _, client = _collect()
    # Windowing must be efficient, not a per-item crawl.
    assert client.calls < 40, f"too many requests: {client.calls}"


def test_respects_lower_bound():
    # since=1200 -> only comments 1200..1229; stories (1000..1029) fall below.
    items, _ = _collect(since=1200, until=1230)
    assert items and all(int(it.created_at_i) >= 1200 for it in items)
