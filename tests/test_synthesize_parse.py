"""Pure parsing/formatting helpers in pipeline.synthesize — no DB, no LLM."""

import json

from painminer.pipeline.synthesize import (
    SynthesisResult,
    _finding_line,
    _parse,
    findings_block,
)


def _full_raw():
    return json.dumps({
        "night_summary": "Quiet night.",
        "patterns": [
            {"headline": "Pattern A", "body": "Body A", "finding_ids": [1, 2]},
        ],
        "worth_reading": [
            {"headline": "", "body": "dropped, no headline", "finding_ids": [3]},
            {"headline": "Read this", "body": "Body B", "finding_ids": [4.0, 5]},
        ],
        "someone_built": [],
    })


def test_parse_full_briefing_dict():
    result = _parse(_full_raw(), {})
    assert isinstance(result, SynthesisResult)
    assert result.night_summary == "Quiet night."
    assert result.sections["patterns"] == [
        {"headline": "Pattern A", "body": "Body A", "finding_ids": [1, 2]},
    ]
    # empty-headline item dropped; finding_ids coerced to ints (non-numeric
    # entries like a stray string id are filtered out, not coerced)
    assert result.sections["worth_reading"] == [
        {"headline": "Read this", "body": "Body B", "finding_ids": [4, 5]},
    ]
    assert result.sections["someone_built"] == []


def test_parse_malformed_json_does_not_raise():
    result = _parse("not json", {})
    assert isinstance(result, SynthesisResult)
    assert isinstance(result.night_summary, str)
    assert result.night_summary != ""
    for section in ("patterns", "worth_reading", "someone_built"):
        assert result.sections.get(section, []) == []


def test_is_empty_true_when_all_sections_empty():
    result = SynthesisResult(sections={
        "patterns": [], "worth_reading": [], "someone_built": [],
    })
    assert result.is_empty is True


def test_is_empty_false_when_any_section_has_item():
    result = SynthesisResult(sections={
        "patterns": [{"headline": "h", "body": "b", "finding_ids": []}],
        "worth_reading": [],
        "someone_built": [],
    })
    assert result.is_empty is False


def _finding(**overrides):
    f = {
        "id": 7,
        "kind": "pain",
        "statement": "X",
        "why_it_matters": "Y",
        "_source": "hackernews",
        "_url": "http://u",
        "_cluster": {"mention_count": 3, "sources_seen": ["a", "b"], "days_seen": ["d1"]},
    }
    f.update(overrides)
    return f


def test_finding_line_full_fields():
    line = _finding_line(_finding())
    assert line.startswith("[7]")
    assert "pain" in line
    assert "X" in line
    assert "why: Y" in line
    assert "hackernews" in line
    assert "http://u" in line
    assert "recurring:" in line


def test_finding_line_no_recurring_note_when_mention_count_one():
    line = _finding_line(_finding(_cluster={"mention_count": 1, "sources_seen": ["a"], "days_seen": ["d1"]}))
    assert "recurring:" not in line


def test_finding_line_no_recurring_note_when_cluster_missing():
    line = _finding_line(_finding(_cluster=None))
    assert "recurring:" not in line


def test_findings_block_joins_lines():
    findings = [_finding(id=1), _finding(id=2)]
    block = findings_block(findings)
    lines = block.split("\n")
    assert len(lines) == 2
    assert lines[0].startswith("[1]")
    assert lines[1].startswith("[2]")
