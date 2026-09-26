"""Atom/RSS feed adapter — offline, static-fixture tests (no network).

Covers: Atom entry parsing (arXiv-shaped), RSS 2.0 item parsing, since_ts/
overlap windowing, malformed-entry skip-not-raise, and clean_html stripping.
"""

from painminer.adapters.atom import AtomAdapter


class FakeResp:
    def __init__(self, text):
        self.text = text
        self.content = text.encode()

    def raise_for_status(self):
        pass


class FakeClient:
    def __init__(self, text):
        self.text = text

    def get(self, url, *a, **k):
        return FakeResp(self.text)


ATOM_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <title>arXiv Query</title>
  <entry>
    <id>http://arxiv.org/abs/2609.15987v1</id>
    <title>Bellman Policy Optimization</title>
    <summary>A method for &lt;b&gt;critic-free&lt;/b&gt; reinforcement learning.</summary>
    <link href="https://arxiv.org/abs/2609.15987v1" rel="alternate" type="text/html"/>
    <link href="https://arxiv.org/pdf/2609.15987v1" rel="related" type="application/pdf"/>
    <published>2026-09-14T17:59:47Z</published>
    <updated>2026-09-14T17:59:47Z</updated>
    <author><name>Zhuoqing Song</name></author>
    <category term="cs.LG" scheme="http://arxiv.org/schemas/atom"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2601.00001v1</id>
    <title>An Old Paper</title>
    <summary>Published long before the window.</summary>
    <link href="https://arxiv.org/abs/2601.00001v1" rel="alternate" type="text/html"/>
    <published>2020-01-01T00:00:00Z</published>
  </entry>
  <entry>
    <title>Malformed Entry With No Timestamp</title>
    <summary>Should be skipped, not raise.</summary>
    <link href="https://arxiv.org/abs/9999.99999v1" rel="alternate" type="text/html"/>
  </entry>
</feed>
"""

RSS_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Product Hunt</title>
    <item>
      <title>FATHER</title>
      <description>&lt;p&gt;A Mac dashboard for site traffic, deploys, uptime, and SEO&lt;/p&gt;</description>
      <link>https://www.producthunt.com/products/father-web-monitoring</link>
      <guid>tag:www.producthunt.com,2005:Post/1250659</guid>
      <pubDate>Mon, 14 Sep 2026 13:10:06 -0700</pubDate>
    </item>
    <item>
      <title>Ancient Product</title>
      <description>Way before the window.</description>
      <link>https://www.producthunt.com/products/ancient</link>
      <guid>tag:www.producthunt.com,2005:Post/1</guid>
      <pubDate>Mon, 01 Jan 2001 00:00:00 -0700</pubDate>
    </item>
    <item>
      <title>Malformed Item, Unparseable Date</title>
      <description>Should be skipped, not raise.</description>
      <link>https://www.producthunt.com/products/malformed</link>
      <guid>tag:www.producthunt.com,2005:Post/2</guid>
      <pubDate>not-a-real-date</pubDate>
    </item>
  </channel>
</rss>
"""


def _config(overrides=None):
    config = {
        "adapter_impl": "atom",
        "feed_url": "https://example.test/feed",
        "overlap_hours": 0,
        "clean_html": True,
    }
    if overrides:
        config.update(overrides)
    return config


def _adapter(name, text, overrides=None):
    source = {"name": name, "adapter": "rss", "config_json": _config(overrides)}
    return AtomAdapter(source, FakeClient(text))


def _collect(adapter, since, until=None):
    return [it for page in adapter.iter_items(since, until) for it in page]


# --- Atom -------------------------------------------------------------


def test_atom_entry_mapped_correctly():
    adapter = _adapter("arxiv", ATOM_FIXTURE, {"overlap_hours": 100000})
    items = {it.source_id: it for it in _collect(adapter, since=0)}
    item = items["http://arxiv.org/abs/2609.15987v1"]
    assert item.url == "https://arxiv.org/abs/2609.15987v1"
    assert "Bellman Policy Optimization" in item.raw_text
    assert "critic-free reinforcement learning" in item.raw_text
    assert item.created_at_i > 0
    assert item.thread_id is None
    assert item.metadata and item.metadata.get("categories") == ["cs.LG"]


def test_atom_clean_html_strips_tags():
    adapter = _adapter("arxiv", ATOM_FIXTURE, {"overlap_hours": 100000})
    items = {it.source_id: it for it in _collect(adapter, since=0)}
    item = items["http://arxiv.org/abs/2609.15987v1"]
    assert "<b>" not in item.raw_text and "</b>" not in item.raw_text


def test_atom_since_ts_filters_old_entries():
    adapter = _adapter("arxiv", ATOM_FIXTURE, {"overlap_hours": 1})
    # 2026-09-14T17:59:47Z as epoch seconds
    since = 1789408787
    items = _collect(adapter, since=since)
    ids = {it.source_id for it in items}
    assert "http://arxiv.org/abs/2609.15987v1" in ids
    assert "http://arxiv.org/abs/2601.00001v1" not in ids  # too old, dropped


def test_atom_malformed_entry_skipped_not_raised():
    adapter = _adapter("arxiv", ATOM_FIXTURE, {"overlap_hours": 100000})
    items = _collect(adapter, since=0)
    titles = [it.raw_text for it in items]
    assert not any("Malformed Entry" in t for t in titles)
    assert len(items) == 2  # only the two well-formed, timestamped entries


# --- RSS ----------------------------------------------------------------


def test_rss_item_mapped_correctly():
    adapter = _adapter("producthunt", RSS_FIXTURE, {"overlap_hours": 100000})
    items = {it.source_id: it for it in _collect(adapter, since=0)}
    item = items["tag:www.producthunt.com,2005:Post/1250659"]
    assert item.url == "https://www.producthunt.com/products/father-web-monitoring"
    assert "FATHER" in item.raw_text
    assert "Mac dashboard" in item.raw_text
    assert item.created_at_i > 0


def test_rss_clean_html_strips_tags():
    adapter = _adapter("producthunt", RSS_FIXTURE, {"overlap_hours": 100000})
    items = {it.source_id: it for it in _collect(adapter, since=0)}
    item = items["tag:www.producthunt.com,2005:Post/1250659"]
    assert "<p>" not in item.raw_text


def test_rss_since_ts_filters_old_entries():
    adapter = _adapter("producthunt", RSS_FIXTURE, {"overlap_hours": 1})
    since = 1789416606  # 2026-09-14T13:10:06-07:00 as epoch seconds
    items = _collect(adapter, since=since)
    ids = {it.source_id for it in items}
    assert "tag:www.producthunt.com,2005:Post/1250659" in ids
    assert "tag:www.producthunt.com,2005:Post/1" not in ids  # 2001, dropped


def test_rss_malformed_item_skipped_not_raised():
    adapter = _adapter("producthunt", RSS_FIXTURE, {"overlap_hours": 100000})
    items = _collect(adapter, since=0)
    ids = {it.source_id for it in items}
    assert "tag:www.producthunt.com,2005:Post/2" not in ids  # bad pubDate
    assert len(items) == 2


# --- multi-feed -----------------------------------------------------------


def test_feed_urls_list_supported():
    source = {
        "name": "multi",
        "adapter": "rss",
        "config_json": _config({"feed_urls": ["https://a.test/feed", "https://b.test/feed"],
                                 "overlap_hours": 100000}),
    }
    del source["config_json"]["feed_url"]
    adapter = AtomAdapter(source, FakeClient(ATOM_FIXTURE))
    assert adapter.feed_urls == ["https://a.test/feed", "https://b.test/feed"]
    pages = list(adapter.iter_items(0))
    assert len(pages) == 2  # one page per feed url


# --- multi-feed failure isolation --------------------------------------


class _RoutedClient:
    """Serves RSS_FIXTURE except for URLs listed as dead, which raise."""

    def __init__(self, dead):
        self.dead = set(dead)

    def get(self, url, *a, **k):
        if url in self.dead:
            raise RuntimeError(f"HTTP 404 for {url}")
        return FakeResp(RSS_FIXTURE)


def _multi(dead):
    source = {"name": "blogs", "adapter": "rss", "config_json": _config({
        "feed_url": None,
        "feed_urls": ["https://a.test/feed", "https://b.test/feed"],
        "overlap_hours": 100000,
    })}
    return AtomAdapter(source, _RoutedClient(dead))


def test_one_dead_feed_does_not_kill_the_others():
    items = _collect(_multi(dead=["https://a.test/feed"]), since=0)
    assert any("FATHER" in it.raw_text for it in items)


def test_all_feeds_dead_still_raises():
    adapter = _multi(dead=["https://a.test/feed", "https://b.test/feed"])
    try:
        _collect(adapter, since=0)
    except RuntimeError as exc:
        assert "b.test" in str(exc)
    else:
        raise AssertionError("expected the source to fail when every feed fails")
