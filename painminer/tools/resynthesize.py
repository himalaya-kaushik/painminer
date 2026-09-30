"""Re-run pass 3 (synthesis) and delivery over findings already in the DB.

For when a night's judging finished but the digest was lost or came out empty.
Does not fetch, judge, embed or cluster; it only rebuilds the briefing from
findings created at or after --since (normally that run's `runs.started_at`).

    python -m painminer.tools.resynthesize --since 2026-09-30T16:19:42+00:00

Exits non-zero, sending nothing, if synthesis is still empty.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date as date_cls

import telegram

from painminer.config import load_config
from painminer.db import DB
from painminer.delivery import document
from painminer.delivery.telegram_bot import _send_scan_digest
from painminer.llm import LLM
from painminer.pipeline import scan
from painminer.pipeline import synthesize as synth_stage


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True, help="ISO timestamp of the run's start")
    ap.add_argument("--day", help="digest date, YYYY-MM-DD (default: today)")
    args = ap.parse_args()

    config = load_config()
    db = DB.from_config()
    llm = LLM(config)

    result = synth_stage.synthesize_night(db, llm, config, run_started_at=args.since)
    print(f"{result.n_findings} findings; empty={result.is_empty}; "
          f"summary={result.night_summary[:120]!r}", flush=True)
    if result.is_empty:
        print("Synthesis still empty; nothing written or sent.", file=sys.stderr)
        return 1

    day = date_cls.fromisoformat(args.day) if args.day else date_cls.today()
    path = document.write_digest(document.render_markdown(result, day=day),
                                 digests_dir=config.digests_dir)
    print(f"Wrote {path}", flush=True)

    summary = scan.ScanSummary()
    summary.synthesis, summary.digest_path = result, str(path)

    async def _deliver() -> None:
        bot = telegram.Bot(config.telegram_token)
        async with bot:
            await _send_scan_digest(bot, int(config.telegram_chat_id), summary)

    asyncio.run(_deliver())
    return 0


if __name__ == "__main__":
    sys.exit(main())
