"""Hacker News adapter — cap-busting windowing, targeted searches, mapping.

A FakeAlgolia stands in for the HTTP client and faithfully mimics
search_by_date: numericFilters windowing, descending order, page/hitsPerPage
paging, an optional `query` substring match, and — crucially — a hard result
cap (like Algolia's ~1000) so the adapter is forced to walk the window in
chunks. Fully offline.
"""

import math

from painminer.adapters.hackernews import HackerNewsAdapter

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
        self.by_tag = by_tag           # {"comment": [...hits...], ...}
        self.calls = 0
        self.seen_params = []          # every params dict received

    @staticmethod
    def _bounds(numeric_filters):
        lower, upper = None, None
        for cond in numeric_filters.split(","):
            if ">=" in cond:
                lower = int(cond.split(">=")[1])
            elif "<" in cond:
                upper = int(cond.split("<")[1])
        return lower, upper

    @staticmethod
    def _text(hit):
        return str(hit.get("comment_text") or hit.get("title") or "")

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        params = params or {}
        self.seen_params.append(params)
        data = self.by_tag.get(params.get("tags"), [])
        lower, upper = self._bounds(params.get("numericFilters", ""))
        q = (params.get("query") or "").lower()
        rows = [
            h for h in data
            if (lower is None or h["created_at_i"] >= lower)
            and (upper is None or h["created_at_i"] < upper)
            and (not q or q in self._text(h).lower())
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
         "story_id": 9000 + i // 50,               # 50 comments per thread
         "comment_text": f"comment {i} with &#x27;quote&#x27;<p>and a break"}
        for i in range(230)                        # 1000..1229
    ]
    stories = [
        {"objectID": f"s{i}", "created_at_i": 1000 + i, "story_id": f"s{i}",
         "title": f"Story number {i}"}
        for i in range(30)                         # 1000..1029
    ]
    return {"comment": comments, "story": stories}


def _adapter(client, config_over=None):
    config = {
        "adapter_impl": "hackernews",
        "base_url": "https://hn.algolia.com/api/v1",
        "endpoint": "search_by_date",
        "tags": ["comment", "story"],              # backward-compat default
        "hits_per_page": HPP,
        "overlap_hours": 0,
        "clean_html": True,
        "id_field": "objectID",
        "timestamp_field": "created_at_i",
        "thread_id_field": "story_id",
        "text_fields": ["comment_text", "story_text", "title"],
        "url_template": "https://news.ycombinator.com/item?id={objectID}",
    }
    if config_over:
        config.update(config_over)
    source = {"name": "hackernews", "adapter": "json_api", "config_json": config}
    return HackerNewsAdapter(source, client)


def _collect(since=1000, until=1230, config_over=None):
    client = FakeAlgolia(_dataset())
    adapter = _adapter(client, config_over)
    items = [it for page in adapter.iter_items(since, until) for it in page]
    return items, client


def test_recovers_every_item_past_the_cap():
    items, _ = _collect()
    assert len({it.source_id for it in items}) == 260


def test_only_boundary_refetches_are_duplicated():
    items, _ = _collect()
    ids = [it.source_id for it in items]
    assert len(ids) - len(set(ids)) <= 4


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


def test_thread_id_mapped_from_story_id():
    items, _ = _collect()
    c5 = next(it for it in items if it.source_id == "c5")
    assert c5.thread_id == str(9000 + 5 // 50)     # comment -> parent story
    s3 = next(it for it in items if it.source_id == "s3")
    assert s3.thread_id == "s3"                     # story -> own id


def test_terminates_and_is_not_pathological():
    _, client = _collect()
    assert client.calls < 40


def test_respects_lower_bound():
    items, _ = _collect(since=1200, until=1230)
    assert items and all(int(it.created_at_i) >= 1200 for it in items)


# --- targeted `searches` (Ask/Show + phrase queries) -----------------------

def test_searches_config_issues_configured_queries():
    searches = [
        {"tags": "comment", "query": "quote"},     # matches all comments
    ]
    items, client = _collect(config_over={"searches": searches})
    # every request carried the query, and only the comment tag was used
    assert all(p.get("query") == "quote" for p in client.seen_params)
    assert all(p.get("tags") == "comment" for p in client.seen_params)
    assert items and all(it.source_id.startswith("c") for it in items)


def test_min_points_adds_numeric_filter():
    # A search with min_points must append "points>=N" to numericFilters so
    # a high-engagement "story" search stays scoped server-side (§5 v3 brief:
    # major launches were never fetched at all before this).
    data = {"story": [
        {"objectID": "s1", "created_at_i": 1100, "title": "Big launch"},
    ]}
    client = FakeAlgolia(data)
    adapter = _adapter(client, {"searches": [{"tags": "story", "min_points": 300}]})
    list(adapter.iter_items(1000, 1200))
    assert client.seen_params
    assert all("points>=300" in p.get("numericFilters", "") for p in client.seen_params)


def test_no_min_points_omits_points_filter():
    data = {"story": [{"objectID": "s1", "created_at_i": 1100, "title": "x"}]}
    client = FakeAlgolia(data)
    adapter = _adapter(client, {"searches": [{"tags": "story"}]})
    list(adapter.iter_items(1000, 1200))
    assert all("points" not in p.get("numericFilters", "") for p in client.seen_params)


def test_query_filters_results():
    # A phrase present in only one comment returns just that one.
    data = {"comment": [
        {"objectID": "cA", "created_at_i": 1100, "story_id": 1,
         "comment_text": "I built a unicorn-detector this weekend"},
        {"objectID": "cB", "created_at_i": 1101, "story_id": 2,
         "comment_text": "just a normal comment about nothing"},
    ]}
    client = FakeAlgolia(data)
    adapter = _adapter(client, {"searches": [{"tags": "comment", "query": "unicorn-detector"}]})
    items = [it for page in adapter.iter_items(1000, 1200) for it in page]
    assert [it.source_id for it in items] == ["cA"]
