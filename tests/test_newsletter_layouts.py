"""Newsletter layout splitting (painminer.adapters.newsletter_layouts) — pure,
offline unit tests over synthetic fixtures shaped like real newsletters.

Covers:
  * DigestLayout (TLDR-style): story count/titles, sponsor + job-ad stories
    dropped, dividers/section-headers excluded from story text, footer
    excluded, every unit is a story whose text starts with its title.
  * BlockLayout (AlphaSignal-style): one unit per story block (title = line
    above the engagement line), sponsor block dropped, READ MORE stripped,
    the trailing Signals unit (excluding the sponsored entry and footer).
  * longform (catch-all): chunk size ceiling, sponsor section + its
    sub-headings dropped, boilerplate lines dropped, runt chunks merged
    instead of standing alone, no chunk of only headings, transcript turns
    split only at line boundaries, heading order preserved, breadcrumb
    titles.
  * detect_layout layout selection, including the digest/longform boundary
    (a read-time tag is required; a bare "(2023)"-style tag is not enough).
  * sanitize(): markdown links and bare URLs never survive into a unit.
  * normalize_title(): case/punctuation/read-time-tag insensitive dedupe key.
  * free_part(): Substack's stock paywall cut-off, generic across publications.
"""

import re

from painminer.adapters.newsletter_layouts import (
    detect_layout,
    free_part,
    normalize_title,
    sanitize,
    split_email,
    split_longform,
)

# --- fixtures ----------------------------------------------------------------

DIGEST_EMAIL = """TLDR AI
Together with Vantage

# Sponsor

**Some Product (Sponsor)**
Some product blurb sentence about a great product for engineers.

🧠

# **Articles & Tutorials**

**Model X Ships With Bigger Context Window (5 minute read)**
Researchers released Model X today with a much larger context window, enabling longer conversations and documents to be processed without truncation.

**Repo Y Trends On GitHub (4 minute read)**
Repo Y jumped to the top of GitHub trending this week after a viral post explained its novel approach to caching.

🎁

**New Chip Promises 10x Speedup (3 minute read)**
A startup announced a new chip architecture that claims a tenfold speedup over existing accelerators for transformer inference workloads.

**

# **Quick Links**

**Awesome-Tools (GitHub Repo)**
A curated list of tools for building with large language models, updated daily by the community.

**Cool AI Directory (Website)**
A directory site cataloguing hundreds of AI tools across categories like writing, coding, and image generation.

**Startup Raises Funding For New Data Platform (6 minute read)**
A startup building a new data platform announced a funding round today to expand its engineering team and go after enterprise customers.

# **Careers**

**TLDR Curator (TLDR Curator, ~5 hrs/week)**
Help curate TLDR AI. Flexible hours, remote work, small stipend.

Love TLDR? Tell your friends and get rewarded with TLDR swag!
"""

BLOCKS_EMAIL = """Hey friend,
Here's what's trending today.

In today's issue:
- Model Q launches
- Robot Corp raises funding
- Signals

**Model Q Launches New Assistant**
33,404 Likes
Model Q released a new coding assistant today that can refactor entire repositories in one pass, according to early testers.
READ MORE
forward →

Presented by Acme Corp
Acme Corp helps you ship faster with our new observability suite built for AI teams.
partner with us →

**Robot Corp Raises Series B**
12,204 Likes
Robot Corp announced a new funding round to build household robots capable of folding laundry and loading dishwashers.
READ MORE
forward →

Signals
1. Startup Zeta ships new API
2,304 Likes
2. Framework Omega hits 1.0
981 Downloads
3. Widget Co unveils gadget
Presented by Widget Co
At Alpha Signal, our mission is to keep engineers ahead of the curve.
"""

_TRANSCRIPT_TURNS = [
    f"**00:0{i % 10}:0{i % 10} Host:** This is turn number {i} of the transcript, "
    f"and it talks at reasonable length about post-training loops, evaluation "
    f"harnesses, and the general shape of the argument being made in this section. "
    f"It stays under the line ceiling on its own but the section overall runs long. ({i})"
    for i in range(12)
]
_TRANSCRIPT = "\n".join(_TRANSCRIPT_TURNS)

LONGFORM_EMAIL = f"""Forwarded this email? Subscribe here for more
READ IN APP

# The Loop That Shipped Twice

## 1. What Changed

RSI in post-training has quietly become the loop that already shipped, and this section explains the mechanism in plain terms for readers who have not been following the papers closely.

Listen to the episode: [Podcast
Title Here
0:00](https://substack.com/app-link/post?token=SECRET12345)

## 2. Why It Matters

The mechanism matters because it changes how teams should think about iteration speed, and this paragraph spells out the practical implications for a reader who ships models.

### From our partners: Acme

Acme sponsor pitch paragraph that must be dropped entirely from the output along with everything nested under this heading.

[**[Webinar] Foo (Sponsor)** ](https://x.y/z)

#### Why Acme Rocks

Even more sponsor text nested under the sponsor heading that must not leak into any kept unit either, no matter how long it runs on.

## 3. Transcript

{_TRANSCRIPT}

## 4. Wrap-up

That's all for today.

Like
Comment
Restack
"""

_MAX_CHARS = 1000
_MIN_CHARS = 200


def _longform_units():
    return split_email(LONGFORM_EMAIL, max_chars=_MAX_CHARS, min_chars=_MIN_CHARS)


# --- DigestLayout --------------------------------------------------------


def test_digest_story_count_and_titles():
    units = split_email(DIGEST_EMAIL)
    titles = [u.title for u in units]
    assert titles == [
        "Model X Ships With Bigger Context Window",
        "Repo Y Trends On GitHub",
        "New Chip Promises 10x Speedup",
        "Awesome-Tools",
        "Cool AI Directory",
        "Startup Raises Funding For New Data Platform",
    ]


def test_digest_drops_sponsor_and_job_ad():
    units = split_email(DIGEST_EMAIL)
    full = "\n".join(u.text for u in units)
    assert "Some Product" not in full
    assert "Some product blurb" not in full
    assert "TLDR Curator" not in full
    assert "hrs/week" not in full


def test_digest_dividers_and_headers_excluded_from_story_text():
    units = split_email(DIGEST_EMAIL)
    full = "\n".join(u.text for u in units)
    assert "Articles & Tutorials" not in full
    assert "Quick Links" not in full
    assert "Careers" not in full
    assert "🧠" not in full and "🎁" not in full
    # a lone "**" divider carries no words and must not appear either
    assert not any(u.text.strip() == "**" for u in units)


def test_digest_footer_excluded():
    units = split_email(DIGEST_EMAIL)
    full = "\n".join(u.text for u in units)
    assert "Love TLDR" not in full


def test_digest_units_are_stories_and_start_with_title():
    units = split_email(DIGEST_EMAIL)
    assert len(units) == 6
    for u in units:
        assert u.story is True
        assert u.text.startswith(u.title)


# --- detect_layout / digest-longform boundary -----------------------------


def test_detect_layout_digest_blocks_longform():
    assert detect_layout(DIGEST_EMAIL.split("\n")) == "digest"
    assert detect_layout(BLOCKS_EMAIL.split("\n")) == "blocks"
    assert detect_layout(sanitize(LONGFORM_EMAIL).split("\n")) == "longform"


def test_detect_layout_essay_with_year_tags_is_not_digest():
    lines = [
        "**Attention Is All You Need (2017)**",
        "**GPT-3 (2020)**",
        "**PaLM (2022)**",
        "**Chinchilla (2022)**",
        "**LLaMA (2023)**",
        "This essay walks through five landmark papers and what each one changed.",
    ]
    assert detect_layout(lines) == "longform"


# --- BlockLayout -----------------------------------------------------------


def test_blocks_one_unit_per_story_title_above_likes_line():
    units = split_email(BLOCKS_EMAIL)
    stories = [u for u in units if u.story]
    assert [u.title for u in stories] == [
        "Model Q Launches New Assistant",
        "Robot Corp Raises Series B",
    ]
    # greeting/TOC never leak into a title or story text
    full = "\n".join(u.text for u in units)
    assert "Hey friend" not in full
    assert "In today's issue" not in full


def test_blocks_read_more_removed():
    units = split_email(BLOCKS_EMAIL)
    for u in units:
        assert "READ MORE" not in u.text
        assert "read more" not in u.text.lower()


def test_blocks_sponsor_block_dropped():
    units = split_email(BLOCKS_EMAIL)
    full = "\n".join(u.text for u in units)
    assert "Acme Corp" not in full
    assert "partner with us" not in full.lower()


def test_blocks_signals_unit_present_excludes_sponsored_entry_and_footer():
    units = split_email(BLOCKS_EMAIL)
    signals = [u for u in units if u.title == "Signals"]
    assert len(signals) == 1
    unit = signals[0]
    assert unit.story is False
    assert "Startup Zeta" in unit.text
    assert "Framework Omega" in unit.text
    assert "Widget Co" not in unit.text            # the sponsored entry
    assert "Presented by" not in unit.text
    assert "our mission" not in unit.text.lower()  # the footer


# --- longform ----------------------------------------------------------


def test_longform_units_within_max_chars():
    units = _longform_units()
    assert len(units) > 1
    for u in units:
        assert len(u.text) <= _MAX_CHARS


def test_longform_sponsor_section_and_subheadings_dropped():
    units = _longform_units()
    full = "\n".join(u.text for u in units)
    assert "From our partners" not in full
    assert "Acme" not in full
    assert "Why Acme Rocks" not in full
    assert "Sponsor" not in full   # the nested [Webinar] (Sponsor) link, dropped with the section


def test_longform_boilerplate_lines_dropped():
    units = _longform_units()
    full = "\n".join(u.text for u in units)
    for boilerplate in ("Forwarded this email", "READ IN APP", "Like", "Comment", "Restack"):
        assert not any(line.strip() == boilerplate for u in units for line in u.text.split("\n"))
    assert full.strip() != ""


def test_longform_no_runt_chunks():
    heading_re = re.compile(r"^#{1,6}\s")
    for u in _longform_units():
        prose_chars = sum(len(ln) for ln in u.text.split("\n") if not heading_re.match(ln))
        assert prose_chars >= 150


def test_longform_no_chunk_is_only_headings():
    heading_re = re.compile(r"^#{1,6}\s")
    for u in _longform_units():
        lines = [ln for ln in u.text.split("\n") if ln.strip()]
        assert not all(heading_re.match(ln) for ln in lines)


def test_longform_transcript_turns_split_only_at_line_boundaries():
    units = _longform_units()
    for turn in _TRANSCRIPT_TURNS:
        assert len(turn) < _MAX_CHARS
        containing = [u for u in units if turn in u.text]
        assert len(containing) == 1, f"turn should appear intact in exactly one unit: {turn[:40]!r}"


def test_longform_heading_order_preserved_across_units():
    units = _longform_units()
    heading_re = re.compile(r"(?m)^#{1,6}\s.*$")
    joined = "\n---\n".join(u.text for u in units)
    headings_in_output = heading_re.findall(joined)
    # every heading kept in the output appears in the same relative order as
    # in the (sanitized) source document
    sanitized_source = sanitize(LONGFORM_EMAIL)
    positions = [sanitized_source.find(h) for h in headings_in_output]
    assert all(p != -1 for p in positions)
    assert positions == sorted(positions)


def test_longform_titles_are_breadcrumbs():
    units = _longform_units()
    for u in units:
        crumbs = u.title.split(" › ")
        assert crumbs[0] == "The Loop That Shipped Twice"
    # a deeper chunk carries a multi-part breadcrumb
    assert any(" › " in u.title for u in units)


# --- sanitize / leaks ----------------------------------------------------


def test_sanitize_strips_links_and_bare_urls():
    text = ("Check out [Cool Tool](https://example.com/x?token=abc) and also see "
            "https://bare.example.com/y for more.")
    out = sanitize(text)
    assert "Cool Tool" in out
    assert "http" not in out
    assert "token=" not in out
    assert "](" not in out


def test_split_email_never_leaks_links_or_tokens():
    for email in (DIGEST_EMAIL, BLOCKS_EMAIL, LONGFORM_EMAIL):
        for u in split_email(email, max_chars=_MAX_CHARS, min_chars=_MIN_CHARS):
            assert "http" not in u.text
            assert "token=" not in u.text
            assert "](" not in u.text


# --- normalize_title -------------------------------------------------------


def test_normalize_title_case_punctuation_and_tag_insensitive():
    assert normalize_title("Introducing Grok 4.7 (3 minute read)") == normalize_title(
        "introducing grok 4.7"
    )


# --- free_part (Substack paywall cut-off) -----------------------------


def test_free_part_cuts_at_unlock_the_rest_marker_any_publication():
    for pub in ("My Pub", "The Sequence", "Another Newsletter"):
        body = (
            f"# Essay Title\n\n## Section One\n\n{'A' * 200}\n\n"
            f"## Subscribe to {pub} to unlock the rest.\n\n"
            "This is paywalled content that must never appear in any unit."
        )
        units = split_email(body, max_chars=1000, min_chars=50)
        full = "\n".join(u.text for u in units)
        assert "paywalled" not in full.lower()
        assert "unlock the rest" not in full.lower()
        assert "A" * 200 in full


def test_free_part_cuts_at_continue_reading_marker():
    body = (
        "Some intro line with enough words to be real content here in this "
        "fixture so it clears the prose floor on its own without help.\n\n"
        "Continue reading this post for free in the Substack app\n\n"
        "More paywalled stuff that must not appear in any unit whatsoever."
    )
    lines = sanitize(body).split("\n")
    kept = free_part(lines)
    joined = "\n".join(kept)
    assert "intro line" in joined
    assert "paywalled stuff" not in joined
    assert "Continue reading" not in joined


def test_free_part_unchanged_when_no_marker_present():
    body = "No paywall marker here.\nJust normal content that keeps going for a while as usual.\n"
    lines = sanitize(body).split("\n")
    assert free_part(lines) == lines


def test_split_longform_direct_call_matches_split_email():
    # split_longform is exercised both directly (per its docstring/contract)
    # and indirectly through split_email's dispatch.
    direct = split_longform(sanitize(LONGFORM_EMAIL).split("\n"), _MAX_CHARS, _MIN_CHARS)
    via_split_email = _longform_units()
    assert [u.text for u in direct] == [u.text for u in via_split_email]
