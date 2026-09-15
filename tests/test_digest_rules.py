"""Offline tests asserting the digest-fix instructions are present in
painminer.prompt.SYNTHESIS_SYSTEM_PROMPT (brief fixes 2 & 3)."""

from __future__ import annotations

from painminer.prompt import SYNTHESIS_SYSTEM_PROMPT


def test_no_superlatives_rule_present():
    assert "No superlatives" in SYNTHESIS_SYSTEM_PROMPT


def test_banned_phrase_the_signal_is_clear_present():
    # The source wraps this phrase across a line break ("the signal is\n
    # clear"), so compare against whitespace-normalized text.
    normalized = " ".join(SYNTHESIS_SYSTEM_PROMPT.split())
    assert "the signal is clear" in normalized


def test_someone_built_cap_at_most_3_present():
    assert "at most 3" in SYNTHESIS_SYSTEM_PROMPT


def test_total_cap_at_most_8_present():
    assert "at most 8" in SYNTHESIS_SYSTEM_PROMPT


def test_do_not_tell_reader_what_to_conclude_present():
    assert "what to conclude" in SYNTHESIS_SYSTEM_PROMPT
