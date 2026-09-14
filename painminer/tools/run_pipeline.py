"""Run the full pipeline end to end and send the digest to Telegram.

Headless equivalent of /scan plus delivery. Prints the operator report:
findings per source, empty-array rate, and seconds per item.

    .venv/bin/python -m painminer.tools.run_pipeline
"""

from __future__ import annotations

import asyncio
from collections import Counter

import telegram

from painminer.pipeline import scan
from painminer.config import load_config
from painminer.db import DB
from painminer.pipeline.embed import Embedder
from painminer.llm import LLM
from painminer.delivery.telegram_bot import _send_digest


def _findings_per_source(db: DB) -> dict[str, int]:
    # findings -> item_id -> source
    item_ids: list[int] = []
    start = 0
    while True:
        rows = db.table("findings").select("item_id").order("id").range(start, start + 999).execute().data
        item_ids += [r["item_id"] for r in rows]
        if len(rows) < 1000:
            break
        start += 1000
    source_of: dict[int, str] = {}
    ids = list(set(item_ids))
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        for r in db.table("items").select("id, source").in_("id", chunk).execute().data:
            source_of[r["id"]] = r["source"]
    return dict(Counter(source_of.get(i, "unknown") for i in item_ids))


async def _deliver(config) -> None:
    bot = telegram.Bot(config.telegram_token)
    async with bot:
        await _send_digest(bot, int(config.telegram_chat_id))


def main() -> None:
    config = load_config()
    db = DB.from_config()
    llm = LLM(config)
    embedder = Embedder.from_config(config)

    summary = scan.run_scan(db, llm, config, embedder=embedder,
                            progress=lambda m: print(m, flush=True))

    print("\nDelivering digest to Telegram…")
    asyncio.run(_deliver(config))

    per_source_findings = _findings_per_source(db)
    print("\n" + "=" * 60)
    print("PIPELINE REPORT")
    print("=" * 60)
    print("\nItems fetched per source:")
    for name, n in summary.per_source.items():
        err = summary.source_errors.get(name)
        print(f"  {name}: {n}" + (f"  ERROR: {err}" if err else ""))
    print("\nFindings per source:")
    for name in summary.per_source:
        print(f"  {name}: {per_source_findings.get(name, 0)}")
    rate = (summary.empty / summary.judged) if summary.judged else 0.0
    print(f"\nJudged: {summary.judged}  (empty {summary.empty}, capped {summary.capped}, failed {summary.failed})")
    print(f"Empty-array rate: {summary.empty}/{summary.judged} = {rate:.0%}")
    print(f"Seconds per item: {summary.seconds_per_item:.2f}s")
    print(f"Findings created: {summary.findings_created}  |  New clusters: {summary.new_clusters}")
    print(f"Total wall-clock: {summary.seconds:.0f}s"
          + ("  [judge stopped on time budget]" if summary.stopped_on_budget else ""))


if __name__ == "__main__":
    main()
