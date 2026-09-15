"""Full pipeline orchestrator (§4): fetch -> dedupe -> judge -> embed ->
cluster -> rank, with a run row for observability and watermark advancement.

Used headless (`.venv/bin/python -m painminer.pipeline.scan`) and by the Telegram /scan command. Emits
progress strings through the optional `progress` callback so a caller can
stream them.

Watermark discipline (§5.1): each source's last_successful_fetch_at is advanced
to the moment its fetch STARTED — but only after the judge stage succeeds, so a
crash before judging never marks unprocessed data as seen. A source whose fetch
errors is recorded (visible in /status) and does not abort the run (§11).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import openai

from datetime import date as date_cls

from painminer.pipeline import cluster as cluster_stage
from painminer.pipeline import dedupe as dedupe_stage
from painminer.pipeline import judge as judge_stage
from painminer.pipeline import rank as rank_stage
from painminer.pipeline import synthesize as synth_stage
from painminer.config import Config, load_config
from painminer.db import DB
from painminer.delivery import document as document_stage
from painminer.pipeline.embed import Embedder, embed_findings
from painminer.pipeline.fetch import fetch_source
from painminer.llm import LLM


@dataclass
class ScanSummary:
    run_id: int | None = None
    per_source: dict[str, int] = field(default_factory=dict)   # source -> new items
    source_errors: dict[str, str] = field(default_factory=dict)
    items_fetched: int = 0
    ready: int = 0
    duplicate: int = 0
    judged: int = 0
    empty: int = 0
    triaged_out: int = 0
    deep_reads: int = 0
    findings_created: int = 0
    capped: int = 0
    failed: int = 0
    embedded: int = 0
    new_clusters: int = 0
    judge_seconds: float = 0.0
    seconds: float = 0.0
    stopped_on_budget: bool = False
    # Pass 3 / delivery (§3, §4)
    synthesis: "synth_stage.SynthesisResult | None" = None
    digest_path: str | None = None
    synthesis_error: str | None = None

    @property
    def seconds_per_item(self) -> float:
        return self.judge_seconds / self.deep_reads if self.deep_reads else 0.0


def _noop(_msg: str) -> None:
    pass


def run_scan(
    db: DB,
    llm: LLM,
    config: Config,
    *,
    embedder: Embedder | None = None,
    progress=None,
) -> ScanSummary:
    progress = progress or _noop
    started = time.time()
    summary = ScanSummary()

    # Preflight (§7.6): abort before fetching if Machine B is unreachable.
    progress("Preflight: checking Machine B…")
    judge_stage.preflight(llm, config)

    run_started_iso = datetime.now(tz=timezone.utc).isoformat()
    run = db.insert("runs", {"started_at": run_started_iso})
    summary.run_id = run["id"]

    # --- fetch every enabled source; remember each fetch's start time --------
    sources = db.table("sources").select("name").eq("enabled", True).order("name").execute().data
    fetch_started_at: dict[str, str] = {}
    for s in sources:
        name = s["name"]
        try:
            fs = fetch_source(db, name)
            fetch_started_at[name] = fs.started_at
            summary.per_source[name] = fs.items_inserted
            summary.items_fetched += fs.items_inserted
            progress(f"Fetched {name}: {fs.items_inserted} new")
        except Exception as exc:                       # a bad source must not kill the run
            summary.source_errors[name] = f"{type(exc).__name__}: {exc}"
            summary.per_source[name] = 0
            progress(f"Fetch {name} FAILED: {exc}")

    # --- dedupe --------------------------------------------------------------
    ds = dedupe_stage.dedupe(db)
    summary.ready, summary.duplicate = ds.ready, ds.duplicate
    progress(f"Dedupe: {ds.ready} ready, {ds.duplicate} duplicate")

    # --- judge (the run budget lives here) -----------------------------------
    progress("Judging…")
    t0 = time.time()

    def judge_progress(js: judge_stage.JudgeRunSummary) -> None:
        progress(f"Judging: {js.items_judged} judged, {js.findings_created} findings…")

    js = judge_stage.run_judge(db, llm, config, progress=judge_progress)
    summary.judge_seconds = time.time() - t0
    summary.judged = js.items_judged
    summary.empty = js.empty_results
    summary.triaged_out = js.triaged_out
    summary.deep_reads = js.deep_reads
    summary.findings_created = js.findings_created
    summary.capped = js.capped_items
    summary.failed = js.items_failed
    summary.stopped_on_budget = js.stopped_on_budget

    # Judge succeeded: advance each fetched source's watermark to its fetch
    # start time (§5.1). Not done on a partial/budget stop if incomplete? The
    # ready queue persists, so advancing is safe — unprocessed rows stay ready
    # and are picked up next run; the window has been covered up to fetch start.
    for name, started_at in fetch_started_at.items():
        db.table("sources").update(
            {"last_successful_fetch_at": started_at}
        ).eq("name", name).execute()

    # --- embed + cluster + rank ---------------------------------------------
    embedder = embedder or Embedder.from_config(config)
    summary.embedded = embed_findings(db, embedder, config.embed_batch_size)
    progress(f"Embedded {summary.embedded} findings")

    cs = cluster_stage.cluster_findings(db, llm, config)
    summary.new_clusters = cs.new_clusters
    progress(f"Clustered: {cs.new_clusters} new clusters, {cs.auto_merges + cs.tiebreak_merges} merges")

    rs = rank_stage.rank_clusters(db)
    progress(f"Ranked {rs.ranked} clusters")

    # --- pass 3: synthesis + the digest document (§3, §4) --------------------
    progress("Synthesising the night…")
    try:
        result = synth_stage.synthesize_night(
            db, llm, config, run_started_at=run_started_iso
        )
        summary.synthesis = result
        markdown = document_stage.render_markdown(result, day=date_cls.today())
        path = document_stage.write_digest(markdown, digests_dir=config.digests_dir)
        summary.digest_path = str(path)
        progress(f"Wrote digest {path} ({result.n_findings} findings synthesised)")
    except Exception as exc:   # synthesis must not lose an otherwise-good run
        summary.synthesis_error = f"{type(exc).__name__}: {exc}"
        progress(f"Synthesis FAILED: {exc}")

    summary.seconds = time.time() - started
    db.table("runs").update({
        "finished_at": datetime.now(tz=timezone.utc).isoformat(),
        "items_fetched": summary.items_fetched,
        "findings_created": summary.findings_created,
        "errors": [f"{k}: {v}" for k, v in summary.source_errors.items()],
    }).eq("id", summary.run_id).execute()

    progress("Scan complete.")
    return summary


def main() -> None:
    config = load_config()
    db = DB.from_config()
    llm = LLM(config)
    s = run_scan(db, llm, config, progress=lambda m: print(m, flush=True))
    print(
        f"\nSCAN DONE run#{s.run_id}: fetched {s.items_fetched}, "
        f"{s.triaged_out} triaged out, {s.deep_reads} deep-read, "
        f"{s.findings_created} findings, {s.new_clusters} new clusters, "
        f"{s.seconds_per_item:.1f}s/deep-read"
        + (f", digest {s.digest_path}" if s.digest_path else "")
        + (f", SYNTHESIS ERROR: {s.synthesis_error}" if s.synthesis_error else "")
    )


if __name__ == "__main__":
    main()
