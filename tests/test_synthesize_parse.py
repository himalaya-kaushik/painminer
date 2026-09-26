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
            {"headline": "Pattern A", "body": "Body A", "topic": "Systems",
             "finding_ids": [1, 2]},
        ],
        "papers": [],
        "worth_reading": [
            {"headline": "", "body": "dropped, no headline", "finding_ids": [3]},
            {"headline": "Read this", "body": "Body B", "finding_ids": [4.0, 5]},
        ],
        "shipped": [
            {"headline": "Lab ships new model", "body": "Body C", "finding_ids": [6]},
        ],
        "market": [],
        "gaps": [],
        "someone_built": [],
    })


def test_parse_full_briefing_dict():
    result = _parse(_full_raw(), {})
    assert isinstance(result, SynthesisResult)
    assert result.night_summary == "Quiet night."
    assert result.sections["patterns"] == [
        {"headline": "Pattern A", "body": "Body A", "topic": "Systems",
         "finding_ids": [1, 2]},
    ]
    # empty-headline item dropped; finding_ids coerced to ints (non-numeric
    # entries like a stray string id are filtered out, not coerced); topic
    # missing on this item is tolerated and defaults to "".
    assert result.sections["worth_reading"] == [
        {"headline": "Read this", "body": "Body B", "topic": "", "finding_ids": [4, 5]},
    ]
    assert result.sections["shipped"] == [
        {"headline": "Lab ships new model", "body": "Body C", "topic": "",
         "finding_ids": [6]},
    ]
    assert result.sections["someone_built"] == []


def test_parse_missing_topic_does_not_drop_item():
    raw = json.dumps({
        "night_summary": "x",
        "patterns": [{"headline": "h", "body": "b", "finding_ids": [1]}],
    })
    result = _parse(raw, {})
    assert len(result.sections["patterns"]) == 1
    assert result.sections["patterns"][0]["topic"] == ""


def test_parse_keeps_topic_when_present():
    raw = json.dumps({
        "night_summary": "x",
        "patterns": [{"headline": "h", "body": "b", "topic": "RL", "finding_ids": [1]}],
    })
    result = _parse(raw, {})
    assert result.sections["patterns"][0]["topic"] == "RL"


def test_parse_old_format_without_topic_key_at_all_still_parses():
    # A synthesis JSON produced before `topic` existed: only the four old
    # keys (headline, body, finding_ids) with no "topic" field anywhere.
    raw = json.dumps({
        "night_summary": "Old-format night.",
        "patterns": [{"headline": "P", "body": "b", "finding_ids": [1]}],
        "worth_reading": [{"headline": "W", "body": "b2", "finding_ids": [2]}],
        "shipped": [],
        "someone_built": [],
    })
    result = _parse(raw, {})
    assert result.night_summary == "Old-format night."
    assert result.sections["patterns"][0]["topic"] == ""
    assert result.sections["worth_reading"][0]["topic"] == ""
    # new sections not present in the old-format JSON come out empty.
    assert result.sections["papers"] == []
    assert result.sections["market"] == []
    assert result.sections["gaps"] == []


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


# --- select_for_prompt source balancing (max_source_share) ------------------

def _fs(i, conf, source, created="2026-09-19T00:00:00+00:00"):
    return {"id": i, "kind": "pain", "statement": f"s{i}", "confidence": conf,
            "created_at": created, "_source": source}


def test_select_for_prompt_default_share_matches_old_top_n_behaviour():
    from painminer.pipeline.synthesize import select_for_prompt
    findings = [
        _fs(1, 0.10, "arxiv"),
        _fs(2, 0.95, "arxiv"),
        _fs(3, 0.50, "arxiv"),
        _fs(4, 0.90, "arxiv"),
        _fs(5, 0.99, "github"),
        _fs(6, 0.20, "github"),
    ]
    # default max_source_share=1.0 -> no source cap, plain top-3 by confidence
    out = select_for_prompt(findings, 3)
    assert [f["id"] for f in out] == [2, 4, 5]


def test_select_for_prompt_under_cap_unchanged_regardless_of_share():
    from painminer.pipeline.synthesize import select_for_prompt
    findings = [_fs(1, 0.9, "arxiv"), _fs(2, 0.9, "arxiv"), _fs(3, 0.1, "github")]
    # len(findings) <= max_findings -> returned as-is, share is irrelevant
    assert select_for_prompt(findings, 10, max_source_share=0.01) == findings
    assert select_for_prompt(findings, 3, max_source_share=0.01) == findings


def test_select_for_prompt_caps_dominant_source_to_include_smaller_source():
    from painminer.pipeline.synthesize import select_for_prompt
    arxiv = [_fs(i, 0.80 + i * 0.001, "arxiv") for i in range(1, 11)]       # 10, high confidence
    github = [_fs(i, 0.10 + (i - 100) * 0.001, "github") for i in range(101, 106)]  # 5, low confidence
    out = select_for_prompt(arxiv + github, 6, max_source_share=0.5)
    sources = [f["_source"] for f in out]
    assert sources.count("arxiv") == 3
    assert sources.count("github") == 3
    assert len(out) == 6
    assert [f["id"] for f in out] == sorted(f["id"] for f in out)  # id-sorted


def test_select_for_prompt_refills_dominant_source_when_others_cant_fill():
    from painminer.pipeline.synthesize import select_for_prompt
    arxiv = [_fs(i, 0.80 + i * 0.001, "arxiv") for i in range(1, 11)]  # 10, high confidence
    github = [_fs(101, 0.05, "github")]                                # 1, low confidence
    out = select_for_prompt(arxiv + github, 6, max_source_share=0.35)
    assert len(out) == 6
    sources = [f["_source"] for f in out]
    assert "github" in sources   # the small source's finding still makes it in
    assert sources.count("arxiv") == 5   # backfilled from the best remaining arxiv


def test_select_for_prompt_never_exceeds_cap_and_is_id_sorted():
    from painminer.pipeline.synthesize import select_for_prompt
    findings = [_fs(i, (i * 37) % 101 / 100.0, f"src{i % 4}") for i in range(50, 0, -1)]
    out = select_for_prompt(findings, 12, max_source_share=0.3)
    assert len(out) <= 12
    assert [f["id"] for f in out] == sorted(f["id"] for f in out)


def test_select_for_prompt_missing_source_treated_as_one_bucket():
    from painminer.pipeline.synthesize import select_for_prompt
    # Mix findings with no "_source" key at all and findings with an explicit
    # None source; both must group into the same (None) bucket without KeyError.
    findings = [
        {"id": 1, "confidence": 0.9, "created_at": "2026-09-19T00:00:00+00:00"},
        {"id": 2, "confidence": 0.8, "created_at": "2026-09-19T00:00:00+00:00", "_source": None},
        {"id": 3, "confidence": 0.7, "created_at": "2026-09-19T00:00:00+00:00"},
        _fs(4, 0.6, "github"),
    ]
    out = select_for_prompt(findings, 2, max_source_share=0.5)
    assert len(out) == 2
    assert [f["id"] for f in out] == sorted(f["id"] for f in out)
