"""NewslettersAdapter (painminer.adapters.newsletters) — hermetic tests over a
fake vault built with tempfile.TemporaryDirectory(); no network, no database.

Covers:
  * parse_frontmatter: basic, wrapped subject, escaped quotes, no frontmatter.
  * parse_date / publication_name helpers.
  * windowing: day-folder window by lookback_days (today and lookback_days
    before, including future-dated folders), since_ts ignored entirely,
    until_ts exclusive on email date, old day folders never listed, rerun
    idempotence.
  * exclude_senders.
  * cross-newsletter same-day dedupe by normalized story title; different day
    stays separate.
  * story/chunk id shapes, determinism across repeated iter_items calls, and
    global uniqueness.
  * FetchedItem field shape: url/thread_id None, metadata keys, raw_text
    header prefix, content_hash independent of the header, created_at_i.
  * chunk header: "part i of n" and no repeated subject when the breadcrumb
    equals the subject.
  * failure isolation: unset/missing vault dir raises; a bad-UTF8 file or a
    frontmatter-less file is skipped, not fatal.
  * one page per email.
  * registry resolution.
"""

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from painminer.adapters import get_adapter
from painminer.adapters.base import content_hash
from painminer.adapters.newsletter_layouts import split_email
from painminer.adapters.newsletters import (
    NewslettersAdapter,
    parse_date,
    parse_frontmatter,
    publication_name,
)

ENV = "VAULT_NEWSLETTERS_DIR"

# A single paragraph long enough to clear newsletter_layouts._MIN_PROSE (150
# chars of non-heading prose), so a plain longform test email survives.
LONG_BODY = (
    "Body text with enough length to survive the minimum prose filter that the "
    "longform splitter applies before it decides a chunk is worth keeping at "
    "all, since short chunks are treated as noise and dropped from the output."
)


def _digest_body(title, blurb, tag="5 minute read", others=(("Second Story Here", "4 minute read",
                                                              "Second story blurb paragraph text goes here for padding."),
                                                             ("Third Story Here", "3 minute read",
                                                              "Third story blurb paragraph text goes here for padding."))):
    lines = ["Some Newsletter", "Together with Sponsor Co", "", f"**{title} ({tag})**", blurb, ""]
    for other_title, other_tag, other_blurb in others:
        lines += [f"**{other_title} ({other_tag})**", other_blurb, ""]
    lines.append("Love TLDR? Tell your friends and get rewarded with swag!")
    return "\n".join(lines)


def _write(root, day, fname, *, sender, subject, date_iso, message_id, body=LONG_BODY):
    d = Path(root) / day
    d.mkdir(parents=True, exist_ok=True)
    fm = (f'---\nsender: "{sender}"\nsubject: "{subject}"\n'
          f'date: "{date_iso}"\nmessage_id: "{message_id}"\n---\n\n')
    (d / fname).write_text(fm + body + "\n", encoding="utf-8")


def _adapter(root, config=None, now=1790100000):
    cfg = dict(config or {})
    a = NewslettersAdapter({"name": "newsletters", "config_json": cfg}, None)
    a.now = now
    return a


class _EnvVault:
    """Context manager: a temp vault dir, exported as VAULT_NEWSLETTERS_DIR."""

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        os.environ[ENV] = self.root
        return self.root

    def __exit__(self, *exc):
        os.environ.pop(ENV, None)
        self._tmp.cleanup()


def _collect(adapter, since_ts=0, until_ts=None):
    return [it for page in adapter.iter_items(since_ts, until_ts) for it in page]


# --- parse_frontmatter -----------------------------------------------------


def test_parse_frontmatter_basic():
    text = ('---\nsender: "TLDR AI <dan@tldrnewsletter.com>"\nsubject: "Hello"\n'
            'date: "2026-09-22T11:15:37Z"\nmessage_id: "<abc@example.com>"\n---\n\nBody here.\n')
    fm, body = parse_frontmatter(text)
    assert fm["sender"] == "TLDR AI <dan@tldrnewsletter.com>"
    assert fm["subject"] == "Hello"
    assert fm["date"] == "2026-09-22T11:15:37Z"
    assert fm["message_id"] == "<abc@example.com>"
    assert body == "Body here.\n"


def test_parse_frontmatter_wrapped_subject():
    text = ('---\nsender: "TLDR AI <dan@tldrnewsletter.com>"\n'
            'subject: "The Sequence - Issue 937: RSI in Post-Training: The Loop That\n'
            'Already Shipped"\ndate: "2026-09-22T11:15:37Z"\n'
            'message_id: "<abc@example.com>"\n---\n\nBody.\n')
    fm, _ = parse_frontmatter(text)
    assert fm["subject"] == "The Sequence - Issue 937: RSI in Post-Training: The Loop That Already Shipped"


def test_parse_frontmatter_escaped_quotes():
    text = ('---\nsender: "Foo \\"Bar\\" <foo@example.com>"\n'
            'subject: "A \\"quoted\\" subject"\ndate: "2026-09-22T11:15:37Z"\n'
            'message_id: "<x@example.com>"\n---\nBody\n')
    fm, _ = parse_frontmatter(text)
    assert fm["sender"] == 'Foo "Bar" <foo@example.com>'
    assert fm["subject"] == 'A "quoted" subject'


def test_parse_frontmatter_no_frontmatter():
    text = "no frontmatter here\njust body"
    assert parse_frontmatter(text) == ({}, text)


# --- parse_date / publication_name -----------------------------------------


def test_parse_date_z_suffix():
    ts = parse_date("2026-09-22T11:15:37Z")
    assert ts == int(datetime(2026, 9, 22, 11, 15, 37, tzinfo=timezone.utc).timestamp())


def test_parse_date_bad_value_returns_none():
    assert parse_date("not-a-real-date") is None
    assert parse_date(None) is None
    assert parse_date("") is None


def test_publication_name_variants():
    assert publication_name("TLDR AI <dan@tldrnewsletter.com>", "fallback") == "TLDR AI"
    assert publication_name("bare@example.com", "fallback") == "bare"
    assert publication_name("", "fallback") == "fallback"


# --- windowing ---------------------------------------------------------


def test_windowing_lower_bound_and_until_exclusive():
    with _EnvVault() as root:
        now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc).timestamp()
        until_ts = 1790000000  # 2026-09-21T14:13:20Z
        until_iso = datetime.fromtimestamp(until_ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        before_until_iso = datetime.fromtimestamp(until_ts - 1, tz=timezone.utc).isoformat().replace("+00:00", "Z")

        _write(root, "2026-09-21", "a--before-until.md", sender="A <a@x.com>", subject="Before until",
               date_iso=before_until_iso, message_id="<w1@x.com>")
        _write(root, "2026-09-21", "a--at-until.md", sender="A <a@x.com>", subject="At until",
               date_iso=until_iso, message_id="<w2@x.com>")
        _write(root, "2026-09-20", "a--too-old-folder.md", sender="A <a@x.com>", subject="Too old folder",
               date_iso="2026-09-20T23:59:59Z", message_id="<w3@x.com>")

        # lookback_days=2 on today=2026-09-23 -> first_day=2026-09-21, so the
        # 09-20 folder is out on folder grounds alone, and of the two 09-21
        # emails only the one strictly before until_ts survives.
        adapter = _adapter(root, {"lookback_days": 2}, now=now)
        subjects = {it.metadata["subject"] for it in _collect(adapter, until_ts=until_ts)}
        assert subjects == {"Before until"}


def test_windowing_lookback_days_reads_expected_folders():
    with _EnvVault() as root:
        now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc).timestamp()
        for day, subject in [
            ("2026-09-20", "Too old"),
            ("2026-09-21", "Day 21"),
            ("2026-09-22", "Day 22"),
            ("2026-09-23", "Day 23 (today)"),
            ("2026-09-24", "Day 24 (future)"),
        ]:
            _write(root, day, "a--issue.md", sender="A <a@x.com>", subject=subject,
                   date_iso=f"{day}T00:00:00Z", message_id=f"<{day}@x.com>")

        adapter = _adapter(root, {"lookback_days": 2}, now=now)
        subjects = {it.metadata["subject"] for it in _collect(adapter)}
        # today, lookback_days before it, and even future-dated folders are
        # read; only the folder before the window is excluded
        assert subjects == {"Day 21", "Day 22", "Day 23 (today)", "Day 24 (future)"}


def test_windowing_since_ts_has_no_effect():
    with _EnvVault() as root:
        now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc).timestamp()
        _write(root, "2026-09-22", "a--issue.md", sender="A <a@x.com>", subject="Issue",
               date_iso="2026-09-22T00:00:00Z", message_id="<w1@x.com>")

        adapter_zero = _adapter(root, {"lookback_days": 2}, now=now)
        ids_zero = [it.source_id for it in _collect(adapter_zero, since_ts=0)]
        adapter_now = _adapter(root, {"lookback_days": 2}, now=now)
        ids_now = [it.source_id for it in _collect(adapter_now, since_ts=int(now))]

        assert ids_zero == ids_now
        assert len(ids_zero) == 1


def test_windowing_lookback_days_zero_reads_only_today():
    with _EnvVault() as root:
        now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc).timestamp()
        _write(root, "2026-09-22", "a--yesterday.md", sender="A <a@x.com>", subject="Yesterday",
               date_iso="2026-09-22T00:00:00Z", message_id="<w1@x.com>")
        _write(root, "2026-09-23", "a--today.md", sender="A <a@x.com>", subject="Today",
               date_iso="2026-09-23T00:00:00Z", message_id="<w2@x.com>")

        adapter = _adapter(root, {"lookback_days": 0}, now=now)
        subjects = {it.metadata["subject"] for it in _collect(adapter)}
        assert subjects == {"Today"}


def test_windowing_old_day_folders_not_listed_and_no_error():
    with _EnvVault() as root:
        now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc).timestamp()
        _write(root, "2026-09-22", "a--in-window.md", sender="A <a@x.com>", subject="In window",
               date_iso="2026-09-22T00:00:00Z", message_id="<w1@x.com>")
        # a garbage, unparseable file tucked into a folder well before the
        # window: the folder must never even be listed, and nothing must raise
        garbage_dir = Path(root) / "2020-01-01"
        garbage_dir.mkdir(parents=True)
        (garbage_dir / "garbage--old.md").write_text("not frontmatter\ngarbage nonsense\n", encoding="utf-8")

        adapter = _adapter(root, {"lookback_days": 2}, now=now)
        items = _collect(adapter)
        assert [it.metadata["subject"] for it in items] == ["In window"]

        listed = adapter._files(Path(root), "2026-09-21")
        assert all("2020-01-01" not in str(p) for p in listed)


def test_windowing_rerun_yields_identical_source_ids():
    with _EnvVault() as root:
        now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc).timestamp()
        _write(root, "2026-09-22", "a--issue.md", sender="A <a@x.com>", subject="Issue",
               date_iso="2026-09-22T00:00:00Z", message_id="<w1@x.com>",
               body=_digest_body("Some Story Title", "Blurb.", "5 minute read"))

        adapter1 = _adapter(root, {"lookback_days": 2}, now=now)
        ids1 = [it.source_id for it in _collect(adapter1)]
        adapter2 = _adapter(root, {"lookback_days": 2}, now=now)
        ids2 = [it.source_id for it in _collect(adapter2)]

        # re-reading an already-processed folder produces the same
        # deterministic source_ids, so a rerun inserts nothing new
        assert ids1 == ids2
        assert ids1


# --- exclude_senders ---------------------------------------------------


def test_exclude_senders_skips_matching_files():
    with _EnvVault() as root:
        _write(root, "2026-09-20", "spamsender--issue.md", sender="Spam <s@x.com>", subject="Spammy",
               date_iso="2026-09-20T00:00:00Z", message_id="<s1@x.com>")
        _write(root, "2026-09-20", "goodsender--issue.md", sender="Good <g@x.com>", subject="Good",
               date_iso="2026-09-20T00:00:00Z", message_id="<g1@x.com>")
        adapter = _adapter(root, {"exclude_senders": ["spamsender"]})
        items = _collect(adapter)
        assert [it.metadata["subject"] for it in items] == ["Good"]


# --- cross-newsletter dedupe --------------------------------------------


def test_cross_newsletter_dedupe_same_day_collapses_to_one_item():
    with _EnvVault() as root:
        _write(root, "2026-09-20", "tldr-ai--issue.md", sender="TLDR AI <a@x.com>", subject="TLDR AI Issue",
               date_iso="2026-09-20T11:00:00Z", message_id="<m1@x.com>",
               body=_digest_body("Model X Ships New Feature", "Blurb from newsletter A.", "5 minute read"))
        _write(root, "2026-09-20", "tldr-dev--issue.md", sender="TLDR Dev <b@x.com>", subject="TLDR Dev Issue",
               date_iso="2026-09-20T13:00:00Z", message_id="<m2@x.com>",
               body=_digest_body("Model X ships new feature!", "Very different blurb from newsletter B.",
                                  "6 minute read",
                                  others=(("Gamma Framework Debuts", "4 minute read", "Gamma blurb padding text."),
                                          ("Delta Library Released", "3 minute read", "Delta blurb padding text."))))

        adapter = _adapter(root)
        items = _collect(adapter)
        story_ids = [it.source_id for it in items if it.source_id.startswith("story:")]
        assert len(story_ids) == len(set(story_ids))   # dedupe key never collides
        # 3 stories from A ('Model X', Second, Third) + 2 non-duplicate from B
        # (Gamma, Delta); B's 'Model X ships new feature!' collapses into A's.
        assert len(items) == 5
        story_titles = [it.raw_text.split("\n\n", 1)[1].split("\n", 1)[0] for it in items]
        model_x_count = sum(1 for t in story_titles if t.lower().startswith("model x"))
        assert model_x_count == 1


def test_cross_newsletter_dedupe_different_day_stays_separate():
    with _EnvVault() as root:
        body = _digest_body("Model X Ships New Feature", "Blurb.", "5 minute read")
        _write(root, "2026-09-20", "tldr-ai--issue1.md", sender="TLDR AI <a@x.com>", subject="Issue 1",
               date_iso="2026-09-20T11:00:00Z", message_id="<m1@x.com>", body=body)
        _write(root, "2026-09-21", "tldr-ai--issue2.md", sender="TLDR AI <a@x.com>", subject="Issue 2",
               date_iso="2026-09-21T11:00:00Z", message_id="<m2@x.com>", body=body)
        adapter = _adapter(root)
        items = _collect(adapter)
        story_ids = {it.source_id for it in items if it.source_id.startswith("story:")
                     and "model" in it.source_id}
        # each day gets its own story id for the same normalized title
        day_prefixes = {it.source_id.rsplit(":", 1)[0] for it in items if it.source_id.startswith("story:")}
        assert "story:2026-09-20" in day_prefixes
        assert "story:2026-09-21" in day_prefixes


# --- ids -----------------------------------------------------------------


def test_story_and_chunk_id_shapes_determinism_and_uniqueness():
    with _EnvVault() as root:
        _write(root, "2026-09-20", "digest--one.md", sender="A <a@x.com>", subject="Digest",
               date_iso="2026-09-20T00:00:00Z", message_id="<d1@x.com>",
               body=_digest_body("Only Story Here", "A blurb.", "5 minute read"))
        _write(root, "2026-09-20", "longform--one.md", sender="B <b@x.com>", subject="Longform",
               date_iso="2026-09-20T01:00:00Z", message_id="<l1@x.com>",
               body="# A Title\n\n## Section One\n\n" + LONG_BODY + "\n\n## Section Two\n\n" + LONG_BODY)

        adapter = _adapter(root, {"chunk_max_chars": 4000, "chunk_min_chars": 10})
        items1 = _collect(adapter)
        adapter2 = _adapter(root, {"chunk_max_chars": 4000, "chunk_min_chars": 10})
        items2 = _collect(adapter2)

        ids1 = [it.source_id for it in items1]
        ids2 = [it.source_id for it in items2]
        assert ids1 == ids2                      # deterministic across separate runs
        assert len(set(ids1)) == len(ids1)        # all unique

        story_ids = [i for i in ids1 if i.startswith("story:")]
        chunk_ids = [i for i in ids1 if i.startswith("chunk:")]
        assert story_ids and chunk_ids
        for sid in story_ids:
            assert sid.startswith("story:2026-09-20:")
        chunk_numbers = sorted(int(cid.rsplit(":", 1)[1]) for cid in chunk_ids)
        assert chunk_numbers == list(range(1, len(chunk_ids) + 1))


# --- item fields -----------------------------------------------------------


def test_item_fields_and_content_hash():
    with _EnvVault() as root:
        body = "# A Title\n\n## Section\n\n" + LONG_BODY
        _write(root, "2026-09-20", "pub--issue.md", sender="Pub Name <p@x.com>", subject="A Title",
               date_iso="2026-09-20T05:00:00Z", message_id="<p1@x.com>", body=body)
        adapter = _adapter(root, {"chunk_max_chars": 4000, "chunk_min_chars": 10})
        items = _collect(adapter)
        assert len(items) == 1
        item = items[0]

        assert item.url is None
        assert item.thread_id is None
        assert set(item.metadata.keys()) == {"publication", "subject"}
        assert "points" not in item.metadata
        assert "num_comments" not in item.metadata
        assert item.metadata["publication"] == "Pub Name"
        assert item.metadata["subject"] == "A Title"
        assert item.raw_text.startswith("[Newsletter: Pub Name, 2026-09-20")
        assert item.created_at_i == int(
            datetime(2026, 9, 20, 5, 0, 0, tzinfo=timezone.utc).timestamp())

        units = split_email(body, max_chars=4000, min_chars=10)
        assert len(units) == 1
        assert item.content_hash == content_hash(units[0].text)


# --- chunk header ------------------------------------------------------


def test_chunk_header_part_i_of_n_and_no_repeated_subject():
    with _EnvVault() as root:
        title = "The Loop That Shipped Twice"
        turns = "\n".join(
            f"**Turn {i}:** " + LONG_BODY + f" (turn {i})" for i in range(6)
        )
        body = (f"# {title}\n\n## 1. Intro\n\n{LONG_BODY}\n\n"
                f"## 2. Transcript\n\n{turns}\n")
        _write(root, "2026-09-20", "pub--issue.md", sender="Pub <p@x.com>", subject=title,
               date_iso="2026-09-20T00:00:00Z", message_id="<p1@x.com>", body=body)
        adapter = _adapter(root, {"chunk_max_chars": 400, "chunk_min_chars": 100})
        items = _collect(adapter)
        assert len(items) > 1

        headers = [it.raw_text.split("\n\n", 1)[0] for it in items]
        n = len(headers)
        for i, header in enumerate(headers, start=1):
            assert f"part {i} of {n}" in header

        # the first chunk's breadcrumb is exactly the subject/title: it must
        # not be repeated as a " — <section>" suffix
        first_header = headers[0]
        assert f'"{title}"' in first_header
        assert f"— {title}" not in first_header


# --- failure isolation ---------------------------------------------------


def test_failure_env_var_unset_raises_runtime_error():
    os.environ.pop(ENV, None)
    adapter = NewslettersAdapter({"name": "n", "config_json": {}}, None)
    try:
        list(adapter.iter_items(0))
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected RuntimeError when vault dir env var is unset")


def test_failure_dir_missing_raises_runtime_error():
    os.environ[ENV] = "/nonexistent/path/for/painminer/tests/xyz"
    try:
        adapter = NewslettersAdapter({"name": "n", "config_json": {}}, None)
        try:
            list(adapter.iter_items(0))
        except RuntimeError:
            pass
        else:
            raise AssertionError("expected RuntimeError when vault dir is missing")
    finally:
        os.environ.pop(ENV, None)


def test_failure_invalid_utf8_file_skipped_others_survive():
    with _EnvVault() as root:
        d = Path(root) / "2026-09-20"
        d.mkdir(parents=True)
        (d / "good--one.md").write_text(
            '---\nsender: "A <a@x.com>"\nsubject: "Good"\n'
            'date: "2026-09-20T00:00:00Z"\nmessage_id: "<g1@x.com>"\n---\n\n' + LONG_BODY + "\n",
            encoding="utf-8",
        )
        (d / "bad--two.md").write_bytes(b"\xff\xfe not valid utf8 \x80\x81")

        adapter = _adapter(root)
        items = _collect(adapter)
        assert [it.metadata["subject"] for it in items] == ["Good"]


def test_tiny_notification_email_yields_zero_items():
    # A couple of short lines carry well under _MIN_PROSE (150 chars) of
    # non-heading text, so the longform splitter drops every chunk and the
    # email yields no units/items at all -- not an error, just nothing to read.
    with _EnvVault() as root:
        _write(root, "2026-09-20", "shipping--notice.md", sender="Shop <s@x.com>",
               subject="Your package has shipped", date_iso="2026-09-20T00:00:00Z",
               message_id="<n1@x.com>", body="Your package has shipped.\nTrack it here.")
        adapter = _adapter(root)
        assert _collect(adapter) == []


def test_failure_no_frontmatter_or_date_skipped():
    with _EnvVault() as root:
        d = Path(root) / "2026-09-20"
        d.mkdir(parents=True)
        (d / "good--one.md").write_text(
            '---\nsender: "A <a@x.com>"\nsubject: "Good"\n'
            'date: "2026-09-20T00:00:00Z"\nmessage_id: "<g1@x.com>"\n---\n\n' + LONG_BODY + "\n",
            encoding="utf-8",
        )
        (d / "nofm--two.md").write_text("Just a plain body\nwith no frontmatter at all.\n", encoding="utf-8")

        adapter = _adapter(root)
        items = _collect(adapter)
        assert [it.metadata["subject"] for it in items] == ["Good"]


# --- one page per email ----------------------------------------------------


def test_one_page_per_email():
    with _EnvVault() as root:
        for i in range(3):
            _write(root, "2026-09-20", f"pub{i}--issue.md", sender=f"Pub{i} <p{i}@x.com>",
                   subject=f"Issue {i}", date_iso="2026-09-20T00:00:00Z", message_id=f"<p{i}@x.com>")
        adapter = _adapter(root)
        pages = list(adapter.iter_items(0))
        assert len(pages) == 3
        for page in pages:
            assert isinstance(page, list)
            assert len(page) >= 1


# --- registry --------------------------------------------------------------


def test_registry_resolves_newsletters_impl():
    source = {"name": "newsletters", "adapter": "bulk", "config_json": {"adapter_impl": "newsletters"}}
    assert isinstance(get_adapter(source, None), NewslettersAdapter)
