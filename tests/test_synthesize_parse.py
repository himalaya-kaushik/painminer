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
        "shipped": [
            {"headline": "Lab ships new model", "body": "Body C", "finding_ids": [6]},
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
    assert result.sections["shipped"] == [
        {"headline": "Lab ships new model", "body": "Body C", "finding_ids": [6]},
    ]
    assert result.sections["someone_built"] == []


def test_parse_malformed_json_does_not_raise():
    result = _parse("not json", {})
    assert isinstance(result, SynthesisResult)
    assert isinstance(result.night_summary, str)
    assert result.night_summary != ""
    for section in ("patterns", "worth_reading", "shipped", "someone_built"):
        assert result.sections.get(section, []) == []


def test_parse_missing_shipped_key_in_raw_json_defaults_empty():
    # A raw response that omits "shipped" entirely (model didn't emit it, or
    # an older recorded response) must not crash and must default to [].
    raw = json.dumps({
        "night_summary": "x", "patterns": [], "worth_reading": [], "someone_built": [],
    })
    result = _parse(raw, {})
    assert result.sections["shipped"] == []


def test_is_empty_true_when_all_sections_empty():
    result = SynthesisResult(sections={
        "patterns": [], "worth_reading": [], "shipped": [], "someone_built": [],
    })
    assert result.is_empty is True


def test_is_empty_false_when_only_shipped_has_item():
    result = SynthesisResult(sections={
        "patterns": [], "worth_reading": [], "someone_built": [],
        "shipped": [{"headline": "h", "body": "b", "finding_ids": []}],
    })
    assert result.is_empty is False


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


# --- prompt-size cap (select_for_prompt) ------------------------------------

def _f(i, conf, created="2026-09-19T00:00:00+00:00"):
    return {"id": i, "kind": "pain", "statement": f"s{i}", "confidence": conf,
            "created_at": created}


def test_select_for_prompt_returns_all_when_under_cap():
    from painminer.pipeline.synthesize import select_for_prompt
    findings = [_f(1, 0.5), _f(2, 0.9)]
    assert select_for_prompt(findings, 250) == findings


def test_select_for_prompt_keeps_highest_confidence_and_sorts_by_id():
    from painminer.pipeline.synthesize import select_for_prompt
    findings = [_f(1, 0.10), _f(2, 0.95), _f(3, 0.50), _f(4, 0.90)]
    out = select_for_prompt(findings, 2)
    # the two strongest (ids 2 and 4) survive, returned in id order
    assert [f["id"] for f in out] == [2, 4]


def test_select_for_prompt_zero_or_negative_cap_disables_capping():
    from painminer.pipeline.synthesize import select_for_prompt
    findings = [_f(1, 0.5), _f(2, 0.9)]
    assert select_for_prompt(findings, 0) == findings


def test_select_for_prompt_handles_missing_confidence():
    from painminer.pipeline.synthesize import select_for_prompt
    findings = [{"id": 1, "statement": "a"}, _f(2, 0.9)]
    out = select_for_prompt(findings, 1)
    assert [f["id"] for f in out] == [2]   # the one with real confidence wins
