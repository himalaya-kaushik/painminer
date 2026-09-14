"""Dump all findings to findings.md (and stdout) for reading.

Each finding is printed with its source item URL, kind, statement,
why_it_matters, and evidence_quote. Grouped by kind, and within a kind sorted
by confidence, so the feed reads top-down.

    .venv/bin/python -m painminer.delivery.findings_report            # writes findings.md
    .venv/bin/python -m painminer.delivery.findings_report out.md     # writes a different file
"""

from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime, timezone

from painminer.db import DB

PAGE = 1000


def _fetch_all(db: DB, table: str, columns: str) -> list[dict]:
    """Read an entire table, paging so we are not capped at 1000 rows."""
    rows: list[dict] = []
    start = 0
    while True:
        resp = (
            db.table(table)
            .select(columns)
            .order("id")
            .range(start, start + PAGE - 1)
            .execute()
        )
        batch = resp.data
        rows.extend(batch)
        if len(batch) < PAGE:
            return rows
        start += PAGE


def _url_by_item(db: DB) -> dict[int, str | None]:
    return {r["id"]: r.get("url") for r in _fetch_all(db, "items", "id, url")}


def build_report(db: DB) -> str:
    findings = _fetch_all(
        db,
        "findings",
        "id, item_id, kind, statement, why_it_matters, domain, "
        "evidence_quote, confidence",
    )
    urls = _url_by_item(db)

    by_kind: dict[str, list[dict]] = defaultdict(list)
    for f in findings:
        by_kind[f["kind"] or "unknown"].append(f)

    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = [
        "# painminer findings",
        "",
        f"_{len(findings)} findings across {len(by_kind)} kinds — generated {now}_",
        "",
    ]

    if not findings:
        lines += [
            "No findings in the database yet. Run a judge pass first:",
            "",
            "```",
            ".venv/bin/python -m painminer.pipeline.fetch hackernews   # or a bounded window",
            ".venv/bin/python -m painminer.pipeline.dedupe",
            ".venv/bin/python -m painminer.pipeline.judge",
            "```",
        ]
        return "\n".join(lines) + "\n"

    # Kind summary, most-populous first.
    lines.append("## By kind")
    lines.append("")
    for kind in sorted(by_kind, key=lambda k: -len(by_kind[k])):
        lines.append(f"- **{kind}** — {len(by_kind[kind])}")
    lines.append("")

    for kind in sorted(by_kind, key=lambda k: -len(by_kind[k])):
        items = sorted(by_kind[kind], key=lambda f: -(f.get("confidence") or 0))
        lines.append("---")
        lines.append("")
        lines.append(f"## {kind}  ({len(items)})")
        lines.append("")
        for f in items:
            url = urls.get(f["item_id"]) or "(no url)"
            conf = f.get("confidence")
            conf_str = f" · confidence {conf:.2f}" if isinstance(conf, (int, float)) else ""
            domain = f.get("domain")
            domain_str = f" · {domain}" if domain else ""
            lines.append(f"### {f['statement']}")
            lines.append("")
            if f.get("why_it_matters"):
                lines.append(f"**Why it matters:** {f['why_it_matters']}")
                lines.append("")
            if f.get("evidence_quote"):
                lines.append(f"> {f['evidence_quote']}")
                lines.append("")
            lines.append(f"[source]({url}){domain_str}{conf_str}")
            lines.append("")

    return "\n".join(lines) + "\n"


def main() -> None:
    out_path = sys.argv[1] if len(sys.argv) > 1 else "findings.md"
    db = DB.from_config()
    report = build_report(db)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(report)
    n = report.count("\n### ")
    print(f"wrote {out_path} ({n} findings)")


if __name__ == "__main__":
    main()
