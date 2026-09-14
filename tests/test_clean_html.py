"""clean_html: HN HTML -> plain text (adapters/base.clean_html)."""

from painminer.adapters.base import clean_html


def test_decodes_entities():
    assert clean_html("don&#x27;t &amp; won&#x27;t") == "don't & won't"
    assert clean_html("a &gt; b &lt; c") == "a > b < c"


def test_block_tags_become_newlines():
    assert clean_html("one<p>two") == "one\ntwo"
    assert clean_html("a<br>b") == "a\nb"


def test_anchor_inner_text_kept():
    out = clean_html('see <a href="https://x.com/y" rel="nofollow">https://x.com/y</a>')
    assert out == "see https://x.com/y"


def test_slash_entity_from_algolia():
    # HN encodes '/' as &#x2F; inside URLs.
    assert clean_html("prompt&#x2F;review&#x2F;push") == "prompt/review/push"


def test_collapses_excess_blank_lines_and_strips():
    assert clean_html("  a<p><p><p>b  ") == "a\n\nb"


def test_plain_text_untouched():
    assert clean_html("just a normal comment") == "just a normal comment"
