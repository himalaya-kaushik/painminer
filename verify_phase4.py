"""Phase 4 live verification (brief §Phase 4 verify).

Judge 20 real HN items against Machine B and print the raw model output next
to the source text. The point is to SEE empty arrays: if every item produces a
finding, the prompt is broken (§7.3, §15). Judging here is DRY — nothing is
committed and raw_text is preserved — so the prompt can be tuned and this
re-run. A final step exercises the real transactional commit on one item.

    python verify_phase4.py
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from db import DB
from config import load_config
from llm import LLM
import judge
import dedupe
import seed_sources
from fetch import fetch_source

SAMPLE = 20
RECENT_SECONDS = 1800   # pull a recent ~30-minute window to sample


def _prepare_ready_items(db: DB) -> list[dict]:
    seed_sources.main()
    db.table("items").delete().eq("source", "hackernews").execute()

    # Bounded recent window (overlap 0 so it stays ~30 min, not 6h+).
    db.table("sources").update(
        {"config_json": {**seed_sources.HACKER_NEWS["config_json"], "overlap_hours": 0}}
    ).eq("name", "hackernews").execute()
    now = int(datetime.now(tz=timezone.utc).timestamp())
    try:
        fetch_source(db, "hackernews", since_ts=now - RECENT_SECONDS, until_ts=now)
    finally:
        seed_sources.main()  # restore overlap_hours=6

    dedupe.dedupe(db)
    resp = (
        db.table("items")
        .select("id, url, raw_text")
        .eq("source", "hackernews")
        .eq("state", "ready")
        .order("id")
        .limit(SAMPLE)
        .execute()
    )
    return resp.data


def _print_case(n: int, item: dict, result: judge.JudgeResult) -> None:
    src = item["raw_text"] or ""
    print("\n" + "=" * 78)
    print(f"[{n}] item id={item['id']}  {item.get('url','')}")
    print("-" * 78)
    print("SOURCE:")
    print(src[:600] + (" …" if len(src) > 600 else ""))
    print("-" * 78)
    print(f"RAW MODEL OUTPUT (attempts={result.attempts}):")
    print(result.raw_outputs[-1] if result.raw_outputs else "<none>")
    if result.ok and result.findings:
        # Quote fidelity: is each evidence_quote actually in the source? (§7.2)
        norm_src = " ".join(src.split()).lower()
        for f in result.findings:
            q = " ".join(str(f["evidence_quote"]).split()).lower()
            mark = "verbatim" if q and q in norm_src else "NOT FOUND IN SOURCE"
            print(f"   - {f['kind']}: evidence_quote {mark}")
    elif not result.ok:
        print(f"   !! validation failed: {result.error}")


def main() -> None:
    db = DB.from_config()
    config = load_config()
    llm = LLM(config)

    print("preflight GET /v1/models …")
    judge.preflight(llm, config)
    warm = llm.warmup()
    print(f"preflight OK, warm-up {warm:.1f}s")

    items = _prepare_ready_items(db)
    print(f"prepared {len(items)} ready HN items to judge (dry run)")
    if len(items) < SAMPLE:
        print(f"WARNING: only {len(items)} items available (< {SAMPLE})")

    empty = 0
    nonempty = 0
    failed = 0
    total_findings = 0
    for n, item in enumerate(items, 1):
        result = judge.judge_item(llm, item["raw_text"] or "")
        _print_case(n, item, result)
        if not result.ok:
            failed += 1
        elif not result.findings:
            empty += 1
        else:
            nonempty += 1
            total_findings += len(result.findings)

    judged = empty + nonempty
    print("\n" + "#" * 78)
    print("PHASE 4 SUMMARY")
    print(f"  items judged:       {judged}")
    print(f"  empty  []:          {empty}")
    print(f"  non-empty:          {nonempty}  ({total_findings} findings)")
    print(f"  validation-failed:  {failed}")
    if judged:
        print(f"  EMPTY-ARRAY RATE:   {empty}/{judged} = {empty/judged:.0%}")

    # Prove the transactional commit path on one item (real write).
    if items:
        victim = items[-1]
        res = judge.judge_item(llm, victim["raw_text"] or "")
        findings = res.findings or []
        n = judge.commit_judgement(db, victim["id"], findings)
        row = db.get("items", id=victim["id"])
        print("\ncommit-path check on item", victim["id"], ":",
              f"wrote {n} findings, state={row['state']}, "
              f"raw_text nulled={row['raw_text'] is None}")
        assert row["state"] == "done" and row["raw_text"] is None, "commit path broken"
        print("commit path OK (findings + raw_text null + done, one transaction)")


if __name__ == "__main__":
    main()
