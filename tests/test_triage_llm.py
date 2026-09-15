"""LLM.triage parsing/fallback behavior — no real client, no network.

Constructs the instance via __new__ (bypassing __init__, which would build a
real OpenAI client) and monkeypatches _structured to return canned output.
"""

from painminer.llm import LLM


def _make(structured_return):
    obj = LLM.__new__(LLM)
    obj._structured = lambda *a, **k: structured_return
    return obj


def test_triage_valid_json_worth_reading_false():
    obj = _make('{"worth_reading": false, "one_line": "just a rant"}')
    assert obj.triage("some text") == {
        "worth_reading": False,
        "one_line": "just a rant",
    }


def test_triage_valid_json_worth_reading_true():
    obj = _make('{"worth_reading": true, "one_line": "a real finding"}')
    res = obj.triage("some text")
    assert res["worth_reading"] is True
    assert res["one_line"] == "a real finding"


def test_triage_malformed_output_falls_back_to_true():
    obj = _make("not json")
    res = obj.triage("some text")
    assert res["worth_reading"] is True


def test_triage_missing_keys_defaults_false_and_empty_string():
    obj = _make("{}")
    res = obj.triage("some text")
    assert res["worth_reading"] is False
    assert res["one_line"] == ""
