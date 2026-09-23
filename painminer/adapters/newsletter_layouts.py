"""Newsletter layouts: split one email body into logical units (pure, no I/O).

One email is NOT one item. A digest like TLDR carries ~15-20 unrelated
stories; a long essay or podcast transcript is far more than one deep read
should swallow. `split_email` detects the layout FROM THE TEXT (not the
sender — AlphaSignal's weekday issues are blocks, its weekend essays are
longform) and hands the body to that layout's splitter.

Layouts are a registry, tried in order; the first whose `detect` fires wins,
and `longform` is the catch-all. Supporting a new newsletter format is one
new Layout subclass appended to LAYOUTS — nothing else changes:

  * DigestLayout   — TLDR-style `**Title (N minute read)**` stories; one unit
                     per story; sponsor and job-ad stories dropped.
  * BlockLayout    — AlphaSignal-style blocks ending "forward →"; one unit per
                     story; sponsor blocks ("partner with us →") dropped; the
                     trailing Signals list becomes one unit.
  * longform       — everything else: sections cut on headings, oversize
                     sections cut on line (paragraph / speaker-turn)
                     boundaries, packed up to `max_chars`. Every chunk is a
                     unit, so all of a long piece gets read.

Nothing here is keyed to a sender. A newsletter never seen before goes
through the same detection and, at worst, lands in longform — still fully
read. Platform-wide conventions (Substack paywall cut-offs, link syntax) are
handled generically for every email.

Units carry `story=True` when they are a titled news story: the adapter
dedupes those by title, so the same story in several newsletters on one day
is read once.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Unit:
    """One logical piece of an email, before it becomes a FetchedItem."""

    title: str           # story headline, or the section breadcrumb for a chunk
    text: str            # the unit's own text (no publication header)
    story: bool          # True: a titled story (deduped by title); False: a chunk


# --- shared text helpers -----------------------------------------------------

_WS = re.compile(r"\s+")
_HAS_WORD = re.compile(r"[^\W_]", re.UNICODE)   # any letter or digit


def _strip_md(text: str) -> str:
    """Drop heading/emphasis markers and collapse whitespace (for titles)."""
    t = re.sub(r"^#+\s*", "", text.strip())
    t = t.replace("**", "").replace("__", "")
    return _WS.sub(" ", t).strip()


def normalize_title(title: str) -> str:
    """Dedupe key for a story title: case/punctuation-free, tag stripped."""
    t = re.sub(r"\([^)]*\)\s*$", "", _strip_md(title))   # "(3 minute read)"
    t = re.sub(r"[^a-z0-9]+", " ", t.lower())
    return t.strip()


def _content_lines(lines: list[str]) -> list[str]:
    """Stripped lines that carry words — drops blanks and emoji/rule-only
    section dividers (`🎁`, `---`, a lone `**`)."""
    out = []
    for ln in lines:
        s = ln.strip()
        if s and _HAS_WORD.search(s):
            out.append(s)
    return out


class Layout:
    """A newsletter format: recognise it, then split it into units."""

    name = "base"

    def detect(self, lines: list[str]) -> bool:
        raise NotImplementedError

    def split(self, lines: list[str]) -> list[Unit]:
        raise NotImplementedError


# --- DigestLayout (TLDR-style) -----------------------------------------------

_DIGEST_TITLE = re.compile(r"^\*\*(?P<title>.+?)\s*\((?P<tag>[^()]{1,60})\)\*\*$")
# Detection needs the read-time tag specifically: plenty of essays bold a
# "**Name (2023)**" line, but only a digest tags stories with read times.
_READ_TIME = re.compile(r"(?i)^\d+\s*(minute|min|hour)s?\s+(read|podcast|video)$")
# Tags that mark a story as an ad rather than content (sponsor slots, and
# TLDR's own job listings like "(TLDR Curator, ~5 hrs/week)").
_AD_TAG = re.compile(r"(?i)(sponsor|\$|remote|hrs?/week|hiring)")
_DIGEST_END = re.compile(r"(?i)^(love tldr\?|want to advertise in tldr|want to work at tldr)")
_HEADING_LINE = re.compile(r"^#{1,6}\s")


class DigestLayout(Layout):
    name = "digest"

    def detect(self, lines: list[str]) -> bool:
        hits = 0
        for ln in lines:
            m = _DIGEST_TITLE.match(ln.strip())
            if m and _READ_TIME.match(m.group("tag").strip()):
                hits += 1
                if hits >= 3:
                    return True
        return False

    def split(self, lines: list[str]) -> list[Unit]:
        units: list[Unit] = []
        title: str | None = None
        is_ad = False
        body: list[str] = []

        def flush() -> None:
            if title is not None and not is_ad:
                text = "\n".join([title, *_content_lines(body)])
                units.append(Unit(title=title, text=text, story=True))

        for raw in lines:
            line = raw.strip()
            if _DIGEST_END.match(line):
                break
            m = _DIGEST_TITLE.match(line)
            if m:
                flush()
                title = _strip_md(m.group("title"))
                is_ad = bool(_AD_TAG.search(m.group("tag")))
                body = []
                continue
            if title is None or _HEADING_LINE.match(line):
                continue       # masthead before story 1 / section headers between
            body.append(line)
        flush()
        return units


# --- BlockLayout (AlphaSignal-style) -----------------------------------------

_BLOCK_END = re.compile(r"^forward\s*→$")
_AD_END = re.compile(r"(?i)^partner with us\s*→$")
_ENGAGEMENT = re.compile(r"(?i)^[\d,.]+[kKmM]?\s+(likes|downloads|stars)$")
_READ_MORE = re.compile(r"(?i)^read more$")
_PRESENTED = re.compile(r"(?i)^presented by\b")
_BLOCKS_TAIL = re.compile(
    r"(?i)^(at alpha ?signal, our mission|looking to promote your company|"
    r"work with us|how was today's email)"
)


class BlockLayout(Layout):
    name = "blocks"

    def detect(self, lines: list[str]) -> bool:
        return sum(1 for ln in lines if _BLOCK_END.match(ln.strip())) >= 2

    def split(self, lines: list[str]) -> list[Unit]:
        units: list[Unit] = []
        segment: list[str] = []
        for line in (ln.strip() for ln in lines):
            if _AD_END.match(line):
                segment = []               # sponsor block: drop it whole
            elif _BLOCK_END.match(line):
                unit = self._story(segment)
                if unit is not None:
                    units.append(unit)
                segment = []
            else:
                segment.append(line)
        signals = self._signals(segment)
        if signals is not None:
            units.append(signals)
        return units

    @staticmethod
    def _story(segment: list[str]) -> Unit | None:
        """Title is the line right above the engagement line ("33,404
        Likes"); everything above it is greeting/TOC and is dropped."""
        lines = _content_lines(segment)
        idx = next((i for i, ln in enumerate(lines) if _ENGAGEMENT.match(ln)), None)
        if not idx:                        # None, or no line above it
            return None
        title = _strip_md(lines[idx - 1])
        body = [ln for ln in lines[idx + 1:] if not _READ_MORE.match(ln)]
        if not body:
            return None
        return Unit(title=title, text="\n".join([title, *body]), story=True)

    @staticmethod
    def _signals(segment: list[str]) -> Unit | None:
        """The tail after the last story: a short numbered list, then the
        footer. A sponsored entry is the line above a 'Presented by X'."""
        lines = _content_lines(segment)
        start = next((i for i, ln in enumerate(lines) if ln.lower() == "signals"), None)
        if start is None:
            return None
        kept: list[str] = []
        for ln in lines[start + 1:]:
            if _BLOCKS_TAIL.match(ln):
                break
            if _PRESENTED.match(ln):
                if kept:
                    kept.pop()
                continue
            kept.append(ln)
        if not kept:
            return None
        return Unit(title="Signals", text="\n".join(["Signals", *kept]), story=False)


# --- longform (catch-all) ----------------------------------------------------

_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)$")
_AD_HEADING = re.compile(
    r"(?i)(from our partners|sponsor|presented by|in partnership|message from the editor)"
)
_BOILERPLATE = re.compile(
    r"(?i)^("
    r"forwarded this email\?.*|_?was this email forwarded to you\?.*|"
    r"read in app|listen now|like|comment|restack|share|"
    r"subscribe( now)?|upgrade to paid|"
    r".*\|\s*read online"
    r")$"
)
# Footer markers: nothing after them is article content.
_TAIL = re.compile(
    r"(?i)^(#+\s*)?(\*\*)?("
    r"what'd you think of today's email|how was today's email|"
    r"liking, sharing, commenting|if you liked this, consider upgrading"
    r")"
)


@dataclass
class _Section:
    path: list[str]      # heading breadcrumb, outermost first
    level: int           # heading level (0 = text before any heading)
    lines: list[str]


def _sections(lines: list[str]) -> list[_Section]:
    sections = [_Section(path=[], level=0, lines=[])]
    stack: list[tuple[int, str]] = []
    skip_level: int | None = None        # inside a sponsor section at this level

    for line in _content_lines(lines):
        if _BOILERPLATE.match(line):
            continue
        if _TAIL.match(line):
            break
        m = _HEADING.match(line)
        if m:
            level, title = len(m.group(1)), _strip_md(m.group(2))
            if skip_level is not None and level > skip_level:
                continue                 # sub-heading inside a sponsor section
            skip_level = None
            if _AD_HEADING.search(title):
                skip_level = level
                continue
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            sections.append(_Section([t for _, t in stack], level, [line]))
        elif skip_level is None:
            sections[-1].lines.append(line)
    return [s for s in sections if s.lines]


def _cut_long_line(line: str, limit: int) -> list[str]:
    """A single line over the ceiling (a long transcript turn): cut at
    sentence ends; hard-cut only when no sentence end is near."""
    out: list[str] = []
    rest = line
    while len(rest) > limit:
        cut = rest.rfind(". ", 0, limit)
        cut = cut + 1 if cut > limit // 3 else limit
        out.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        out.append(rest)
    return out


_MIN_PROSE = 150      # chars of non-heading text a longform chunk must carry


def _size(lines: list[str]) -> int:
    return sum(len(ln) + 1 for ln in lines)


def split_longform(lines: list[str], max_chars: int, min_chars: int) -> list[Unit]:
    """Pack heading sections into chunks of at most `max_chars`.

    A top-level (#/##) heading starts a fresh chunk once the current one has
    `min_chars` of content, so chunks follow the piece's own structure;
    smaller sections pack together. A section over the ceiling is cut on line
    boundaries — for a transcript each line is a speaker turn. Runts under
    `min_chars` then merge into a neighbour when the result still fits.
    """
    chunks: list[tuple[list[str], list[str]]] = []   # (breadcrumb, lines)
    path: list[str] = []
    cur: list[str] = []

    def flush() -> None:
        nonlocal cur
        if cur and all(_HEADING.match(ln) for ln in cur):
            return             # a bare heading rides along into the next chunk
        if cur:
            chunks.append((path, cur))
        cur = []

    for sec in _sections(lines):
        pieces: list[str] = []
        for ln in sec.lines:
            pieces.extend(_cut_long_line(ln, max_chars) if len(ln) > max_chars else [ln])
        major = 0 < sec.level <= 2
        if cur and (_size(cur) + _size(pieces) > max_chars
                    or (major and _size(cur) >= min_chars)):
            flush()
        if not cur:
            path = sec.path
        for p in pieces:
            if cur and _size(cur) + len(p) + 1 > max_chars:
                flush()
                path = sec.path
            cur.append(p)
    flush()

    merged: list[tuple[list[str], list[str]]] = []
    for p, body in chunks:
        if merged and (_size(body) < min_chars or _size(merged[-1][1]) < min_chars) \
                and _size(merged[-1][1]) + _size(body) <= max_chars:
            prev_path, prev = merged[-1]
            merged[-1] = (prev_path or p, prev + body)
        else:
            merged.append((p, body))

    # A chunk with almost no prose (a lone postal-address footer that could
    # not merge) is not worth a deep read.
    return [Unit(title=" › ".join(p), text="\n".join(body), story=False)
            for p, body in merged
            if sum(len(ln) for ln in body if not _HEADING.match(ln)) >= _MIN_PROSE]


# --- registry ----------------------------------------------------------------

LAYOUTS: tuple[Layout, ...] = (DigestLayout(), BlockLayout())


def detect_layout(lines: list[str]) -> str:
    """Name of the layout `split_email` would use (diagnostics and tests)."""
    for layout in LAYOUTS:
        if layout.detect(lines):
            return layout.name
    return "longform"


# The fetcher strips single-line markdown links, but a link whose text wraps
# lines survives it — and Substack's podcast play links carry a signed user
# token. URLs are never useful to the model (items carry no url), so every
# link is reduced to its text and bare URLs are dropped before splitting.
# Link text may hold one level of brackets: "[**[Webinar] ... (Sponsor)**](url)".
_MD_LINK = re.compile(r"\[((?:[^\[\]]|\[[^\[\]]*\])*)\]\([^)]*\)", re.DOTALL)
_BARE_URL = re.compile(r"https?://\S+")


def sanitize(body: str) -> str:
    return _BARE_URL.sub("", _MD_LINK.sub(r"\1", body))


# Platform paywall cut-offs (Substack's stock wording, shared by every
# publication on it). What follows is an upsell, never article text, so the
# email is cut there and only the free part is read — no per-sender rules.
_PAYWALL = re.compile(
    r"(?i)^(#+\s*)?(subscribe to .{1,80} to unlock the rest|"
    r"continue reading this post for free in the substack app)"
)


def free_part(lines: list[str]) -> list[str]:
    for i, ln in enumerate(lines):
        if _PAYWALL.match(ln.strip()):
            return lines[:i]
    return lines


def split_email(body: str, *, max_chars: int = 4000, min_chars: int = 400) -> list[Unit]:
    """Split one email body into logical units, by detected layout."""
    lines = free_part(sanitize(body).split("\n"))
    for layout in LAYOUTS:
        if layout.detect(lines):
            return layout.split(lines)
    return split_longform(lines, max_chars, min_chars)
