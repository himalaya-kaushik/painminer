"""Ranking formula (rank.recurrence_score) and feature extraction (rank.cluster_features)."""

from datetime import datetime, timedelta, timezone

from painminer.pipeline.rank import recurrence_score, cluster_features


NOW = datetime(2026, 1, 15, tzinfo=timezone.utc)


def test_worked_example_one():
    assert abs(recurrence_score(3, 5, 8, True) - 18.7) < 0.05


def test_worked_example_two():
    assert abs(recurrence_score(1, 1, 30, True) - 10.5) < 0.05


def test_recent_flag_adds_exactly_two():
    with_recent = recurrence_score(2, 3, 5, True)
    without_recent = recurrence_score(2, 3, 5, False)
    assert with_recent - without_recent == 2.0


def test_mention_count_monotonic_and_log_capped():
    low = recurrence_score(1, 1, 8, False)
    high = recurrence_score(1, 1, 40, False)
    assert high >= low
    assert high - low < 3.0


def test_n_sources_adds_exactly_two_per_unit():
    a = recurrence_score(2, 1, 1, False)
    b = recurrence_score(3, 1, 1, False)
    assert b - a == 2.0


def test_n_days_adds_exactly_one_point_five_per_unit():
    a = recurrence_score(1, 2, 1, False)
    b = recurrence_score(1, 3, 1, False)
    assert b - a == 1.5


def test_zero_mentions_contributes_nothing():
    assert recurrence_score(0, 0, 0, False) == 0.0


def test_cluster_features_basic():
    last_seen = (NOW - timedelta(days=3)).isoformat()
    cluster = {
        "kind": "pain",
        "sources_seen": ["hn", "gh"],
        "days_seen": ["2026-01-01"],
        "mention_count": 3,
        "last_seen": last_seen,
    }
    f = cluster_features(cluster, now=NOW)
    assert f == {
        "n_sources": 2,
        "n_days": 1,
        "mention_count": 3,
        "recent": True,
        "kind": "pain",
    }


def test_cluster_features_not_recent():
    last_seen = (NOW - timedelta(days=30)).isoformat()
    cluster = {
        "kind": "pain",
        "sources_seen": ["hn"],
        "days_seen": ["2026-01-01"],
        "mention_count": 1,
        "last_seen": last_seen,
    }
    f = cluster_features(cluster, now=NOW)
    assert f["recent"] is False


def test_cluster_features_missing_fields_do_not_crash():
    cluster = {"kind": "pain", "sources_seen": None, "days_seen": [], "mention_count": None, "last_seen": None}
    f = cluster_features(cluster, now=NOW)
    assert f["n_sources"] == 0
    assert f["n_days"] == 0
    assert f["mention_count"] == 0
    assert f["recent"] is False
