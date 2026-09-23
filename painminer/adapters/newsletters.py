"""Newsletter vault adapter (§3.1): daily newsletters from the Gmail-fed vault.

A separate job (newsletter-synthesizer, run by the himalaya-vault repo) pulls
the inbox once a day and writes each email as markdown to
`Newsletters/YYYY-MM-DD/<sender-slug>--<subject-slug>.md` with a small
frontmatter (sender, subject, date, message_id). The nightly workflow checks
that folder out and points `VAULT_NEWSLETTERS_DIR` at it; this adapter reads
it locally — no network, no token in Python.

Each email is split into logical units by `newsletter_layouts` (one item per
digest story, one per longform chunk), so a 20-story digest or a 100KB essay
never lands on the model as one blob.

Same story, several newsletters: TLDR, TLDR AI and TLDR Dev often run the
same headline on the same day. A story's source_id is built from its day and
normalized title, so the repeats collapse to one item — within a run here, and
across runs via the (source, source_id) unique constraint. It is read once.
Longform chunks are keyed by message id + chunk number.

Window, by vault day folder: today's folder and the `lookback_days` before
it (UTC). The vault's daily fetch files each email under the day it arrived,
so yesterday's mail lands the next day; re-reading a folder already done is
free (fetch upserts with ignore_duplicates, and ids are deterministic), so
each night effectively adds only what the vault newly wrote. The watermark
(since_ts) is deliberately not used: it would turn a first run into fetch's
default 30-day backfill, and a vault that was down for longer than the
lookback is skipped rather than dumped into one night. until_ts (tests,
bounded chunks) is still honoured on the email's date.

Failure isolation: a missing/unset vault dir raises, so scan.py records a
per-source failure and the run carries on, exactly like any other source. A
single unreadable or malformed file is skipped, never raises.

Relevant `config_json` keys:

    vault_dir_env     str    env var naming the Newsletters dir
                             (default "VAULT_NEWSLETTERS_DIR")
    exclude_senders   list   optional escape hatch: sender slugs (the filename
                             part before "--") to skip (default none)
    lookback_days     int    day folders read before today's (default 2)
    chunk_max_chars   int    longform chunk ceiling (default 4000)
    chunk_min_chars   int    longform runt threshold (default 400)
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from pathlib import Path
from typing import Any

from .base import Adapter, FetchedItem, content_hash
from .newsletter_layouts import Unit, normalize_title, split_email

_DATE_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FM_KEY = re.compile(r'^([A-Za-z_]+):\s*"(.*)$')


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split the fetcher's frontmatter from the body.

    The fetcher writes `key: "value"` lines and a long subject can wrap onto
    a continuation line, so this is a small purpose-built parser rather than
    a YAML dependency. Returns ({}, text) when there is no frontmatter.
    """
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    block, body = text[4:end], text[end + 4:].lstrip("\n")

    fields: dict[str, str] = {}
    key = ""
    for line in block.split("\n"):
        m = _FM_KEY.match(line)
        if m:
            key = str(m.group(1))
            fields[key] = str(m.group(2))
        elif key:
            fields[key] += " " + line.strip()   # a wrapped subject line
    for k, v in fields.items():
        v = v.rstrip()
        if v.endswith('"'):
            v = v[:-1]
        fields[k] = v.replace('\\"', '"').strip()
    return fields, body


def parse_date(value: str | None) -> int | None:
    """ISO timestamp from frontmatter -> epoch seconds (UTC if naive)."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def publication_name(sender: str, fallback: str) -> str:
    """'TLDR AI <dan@tldrnewsletter.com>' -> 'TLDR AI'."""
    name, addr = parseaddr(sender or "")
    return name.strip() or addr.split("@")[0] or fallback


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]


class NewslettersAdapter(Adapter):
    def __init__(self, source: dict[str, Any], client: Any) -> None:
        super().__init__(source, client)
        cfg = self.config
        self.vault_dir_env: str = cfg.get("vault_dir_env", "VAULT_NEWSLETTERS_DIR")
        self.exclude_senders: set[str] = {s.lower() for s in cfg.get("exclude_senders") or []}
        self.lookback_days: int = int(cfg.get("lookback_days", 2))
        self.chunk_max_chars: int = int(cfg.get("chunk_max_chars", 4000))
        self.chunk_min_chars: int = int(cfg.get("chunk_min_chars", 400))
        self.now: float | None = None        # tests pin the clock here

    # --- files ---------------------------------------------------------------

    def _root(self) -> Path:
        value = os.environ.get(self.vault_dir_env, "").strip()
        if not value:
            raise RuntimeError(f"{self.vault_dir_env} is not set; newsletter vault unavailable")
        root = Path(value)
        if not root.is_dir():
            raise RuntimeError(f"newsletter vault dir {root} does not exist")
        return root

    def _files(self, root: Path, first_day: str) -> list[Path]:
        """Emails in day folders from `first_day` on. Only those folders are
        listed, so cost stays flat as the vault grows."""
        files: list[Path] = []
        for d in sorted(root.iterdir()):
            if d.is_dir() and _DATE_DIR.match(d.name) and d.name >= first_day:
                files.extend(sorted(p for p in d.iterdir()
                                    if p.suffix == ".md" and not self._excluded(p)))
        return files

    def _excluded(self, path: Path) -> bool:
        return path.name.split("--", 1)[0].lower() in self.exclude_senders

    # --- items ---------------------------------------------------------------

    def _item(self, u: Unit, *, source_id: str, header: str, ts: int,
              publication: str, subject: str) -> FetchedItem:
        return FetchedItem(
            source=self.name,
            source_id=source_id,
            url=None,
            raw_text=f"{header}\n\n{u.text}",
            content_hash=content_hash(u.text),
            created_at_i=ts,
            thread_id=None,      # each unit is its own thread for the per-thread cap
            # No points/num_comments keys: the deep read's engagement line is
            # an HN concept and must stay absent for newsletter items.
            metadata={"publication": publication, "subject": subject},
        )

    def _email_items(self, path: Path, until_ts: int | None,
                     seen: set[str]) -> list[FetchedItem]:
        fm, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        ts = parse_date(fm.get("date"))
        if ts is None or (until_ts is not None and ts >= until_ts):
            return []

        publication = publication_name(fm.get("sender", ""), path.name.split("--", 1)[0])
        subject = fm.get("subject") or path.stem
        message_id = fm.get("message_id") or f"{path.parent.name}/{path.name}"
        day = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()

        units = split_email(body, max_chars=self.chunk_max_chars,
                            min_chars=self.chunk_min_chars)
        n_chunks = sum(1 for u in units if not u.story)
        chunk_no = 0
        items: list[FetchedItem] = []
        for u in units:
            if u.story:
                key = normalize_title(u.title)
                if not key:
                    continue
                source_id = f"story:{day}:{_sha(key)}"
                header = f"[Newsletter: {publication}, {day}]"
            else:
                chunk_no += 1
                source_id = f"chunk:{_sha(message_id)}:{chunk_no}"
                section = u.title
                if section == subject or section.startswith(subject + " › "):
                    section = section[len(subject):].lstrip(" ›")   # don't repeat it
                where = f" — {section}" if section else ""
                part = f", part {chunk_no} of {n_chunks}" if n_chunks > 1 else ""
                header = f'[Newsletter: {publication}, {day}: "{subject}"{where}{part}]'
            if source_id in seen:
                continue           # same story already taken from another newsletter
            seen.add(source_id)
            items.append(self._item(u, source_id=source_id, header=header, ts=ts,
                                    publication=publication, subject=subject))
        return items

    # --- Adapter interface ---------------------------------------------------

    def iter_items(
        self, since_ts: int, until_ts: int | None = None
    ) -> Iterator[list[FetchedItem]]:
        """One page per email, so memory stays bounded by one email."""
        root = self._root()
        now = self.now if self.now is not None else time.time()
        today = datetime.fromtimestamp(now, tz=timezone.utc).date()
        first_day = (today - timedelta(days=self.lookback_days)).isoformat()
        seen: set[str] = set()
        for path in self._files(root, first_day):
            try:
                page = self._email_items(path, until_ts, seen)
            except Exception:
                continue           # one bad file never kills the source
            if page:
                yield page
