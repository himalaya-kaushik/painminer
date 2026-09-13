"""The judge prompt assembly (prompt.build_messages, §7)."""

import json

from prompt import (
    SYSTEM_PROMPT,
    FINDINGS_SCHEMA,
    build_messages,
    _POSITIVE_OUTPUT,
    _POSITIVE_SOURCE,
)


def test_system_prompt_is_verbatim_anchor_lines():
    # Guardrail: the highest-leverage lines must be present unchanged (§7.3).
    assert "MOST TEXT CONTAINS NOTHING USEFUL." in SYSTEM_PROMPT
    assert "Return [] when there is nothing worth surfacing." in SYSTEM_PROMPT
    assert "evidence_quote must be copied verbatim" in SYSTEM_PROMPT


def test_first_message_is_system_then_fewshot():
    msgs = build_messages("some source text")
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == SYSTEM_PROMPT
    # negatives are user/assistant pairs answering []
    assert any(m["role"] == "assistant" and m["content"] == "[]" for m in msgs)


def test_last_message_is_the_item_text():
    msgs = build_messages("THE ITEM")
    assert msgs[-1] == {"role": "user", "content": "THE ITEM"}


def test_positive_fewshot_has_two_findings():
    assert len(_POSITIVE_OUTPUT) == 2
    kinds = {f["kind"] for f in _POSITIVE_OUTPUT}
    assert kinds == {"pain", "build"}


def test_positive_evidence_quotes_are_verbatim():
    # The few-shot must model the rule it teaches: quotes are in the source.
    for f in _POSITIVE_OUTPUT:
        assert f["evidence_quote"] in _POSITIVE_SOURCE


def test_retry_appends_error():
    msgs = build_messages("ITEM", retry_error="element 0 missing keys")
    assert "was rejected" in msgs[-1]["content"]
    assert "element 0 missing keys" in msgs[-1]["content"]


def test_schema_is_open_array_of_findings():
    assert FINDINGS_SCHEMA["type"] == "array"
    props = FINDINGS_SCHEMA["items"]["properties"]
    assert "kind" in props and "enum" not in props["kind"]  # open taxonomy
    assert FINDINGS_SCHEMA["items"]["additionalProperties"] is False


def test_fewshot_assistant_outputs_parse_as_json():
    msgs = build_messages("x")
    for m in msgs:
        if m["role"] == "assistant":
            json.loads(m["content"])  # must be valid JSON
