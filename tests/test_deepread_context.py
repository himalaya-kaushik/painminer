"""Thread-context injection into the pass-2 deep-read user message
(prompt.build_messages, §3 pass 2)."""

from painminer.prompt import build_messages


def test_no_context_last_message_is_raw_text():
    assert build_messages("THE ITEM")[-1] == {"role": "user", "content": "THE ITEM"}


def test_context_included_with_marker_and_source_text():
    msgs = build_messages("THE ITEM", context="parent story stuff")
    user = msgs[-1]["content"]
    assert "parent story stuff" in user
    assert "THE ITEM" in user
    assert "Thread context" in user


def test_context_and_engagement_both_present():
    msgs = build_messages(
        "THE ITEM",
        context="parent story stuff",
        engagement={"points": 120, "num_comments": 45},
    )
    user = msgs[-1]["content"]
    assert "120 points" in user
    assert "parent story stuff" in user


def test_empty_context_string_adds_no_marker():
    msgs = build_messages("THE ITEM", context="")
    assert "Thread context" not in msgs[-1]["content"]


def test_none_context_adds_no_marker():
    msgs = build_messages("THE ITEM", context=None)
    assert "Thread context" not in msgs[-1]["content"]


def test_whitespace_only_context_adds_no_marker():
    msgs = build_messages("THE ITEM", context="   \n  ")
    assert "Thread context" not in msgs[-1]["content"]
