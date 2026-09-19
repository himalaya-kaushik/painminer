"""run_judge's tolerance for LLM errors mid-run: a single timeout/connection
error must not abort a multi-hour run (Machine B can transiently evict a
model under memory pressure); only MAX_CONSECUTIVE_LLM_ERRORS in a row does.
Fully offline: a minimal fake DB/LLM implementing just the call surface
run_judge actually uses. Plain functions, no pytest, matching run_tests.py.
"""

from __future__ import annotations

import openai

from painminer.config import Config
from painminer.pipeline.judge import (
    MAX_CONSECUTIVE_LLM_ERRORS,
    PreflightError,
    run_judge,
)


# --- minimal fake DB: just enough surface for run_judge + queue_ops ---------

class _Query:
    """Chainable no-op query builder; terminal .execute() is table-specific."""

    def __init__(self, db: "FakeDB", table: str):
        self.db = db
        self.table_name = table
        self._update = None

    def select(self, *_a, **_k):
        return self

    def eq(self, field, value):
        if self._update is not None and field == "id":
            self.db.items[value]["state"] = self._update["state"]
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def update(self, values):
        self._update = values
        return self

    def execute(self):
        if self.table_name == "sources":
            return _Resp([{"name": "only_source"}])
        return _Resp([])   # updates don't need a return value


class _Resp:
    def __init__(self, data):
        self.data = data


class _Rpc:
    def __init__(self, db: "FakeDB", name: str, params: dict):
        self.db = db
        self.name = name
        self.params = params

    def execute(self):
        if self.name == "claim_items":
            batch = [
                it for it in self.db.items.values()
                if it["state"] == "ready" and (self.params.get("p_source") in (None, it["source"]))
            ][: self.params["batch_size"]]
            for it in batch:
                it["state"] = "processing"
            return _Resp(batch)
        if self.name == "record_judgement":
            item_id = self.params["p_item_id"]
            self.db.items[item_id]["state"] = "done"
            self.db.items[item_id]["raw_text"] = None
            n = len(self.params["p_findings"])
            self.db.committed.append((item_id, n))
            return _Resp(n)
        raise AssertionError(f"unexpected rpc {self.name}")


class _Client:
    def __init__(self, db: "FakeDB"):
        self.db = db

    def rpc(self, name, params):
        return _Rpc(self.db, name, params)


class FakeDB:
    def __init__(self, n_items: int):
        self.items = {
            i: {"id": i, "source": "only_source", "raw_text": f"item {i} text",
                "state": "ready", "thread_id": None, "metadata": None}
            for i in range(1, n_items + 1)
        }
        self.committed: list[tuple[int, int]] = []
        self.client = _Client(self)

    def table(self, name):
        return _Query(self, name)


# --- minimal fake LLM --------------------------------------------------------

class FlakyLLM:
    """Always triages worth_reading=True; complete() fails on specific calls."""

    def __init__(self, fail_on: set[int]):
        self.fail_on = fail_on   # 1-indexed call numbers that raise
        self.calls = 0

    def list_models(self):
        return ["m", "e"]   # llm_model and embed_model from _config()

    def warmup(self):
        return 0.0

    def triage(self, _text):
        return {"worth_reading": True, "one_line": ""}

    def complete(self, _messages):
        self.calls += 1
        if self.calls in self.fail_on:
            raise openai.APITimeoutError(request=None)
        return "[]"


def _config():
    return Config(
        supabase_url="u", supabase_service_key="k", telegram_token="t",
        telegram_chat_id="c", llm_base_url="http://x/v1", llm_model="m",
        embed_model="e", synthesis_model="m",
    )


def test_single_transient_error_does_not_abort_the_run():
    db = FakeDB(5)
    llm = FlakyLLM(fail_on={2})   # 2nd deep-read call fails, then recovers
    summary = run_judge(db, llm, _config(), context_fetcher=None)
    # the failed item was released (not lost) and picked back up before the
    # queue drained; no items left ready or processing.
    assert all(it["state"] == "done" for it in db.items.values())
    assert summary.items_judged == 5


def test_sustained_errors_abort_after_the_threshold():
    db = FakeDB(10)
    # Fail every call from the 2nd onward -> hits MAX_CONSECUTIVE_LLM_ERRORS
    llm = FlakyLLM(fail_on=set(range(2, 20)))
    try:
        run_judge(db, llm, _config(), context_fetcher=None)
        assert False, "expected PreflightError"
    except PreflightError as exc:
        assert f"{MAX_CONSECUTIVE_LLM_ERRORS} consecutive" in str(exc)
    # item 1 succeeded before the errors started; the next 3 items that each
    # hit an error were released back to ready (not burned as an attempt);
    # the batch item the abort interrupted mid-loop is left 'processing' --
    # claim_items' existing stuck-row reclaim (sql/queue.sql) picks it up on
    # a later run, same as any other mid-run interruption (e.g. a crash).
    # Items never claimed at all stay 'ready' untouched.
    states = [it["state"] for it in db.items.values()]
    assert states.count("done") == 1
    assert states.count("processing") == 1
    assert states.count("ready") == len(db.items) - 2


def test_a_success_between_errors_resets_the_counter():
    db = FakeDB(10)
    # error, success, error, success, error -- never 3 in a row -> must finish.
    llm = FlakyLLM(fail_on={1, 3, 5})
    summary = run_judge(db, llm, _config(), context_fetcher=None)
    assert all(it["state"] == "done" for it in db.items.values())
    assert summary.items_judged == 10
