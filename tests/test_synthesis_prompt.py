"""Pass-3 synthesis prompt assembly (prompt.build_synthesis_messages, §3 pass 3)."""

from painminer.prompt import (
    SYNTHESIS_SCHEMA,
    SYNTHESIS_SECTIONS,
    SYNTHESIS_SYSTEM_PROMPT,
    build_synthesis_messages,
)


def test_synthesis_schema_required_keys():
    assert set(SYNTHESIS_SCHEMA["required"]) == {
        "night_summary", "patterns", "papers", "worth_reading", "shipped",
        "market", "gaps", "someone_built",
    }


def test_synthesis_schema_sections_are_arrays_of_items():
    for section in SYNTHESIS_SECTIONS:
        section_schema = SYNTHESIS_SCHEMA["properties"][section]
        assert section_schema["type"] == "array"
        item_schema = section_schema["items"]
        assert item_schema["required"] == ["headline", "body", "topic", "finding_ids"]
        assert item_schema["additionalProperties"] is False


def test_synthesis_sections_order():
    # "shipped" (funded-lab launches) outranks "someone_built" (indie
    # projects) so it survives the total-cap cut first.
    assert SYNTHESIS_SECTIONS == (
        "patterns", "papers", "worth_reading", "shipped", "market", "gaps",
        "someone_built",
    )


def test_build_synthesis_messages_shape():
    msgs = build_synthesis_messages("findings block text", "clusters block text")
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == SYNTHESIS_SYSTEM_PROMPT
    assert msgs[1]["role"] == "user"
    user = msgs[1]["content"]
    assert "findings block text" in user
    assert "clusters block text" in user


def test_build_synthesis_messages_empty_placeholders():
    msgs = build_synthesis_messages("", "")
    user = msgs[-1]["content"]
    assert "(none tonight)" in user
    assert "(none yet)" in user


def test_synthesis_system_prompt_quiet_night_instruction():
    assert "quiet night" in SYNTHESIS_SYSTEM_PROMPT.lower()


def test_synthesis_system_prompt_bans_why_it_matters_phrase():
    assert "why it matters" in SYNTHESIS_SYSTEM_PROMPT
