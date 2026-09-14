"""Digest rendering pure functions (digest._visible, _new_this_week, format_card)
and the actions.DISPATCH table shape. No DB/LLM/telegram calls.
"""

from datetime import datetime, timedelta, timezone

from painminer.delivery import actions
from painminer.delivery import digest
from painminer.delivery.digest import Card


def test_visible_not_muted_not_pinned():
    assert digest._visible({"muted": False, "pinned": False}) is True


def test_visible_muted_is_hidden():
    assert digest._visible({"muted": True, "pinned": False}) is False


def test_visible_pinned_same_count_is_hidden():
    cluster = {"muted": False, "pinned": True, "mention_count": 5, "pinned_mention_count": 5}
    assert digest._visible(cluster) is False


def test_visible_pinned_resurfaced_is_shown():
    cluster = {"muted": False, "pinned": True, "mention_count": 6, "pinned_mention_count": 5}
    assert digest._visible(cluster) is True


def test_visible_pinned_missing_pinned_count_defaults_to_zero():
    cluster = {"muted": False, "pinned": True, "mention_count": 1, "pinned_mention_count": None}
    assert digest._visible(cluster) is True


def test_new_this_week_none_is_zero():
    assert digest._new_this_week(None) == 0


def test_new_this_week_empty_is_zero():
    assert digest._new_this_week([]) == 0


def test_new_this_week_counts_recent_dates():
    today = datetime.now(tz=timezone.utc).date()
    recent_3 = (today - timedelta(days=3)).isoformat()
    old_30 = (today - timedelta(days=30)).isoformat()
    days_seen = [today.isoformat(), recent_3, old_30]
    assert digest._new_this_week(days_seen) == 2


def test_new_this_week_today_counts():
    today = datetime.now(tz=timezone.utc).date().isoformat()
    assert digest._new_this_week([today]) == 1


def test_new_this_week_thirty_days_ago_does_not_count():
    old_30 = (datetime.now(tz=timezone.utc).date() - timedelta(days=30)).isoformat()
    assert digest._new_this_week([old_30]) == 0


def test_format_card_full():
    card = Card(
        cluster_id=7,
        kind="pain",
        statement="X manual step",
        why="matters",
        quote="verbatim q",
        url="http://u",
        mention_count=4,
        n_sources=2,
        new_this_week=1,
    )
    out = digest.format_card(card)
    assert digest.KIND_EMOJI["pain"] in out
    assert "X manual step" in out
    assert "matters" in out
    assert '"verbatim q"' in out
    assert "4 mentions" in out
    assert "2 sources" in out
    assert "1 new this week" in out
    assert "#7" in out


def test_format_card_omits_missing_why_and_quote():
    card = Card(
        cluster_id=9,
        kind="build",
        statement="Y statement",
        why=None,
        quote=None,
        url=None,
        mention_count=0,
        n_sources=0,
        new_this_week=0,
    )
    out = digest.format_card(card)
    assert "Y statement" in out
    assert '"' not in out
    assert "0 mentions" in out
    assert "0 sources" in out
    assert "0 new this week" in out
    assert "#9" in out


def test_dispatch_keys_and_callable():
    assert set(actions.DISPATCH.keys()) == {"fire", "mute", "pin"}
    assert all(callable(fn) for fn in actions.DISPATCH.values())
