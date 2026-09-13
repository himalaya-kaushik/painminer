"""Per-thread finding cap (judge.cap_for_thread, thread_key)."""

from judge import cap_for_thread, thread_key, MAX_FINDINGS_PER_THREAD


def test_cap_is_two():
    assert MAX_FINDINGS_PER_THREAD == 2


def test_full_allowance_for_fresh_thread():
    assert cap_for_thread({}, "t1", 3) == 2       # capped down to 2
    assert cap_for_thread({}, "t1", 1) == 1       # fewer than cap -> all


def test_partial_allowance():
    assert cap_for_thread({"t1": 1}, "t1", 3) == 1
    assert cap_for_thread({"t1": 1}, "t1", 1) == 1


def test_no_allowance_when_full():
    assert cap_for_thread({"t1": 2}, "t1", 5) == 0
    assert cap_for_thread({"t1": 3}, "t1", 5) == 0   # never negative


def test_threads_are_independent():
    counts = {"t1": 2}
    assert cap_for_thread(counts, "t2", 2) == 2


def test_thread_key_prefers_thread_id():
    assert thread_key({"id": 7, "thread_id": "99"}) == "99"


def test_thread_key_falls_back_to_item_id():
    key = thread_key({"id": 7, "thread_id": None})
    assert key == "_item:7"
    # two thread-less items must not collide
    assert thread_key({"id": 8, "thread_id": None}) != key
