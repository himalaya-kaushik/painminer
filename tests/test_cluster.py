"""Pure clustering decision (cluster.decide): merge / tiebreak / new (§8)."""

from cluster import decide

MERGE = 0.90
TIEBREAK_LOW = 0.75


def test_none_is_new():
    assert decide(None, MERGE, TIEBREAK_LOW) == "new"


def test_below_tiebreak_low_is_new():
    assert decide(TIEBREAK_LOW - 0.01, MERGE, TIEBREAK_LOW) == "new"


def test_at_tiebreak_low_is_tiebreak():
    assert decide(TIEBREAK_LOW, MERGE, TIEBREAK_LOW) == "tiebreak"


def test_between_thresholds_is_tiebreak():
    assert decide(0.80, MERGE, TIEBREAK_LOW) == "tiebreak"


def test_at_merge_threshold_is_merge():
    assert decide(MERGE, MERGE, TIEBREAK_LOW) == "merge"


def test_above_merge_threshold_is_merge():
    assert decide(0.99, MERGE, TIEBREAK_LOW) == "merge"
