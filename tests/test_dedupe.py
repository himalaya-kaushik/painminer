"""Structural dedupe decision (dedupe.classify) and helpers."""

from painminer.adapters.base import content_hash
from painminer.pipeline.dedupe import classify, MIN_CHARS, _chunks, IN_CHUNK


LONG = "Solo freelancers manually copy Stripe payouts into spreadsheets weekly."


def test_ready_for_substantive_new_text():
    assert classify(LONG, content_hash(LONG), set()) == ("ready", "ok")


def test_too_short_is_duplicate():
    assert classify("too short", None, set())[0] == "duplicate"
    assert classify("too short", None, set())[1] == "too_short"


def test_boundary_length():
    just_under = "x" * (MIN_CHARS - 1)
    just_over = "y" * MIN_CHARS
    assert classify(just_under, None, set())[0] == "duplicate"
    assert classify(just_over, None, set())[0] == "ready"


def test_link_only_is_duplicate():
    # Long enough to clear the too-short check, so link_only is what fires.
    url = "https://example.com/some/really/long/article/path/exceeding-forty-chars"
    assert classify(url, "h", set()) == ("duplicate", "link_only")


def test_url_with_words_is_not_link_only():
    text = "check this out, it changed how I think: https://example.com/x and more"
    assert classify(text, "h", set())[0] == "ready"


def test_content_hash_duplicate():
    h = content_hash(LONG)
    assert classify(LONG, h, {h}) == ("duplicate", "content_hash")


def test_whitespace_normalized_hash_matches():
    a = content_hash("Hello   World")
    b = content_hash("hello world")
    assert a == b  # case + whitespace normalized


def test_chunks_splits_without_loss():
    items = list(range(250))
    chunks = list(_chunks(items, IN_CHUNK))
    assert all(len(c) <= IN_CHUNK for c in chunks)
    assert [x for c in chunks for x in c] == items   # order + completeness
    assert len(chunks) == 3                            # 100 + 100 + 50


def test_chunks_empty():
    assert list(_chunks([], IN_CHUNK)) == []
