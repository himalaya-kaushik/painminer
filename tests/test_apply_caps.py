"""Offline tests for painminer.pipeline.synthesize.apply_caps (deterministic
digest size limits, brief fixes 2 & 3)."""

from __future__ import annotations

from painminer.pipeline.synthesize import SynthesisResult, apply_caps


def mk(p, w, sh, s):
    r = SynthesisResult()

    def items(n, tag):
        return [{"headline": f"{tag}{i}", "body": "", "finding_ids": []} for i in range(n)]

    r.sections = {
        "patterns": items(p, "p"),
        "worth_reading": items(w, "w"),
        "shipped": items(sh, "sh"),
        "someone_built": items(s, "s"),
    }
    return r


def test_someone_built_capped_independently():
    r = mk(0, 0, 0, 10)
    apply_caps(r, someone_built_cap=3, total_cap=100)
    assert len(r.sections["someone_built"]) == 3


def test_total_cap_order_patterns_worth_reading_shipped_someone_built():
    r = mk(4, 4, 4, 10)
    apply_caps(r, someone_built_cap=3, total_cap=8)
    assert len(r.sections["patterns"]) == 4
    assert len(r.sections["worth_reading"]) == 4
    assert len(r.sections["shipped"]) == 0
    assert len(r.sections["someone_built"]) == 0


def test_shipped_outranks_someone_built_when_total_cap_forces_a_cut():
    # patterns+worth_reading leave room for 2; shipped and someone_built both
    # want in — shipped (funded-lab launches) must survive first.
    r = mk(3, 3, 2, 3)
    apply_caps(r, someone_built_cap=3, total_cap=8)
    assert len(r.sections["patterns"]) == 3
    assert len(r.sections["worth_reading"]) == 3
    assert len(r.sections["shipped"]) == 2
    assert len(r.sections["someone_built"]) == 0


def test_total_cap_leaves_room_for_someone_built_when_under_budget():
    r = mk(2, 2, 0, 10)
    apply_caps(r, someone_built_cap=3, total_cap=8)
    assert len(r.sections["patterns"]) == 2
    assert len(r.sections["worth_reading"]) == 2
    assert len(r.sections["shipped"]) == 0
    assert len(r.sections["someone_built"]) == 3
    total = sum(len(r.sections[s]) for s in
               ("patterns", "worth_reading", "shipped", "someone_built"))
    assert total == 7


def test_patterns_alone_exceed_total_cap():
    r = mk(10, 0, 0, 0)
    apply_caps(r, someone_built_cap=3, total_cap=8)
    assert len(r.sections["patterns"]) == 8
    assert len(r.sections["worth_reading"]) == 0
    assert len(r.sections["shipped"]) == 0
    assert len(r.sections["someone_built"]) == 0


def test_empty_sections_do_not_crash_and_stay_empty():
    r = mk(0, 0, 0, 0)
    apply_caps(r, someone_built_cap=3, total_cap=8)
    assert r.sections["patterns"] == []
    assert r.sections["worth_reading"] == []
    assert r.sections["shipped"] == []
    assert r.sections["someone_built"] == []


def test_missing_shipped_key_does_not_crash():
    # A result built before the "shipped" section existed (or a section the
    # model omitted) has no "shipped" key at all — apply_caps must not KeyError.
    r = SynthesisResult()
    r.sections = {"patterns": [], "worth_reading": [], "someone_built": []}
    apply_caps(r, someone_built_cap=3, total_cap=8)
    assert r.sections.get("shipped") == []


def test_returns_same_object_identity():
    r = mk(1, 1, 1, 1)
    out = apply_caps(r, someone_built_cap=3, total_cap=8)
    assert out is r
