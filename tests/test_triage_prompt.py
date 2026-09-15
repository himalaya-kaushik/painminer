"""Pass-1 triage prompt assembly (prompt.build_triage_messages, §3 pass 1)."""

from painminer.prompt import (
    TRIAGE_SCHEMA,
    TRIAGE_SYSTEM_PROMPT,
    build_triage_messages,
)


def test_build_triage_messages_shape():
    msgs = build_triage_messages("some raw text")
    assert msgs == [
        {"role": "system", "content": TRIAGE_SYSTEM_PROMPT},
        {"role": "user", "content": "some raw text"},
    ]


def test_build_triage_messages_user_is_raw_text_verbatim():
    msgs = build_triage_messages("THE RAW ITEM TEXT")
    assert msgs[-1] == {"role": "user", "content": "THE RAW ITEM TEXT"}


def test_triage_schema_shape():
    assert TRIAGE_SCHEMA["required"] == ["worth_reading", "one_line"]
    assert TRIAGE_SCHEMA["additionalProperties"] is False
    assert TRIAGE_SCHEMA["properties"]["worth_reading"]["type"] == "boolean"


def test_triage_system_prompt_has_reader_profile():
    assert "machine learning engineer in India" in TRIAGE_SYSTEM_PROMPT


def test_triage_system_prompt_is_false_biased():
    assert "default answer is false" in TRIAGE_SYSTEM_PROMPT
