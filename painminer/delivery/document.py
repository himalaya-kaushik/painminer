"""The digest document (v3 brief §4). Pure rendering + file IO, no telegram
import, so it is reusable and testable offline.

Pass 3 produces a structured briefing; this turns it into the `.md` the reader
opens over coffee, writes it to `digests/YYYY-MM-DD.md` (a free, greppable
archive), and builds the short companion message that carries the feedback
buttons a document can't.

Items are numbered sequentially across the whole document so the companion
message can refer to "item 3" and mean the same thing the reader sees.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import date as date_cls
from pathlib import Path

from painminer.pipeline.synthesize import SynthesisResult

_SECTION_TITLES = {
    "patterns": "Patterns",
    "worth_reading": "Worth reading",
    "shipped": "Shipped",
    "someone_built": "Someone built",
}
_SECTION_ORDER = ("patterns", "worth_reading", "shipped", "someone_built")


@dataclass
class Headline:
    n: int
    section: str
    headline: str
    cluster_id: int | None
    url: str | None


def _numbered(result: SynthesisResult) -> list[tuple[int, str, dict]]:
    """Flatten sections into (number, section, item) in document order."""
    out = []
    n = 0
    for section in _SECTION_ORDER:
        for item in result.sections.get(section, []):
            n += 1
            out.append((n, section, item))
    return out


def _links(item: dict, index: dict[int, dict]) -> list[str]:
    seen: set[str] = set()
    links: list[str] = []
    for fid in item.get("finding_ids", []):
        meta = index.get(fid)
        if meta and meta.get("url") and meta["url"] not in seen:
            seen.add(meta["url"])
            links.append(meta["url"])
    return links


def _first_cluster_id(item: dict, index: dict[int, dict]) -> int | None:
    for fid in item.get("finding_ids", []):
        meta = index.get(fid)
        if meta and meta.get("cluster_id") is not None:
            return meta["cluster_id"]
    return None


def render_markdown(result: SynthesisResult, day: date_cls | None = None) -> str:
    day = day or date_cls.today()
    heading = day.strftime("%-d %B %Y") if hasattr(day, "strftime") else str(day)
    lines = [f"# {heading}", ""]
    if result.night_summary:
        lines += [result.night_summary, ""]

    numbered = _numbered(result)
    by_section: dict[str, list[tuple[int, dict]]] = {}
    for n, section, item in numbered:
        by_section.setdefault(section, []).append((n, item))

    for section in _SECTION_ORDER:
        items = by_section.get(section)
        if not items:
            continue
        lines += [f"## {_SECTION_TITLES[section]}", ""]
        for n, item in items:
            lines.append(f"### {n}. {item['headline']}")
            body = item.get("body", "").strip()
            links = _links(item, result.findings_index)
            if body:
                lines.append(body)
            if links:
                lines.append("")
                lines.append(" · ".join(f"[source]({u})" for u in links))
            lines.append("")

    if result.is_empty and not result.night_summary:
        lines += ["Nothing worth surfacing tonight.", ""]

    return "\n".join(lines).rstrip() + "\n"


def headlines(result: SynthesisResult, limit: int = 5) -> list[Headline]:
    """The top `limit` items, numbered, for the companion button message."""
    out: list[Headline] = []
    for n, section, item in _numbered(result):
        out.append(Headline(
            n=n,
            section=section,
            headline=item["headline"],
            cluster_id=_first_cluster_id(item, result.findings_index),
            url=(_links(item, result.findings_index) or [None])[0],
        ))
    return out[:limit]


def write_digest(markdown: str, day: date_cls | None = None, digests_dir: str = "digests") -> Path:
    day = day or date_cls.today()
    stamp = day.strftime("%Y-%m-%d") if hasattr(day, "strftime") else str(day)
    path = Path(digests_dir) / f"{stamp}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")
    return path


def commit_digest(path: Path) -> bool:
    """Best-effort: add + commit the digest to the repo (§4, free archive).

    Never raises; returns True on a successful commit. No author trailers.
    """
    try:
        subprocess.run(["git", "add", str(path)], check=True,
                       capture_output=True, timeout=30)
        subprocess.run(
            ["git", "commit", "-m", f"digest: {path.stem}", "--", str(path)],
            check=True, capture_output=True, timeout=30,
        )
        return True
    except Exception:
        return False
