"""Tests for painminer.pipeline.synthesize.drop_duplicate_stories."""

from __future__ import annotations

from painminer.pipeline.synthesize import SynthesisResult, drop_duplicate_stories
from painminer.prompt import SYNTHESIS_SECTIONS


def mk(sections: dict, findings_index: dict | None = None) -> SynthesisResult:
    r = SynthesisResult()
    r.sections = {s: sections.get(s, []) for s in SYNTHESIS_SECTIONS}
    r.findings_index = findings_index or {}
    return r


def item(headline, finding_ids):
    return {"headline": headline, "body": "", "finding_ids": finding_ids}


def test_same_finding_id_in_pattern_and_later_gap_drops_gap():
    r = mk({
        "patterns": [item("p", [1])],
        "gaps": [item("g", [1])],
    })
    drop_duplicate_stories(r)
    assert [i["headline"] for i in r.sections["patterns"]] == ["p"]
    assert r.sections["gaps"] == []


def test_different_ids_same_url_drops_later_item():
    r = mk(
        {
            "patterns": [item("p", [1])],
            "worth_reading": [item("w", [2])],
        },
        findings_index={
            1: {"url": "https://x.com/a"},
            2: {"url": "https://x.com/a"},
        },
    )
    drop_duplicate_stories(r)
    assert [i["headline"] for i in r.sections["patterns"]] == ["p"]
    assert r.sections["worth_reading"] == []


def test_items_with_no_url_and_different_ids_both_kept():
    r = mk(
        {
            "patterns": [item("p", [1])],
            "worth_reading": [item("w", [2])],
        },
        findings_index={
            1: {"url": None},
            2: {"url": None},
        },
    )
    drop_duplicate_stories(r)
    assert [i["headline"] for i in r.sections["patterns"]] == ["p"]
    assert [i["headline"] for i in r.sections["worth_reading"]] == ["w"]


def test_finding_id_not_in_index_does_not_crash():
    r = mk(
        {
            "patterns": [item("p", [999])],
        },
        findings_index={},
    )
    drop_duplicate_stories(r)
    assert [i["headline"] for i in r.sections["patterns"]] == ["p"]


def test_distinct_items_all_kept_order_preserved():
    r = mk(
        {
            "patterns": [item("p1", [1]), item("p2", [2])],
            "worth_reading": [item("w1", [3])],
        },
        findings_index={
            1: {"url": "https://x.com/1"},
            2: {"url": "https://x.com/2"},
            3: {"url": "https://x.com/3"},
        },
    )
    drop_duplicate_stories(r)
    assert [i["headline"] for i in r.sections["patterns"]] == ["p1", "p2"]
    assert [i["headline"] for i in r.sections["worth_reading"]] == ["w1"]


def test_returns_same_object_identity():
    r = mk({"patterns": [item("p", [1])]})
    out = drop_duplicate_stories(r)
    assert out is r
