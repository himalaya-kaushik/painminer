"""Offline tests for painminer.pipeline.queue_ops.claim, using in-process
fakes for db.client.rpc(...).execute() — no network, no database."""

from __future__ import annotations

from painminer.pipeline.queue_ops import claim


class FakeResp:
    def __init__(self, data):
        self.data = data


class FakeRPC:
    def __init__(self, captured, data):
        self.captured = captured
        self.data = data

    def execute(self):
        return FakeResp(self.data)


class FakeClient:
    def __init__(self, data):
        self.captured = {}
        self.data = data

    def rpc(self, name, params):
        self.captured["name"] = name
        self.captured["params"] = params
        return FakeRPC(self.captured, self.data)


class FakeDB:
    def __init__(self, data=None):
        self.client = FakeClient(data if data is not None else [])


def test_claim_default_source_is_none():
    db = FakeDB()
    claim(db, 5)
    assert db.client.captured["name"] == "claim_items"
    params = db.client.captured["params"]
    assert params["batch_size"] == 5
    assert "stuck_seconds" in params
    assert "max_attempts" in params
    assert params["p_source"] is None


def test_claim_passes_source_through():
    db = FakeDB()
    claim(db, 5, source="lobsters")
    assert db.client.captured["params"]["p_source"] == "lobsters"


def test_claim_returns_empty_list_when_no_data():
    db = FakeDB([])
    result = claim(db, 5)
    assert result == []


def test_claim_returns_data_list_when_nonempty():
    db = FakeDB([{"id": 1}])
    result = claim(db, 5)
    assert result == [{"id": 1}]
