"""Generic JsonApiAdapter — bare-array single-page response, overlap_hours."""

from painminer.adapters.json_api import JsonApiAdapter


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeClient:
    """Serves a single page: a bare JSON array of hits (no hits_path)."""

    def __init__(self, hits):
        self.hits = hits
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        return FakeResponse(self.hits)


def _adapter(client, config_over=None):
    config = {
        "adapter_impl": "json_api",
        "base_url": "https://example.com/api",
        "endpoint": "items",
        "hits_path": "",            # response root is the array itself
        "id_field": "id",
        "timestamp_field": "ts",
        "text_fields": ["text"],
    }
    if config_over:
        config.update(config_over)
    source = {"name": "generic", "adapter": "json_api", "config_json": config}
    return JsonApiAdapter(source, client)


def _collect(adapter, since, until=None):
    return [it for page in adapter.iter_items(since, until) for it in page]


def test_item_before_since_excluded_with_no_overlap():
    since_ts = 10_000
    hits = [{"id": "a", "ts": since_ts - 10 * 3600, "text": "old"}]
    client = FakeClient(hits)
    adapter = _adapter(client)
    items = _collect(adapter, since_ts)
    assert items == []


def test_item_before_since_included_with_overlap_hours_24():
    since_ts = 10_000
    hits = [{"id": "a", "ts": since_ts - 10 * 3600, "text": "old"}]
    client = FakeClient(hits)
    adapter = _adapter(client, {"overlap_hours": 24})
    items = _collect(adapter, since_ts)
    assert [it.source_id for it in items] == ["a"]


def test_default_overlap_hours_is_zero():
    since_ts = 10_000
    hits = [
        {"id": "a", "ts": since_ts - 1, "text": "just before"},
        {"id": "b", "ts": since_ts, "text": "right at"},
    ]
    client = FakeClient(hits)
    adapter = _adapter(client)   # no overlap_hours key at all
    items = _collect(adapter, since_ts)
    assert [it.source_id for it in items] == ["b"]
