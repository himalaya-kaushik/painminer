"""Digest rendering (delivery.document) — pure formatting + file IO, no DB."""

import datetime
import shutil
import tempfile
from pathlib import Path

from painminer.delivery.document import headlines, render_markdown, write_digest
from painminer.pipeline.synthesize import SynthesisResult


def _result(sections=None, night_summary="", findings_index=None):
    return SynthesisResult(
        night_summary=night_summary,
        sections=sections or {"patterns": [], "worth_reading": [], "someone_built": []},
        findings_index=findings_index or {},
    )


def _item(headline, body, finding_ids):
    return {"headline": headline, "body": body, "finding_ids": finding_ids}


def test_render_markdown_heading_and_summary():
    result = _result(night_summary="Quiet night. Nothing much.")
    md = render_markdown(result, day=datetime.date(2026, 9, 15))
    assert md.startswith("# 15 September 2026")
    assert "Quiet night. Nothing much." in md


def test_render_markdown_only_nonempty_sections_get_headers():
    result = _result(
        night_summary="Summary.",
        sections={
            "patterns": [_item("P headline", "P body", [])],
            "worth_reading": [],
            "someone_built": [],
        },
    )
    md = render_markdown(result, day=datetime.date(2026, 9, 15))
    assert "## Patterns" in md
    assert "## Worth reading" not in md
    assert "## Someone built" not in md


def test_render_markdown_sequential_numbering_across_sections():
    result = _result(
        sections={
            "patterns": [_item("P1", "b1", []), _item("P2", "b2", [])],
            "worth_reading": [_item("W1", "b3", [])],
            "shipped": [_item("SH1", "b5", [])],
            "someone_built": [_item("S1", "b4", [])],
        },
    )
    md = render_markdown(result, day=datetime.date(2026, 9, 15))
    assert "### 1. P1" in md
    assert "### 2. P2" in md
    assert "### 3. W1" in md
    assert "### 4. SH1" in md   # shipped sits between worth_reading and someone_built
    assert "### 5. S1" in md
    assert "b1" in md and "b2" in md and "b3" in md and "b4" in md and "b5" in md


def test_render_markdown_shipped_section_header_and_missing_key_safe():
    # A result with no "shipped" key at all (e.g. built before this section
    # existed) must render fine with no "## Shipped" header.
    result = _result(
        sections={"patterns": [], "worth_reading": [], "someone_built": []},
    )
    md = render_markdown(result, day=datetime.date(2026, 9, 15))
    assert "## Shipped" not in md

    result2 = _result(
        sections={
            "patterns": [], "worth_reading": [],
            "shipped": [_item("A funded lab shipped a new model", "body", [])],
            "someone_built": [],
        },
    )
    md2 = render_markdown(result2, day=datetime.date(2026, 9, 15))
    assert "## Shipped" in md2
    assert "### 1. A funded lab shipped a new model" in md2


def test_render_markdown_links_dedupe_and_resolve():
    index = {
        1: {"url": "http://a", "cluster_id": None},
        2: {"url": "http://a", "cluster_id": None},   # duplicate url
        3: {"url": "http://b", "cluster_id": None},
    }
    result = _result(
        sections={
            "patterns": [_item("P1", "b1", [1, 2, 3])],
            "worth_reading": [],
            "someone_built": [],
        },
        findings_index=index,
    )
    md = render_markdown(result, day=datetime.date(2026, 9, 15))
    assert "[source](http://a)" in md
    assert "[source](http://b)" in md
    assert md.count("http://a") == 1


def test_render_markdown_all_empty_sections_with_summary():
    result = _result(night_summary="Quiet night.")
    md = render_markdown(result, day=datetime.date(2026, 9, 15))
    assert md.startswith("# 15 September 2026")
    assert "Quiet night." in md
    assert "## Patterns" not in md
    assert "## Worth reading" not in md
    assert "## Someone built" not in md


def test_headlines_numbering_and_limit():
    result = _result(
        sections={
            "patterns": [_item("P1", "b1", []), _item("P2", "b2", [])],
            "worth_reading": [_item("W1", "b3", [])],
            "someone_built": [_item("S1", "b4", [])],
        },
    )
    hs = headlines(result, limit=2)
    assert len(hs) == 2
    assert [h.n for h in hs] == [1, 2]
    assert [h.headline for h in hs] == ["P1", "P2"]


def test_headlines_cluster_id_and_url_resolution():
    index = {
        1: {"url": None, "cluster_id": None},
        2: {"url": "http://x", "cluster_id": 42},
    }
    result = _result(
        sections={
            "patterns": [_item("P1", "b1", [1, 2])],
            "worth_reading": [],
            "someone_built": [],
        },
        findings_index=index,
    )
    hs = headlines(result, limit=5)
    assert len(hs) == 1
    assert hs[0].cluster_id == 42
    assert hs[0].url == "http://x"


def test_write_digest_roundtrip():
    tmp_dir = Path(tempfile.mkdtemp(prefix="painminer_test_digest_"))
    try:
        path = write_digest("hello digest", day=datetime.date(2026, 9, 15), digests_dir=str(tmp_dir))
        assert path == tmp_dir / "2026-09-15.md"
        assert path.read_text(encoding="utf-8") == "hello digest"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
