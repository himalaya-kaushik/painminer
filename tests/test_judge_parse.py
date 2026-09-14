"""Judge output parsing + validation (judge.parse_and_validate, §7.5)."""

import json

from painminer.pipeline.judge import parse_and_validate


def _finding(**over):
    base = {
        "kind": "pain",
        "statement": "a specific stated problem",
        "why_it_matters": "one line",
        "domain": "developer-tools",
        "evidence_quote": "verbatim",
        "confidence": 0.8,
    }
    base.update(over)
    return base


def test_empty_array_is_valid():
    findings, err = parse_and_validate("[]")
    assert err is None and findings == []


def test_valid_single_finding():
    findings, err = parse_and_validate(json.dumps([_finding()]))
    assert err is None
    assert findings[0]["kind"] == "pain"


def test_open_taxonomy_kind_accepted():
    findings, err = parse_and_validate(json.dumps([_finding(kind="mysterious-new-kind")]))
    assert err is None and findings[0]["kind"] == "mysterious-new-kind"


def test_not_json():
    findings, err = parse_and_validate("not json at all")
    assert findings is None and "not valid JSON" in err


def test_top_level_object_rejected():
    findings, err = parse_and_validate(json.dumps(_finding()))
    assert findings is None and "array" in err


def test_missing_key_rejected():
    bad = _finding()
    del bad["evidence_quote"]
    findings, err = parse_and_validate(json.dumps([bad]))
    assert findings is None and "evidence_quote" in err


def test_confidence_out_of_range_rejected():
    findings, err = parse_and_validate(json.dumps([_finding(confidence=1.5)]))
    assert findings is None and "confidence" in err


def test_empty_statement_rejected():
    findings, err = parse_and_validate(json.dumps([_finding(statement="  ")]))
    assert findings is None and "statement" in err


def test_only_required_keys_kept():
    findings, err = parse_and_validate(json.dumps([_finding(extra="junk")]))
    assert err is None and "extra" not in findings[0]
