"""Hacker News thread-context fetching (pipeline.thread_context), against a
fake httpx-like client — no network."""

from painminer.pipeline.thread_context import fetch_thread_context


class FakeResp:
    def __init__(self, data):
        self._d = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._d


class FakeClient:
    def __init__(self, data=None, exc=None):
        self.data = data
        self.exc = exc

    def get(self, url, timeout=None):
        if self.exc:
            raise self.exc
        return FakeResp(self.data)


def test_non_hackernews_source_returns_none():
    item = {"source": "github", "thread_id": "111"}
    client = FakeClient(data={"title": "should not be used"})
    assert fetch_thread_context(item, client) is None


def test_hackernews_walks_nested_comment_tree():
    item = {"source": "hackernews", "thread_id": "111", "source_id": "222"}
    story = {
        "title": "T",
        "text": "story body",
        "children": [
            {
                "author": "al",
                "text": "comment one",
                "children": [
                    {"author": "bo", "text": "reply"},
                ],
            },
        ],
    }
    client = FakeClient(data=story)
    context = fetch_thread_context(item, client)
    assert context is not None
    assert "Story: T" in context
    assert "story body" in context
    assert "comment one" in context
    assert "reply" in context


def test_hackernews_client_error_returns_none_never_raises():
    item = {"source": "hackernews", "thread_id": "111", "source_id": "222"}
    client = FakeClient(exc=Exception("boom"))
    assert fetch_thread_context(item, client) is None


def test_hackernews_no_thread_id_returns_none():
    item = {"source": "hackernews"}
    client = FakeClient(data={"title": "T"})
    assert fetch_thread_context(item, client) is None


def test_char_cap_truncates_with_marker():
    long_text = "x" * 5000
    story = {"title": "T", "text": long_text, "children": []}
    item = {"source": "hackernews", "thread_id": "111"}
    client = FakeClient(data=story)
    context = fetch_thread_context(item, client, max_chars=50)
    assert context is not None
    assert "truncated" in context
    assert len(context) <= 50 + len("\n…[thread truncated]")


def test_clean_html_applied_to_comment_text():
    story = {
        "title": "T",
        "text": "",
        "children": [
            {"author": "al", "text": "<p>hi</p>"},
        ],
    }
    item = {"source": "hackernews", "thread_id": "111"}
    client = FakeClient(data=story)
    context = fetch_thread_context(item, client)
    assert context is not None
    assert "hi" in context
    assert "<p>" not in context
