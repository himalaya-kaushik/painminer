"""Judge stage — passes 1 and 2 of the v3 reader (brief §3).

Per `ready` item, two calls instead of one:
  1. Triage (cheap, reasoning off): worth a closer look at all? Most items are
     killed here — committed as `done` with zero findings. This is haystack
     reduction (§3, pass 1).
  2. Deep read (survivors only): fetch the item's thread context first, then
     the v2 extraction (0..n findings) with the reader profile in the prompt
     (§3, pass 2).

Contract otherwise unchanged:
- Preflight GET /v1/models before doing anything; on failure, a Telegram
  message naming the VPN and Machine B, then abort (§7.6).
- One warm-up call to absorb cold start (§7.6).
- Deep read: parse+validate; on failure retry once with the error appended;
  on second failure mark the item `failed` (§7.5).
- On success, record findings + null raw_text + mark done in one transaction
  (record_judgement, §6).
- Respect max_run_minutes; on expiry stop cleanly, leaving the watermark (§5.2).

The watermark itself is advanced by /scan after a full judge pass, not here —
a partial run must never mark unprocessed data as seen (§5.1).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

import openai

from painminer.config import Config, load_config
from painminer.db import DB
from painminer.llm import LLM
from painminer.notify import send_telegram
from painminer.prompt import REQUIRED_KEYS, build_messages
from painminer.pipeline import queue_ops
from painminer.pipeline import thread_context as thread_context_mod
from painminer.pipeline.fetch import _make_client

CLAIM_BATCH = 20
PER_SOURCE_CLAIM = 5          # per-source claim size for the round-robin (§6)
MAX_FINDINGS_PER_THREAD = 2   # cap findings per parent HN thread per run
# A single LLM timeout/connection error is a blip (Machine B can briefly evict
# a model under memory pressure with several loaded at once) and must not kill
# a multi-hour run. Only abort once several IN A ROW fail, which is the real
# signal Machine B is genuinely down. Each failed item is released, not burned
# as an attempt, so nothing is lost either way.
MAX_CONSECUTIVE_LLM_ERRORS = 3


def thread_key(item: dict) -> str:
    """Grouping key for the per-thread cap.

    Falls back to the item id when a source has no thread concept, so such
    items are never lumped together under a shared null key.
    """
    return item.get("thread_id") or f"_item:{item['id']}"


def cap_for_thread(thread_counts: dict[str, int], key: str, n_findings: int) -> int:
    """How many of an item's findings may be committed under the per-thread cap.

    Pure and side-effect free (unit-testable). Returns 0..n_findings.
    """
    remaining = MAX_FINDINGS_PER_THREAD - thread_counts.get(key, 0)
    return max(0, min(n_findings, remaining))


class PreflightError(RuntimeError):
    """Machine B is unreachable; the run must abort before fetching."""


@dataclass
class JudgeResult:
    ok: bool
    findings: list[dict] | None
    raw_outputs: list[str] = field(default_factory=list)  # raw model text, per attempt
    error: str | None = None
    attempts: int = 0


@dataclass
class JudgeRunSummary:
    items_judged: int = 0        # items processed to `done` (triaged-out + deep-read)
    items_failed: int = 0
    findings_created: int = 0
    empty_results: int = 0       # items that yielded zero findings (incl. triaged-out)
    triaged_out: int = 0         # killed by pass-1 triage (worth_reading=false)
    deep_reads: int = 0          # items that passed triage into pass 2
    capped_items: int = 0        # retired without judging: thread already at cap
    stopped_on_budget: bool = False


# --- preflight --------------------------------------------------------------

def preflight(llm: LLM, config: Config) -> None:
    """Verify Machine B is reachable and every model this run needs is loaded.

    Checks llm_model (triage + deep read), embed_model (clustering, §8 — never
    loaded locally on Machine A), and synthesis_model when it names a distinct
    model. On any failure, a Telegram alert naming what's missing, then abort
    before fetching (§7.6).
    """
    try:
        models = llm.list_models()
    except Exception as exc:  # any transport/HTTP failure
        send_telegram(
            config,
            "painminer: cannot reach the LLM on Machine B at "
            f"{config.llm_base_url}. Is Machine B awake and LM Studio serving? "
            "Is the VPN off on this machine? (a VPN blocks LAN access to "
            "Machine B). Aborting the run.",
        )
        raise PreflightError(f"preflight failed: {exc}") from exc

    required = {"llm_model": config.llm_model, "embed_model": config.embed_model}
    if config.synthesis_model and config.synthesis_model != config.llm_model:
        required["synthesis_model"] = config.synthesis_model
    missing = {name: value for name, value in required.items() if value not in models}
    if missing:
        detail = ", ".join(f"{name}={value!r}" for name, value in missing.items())
        send_telegram(
            config,
            f"painminer: Machine B is up but not every model is loaded "
            f"({detail} not in: {', '.join(models)}). Aborting the run.",
        )
        raise PreflightError(f"model(s) not loaded on Machine B: {detail}")


# --- parse + validate -------------------------------------------------------

def parse_and_validate(raw: str) -> tuple[list[dict] | None, str | None]:
    """Return (findings, None) on success or (None, error) on failure (§7.5)."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"not valid JSON: {exc}"

    if not isinstance(data, list):
        return None, "top-level value must be a JSON array"

    cleaned: list[dict] = []
    for i, elem in enumerate(data):
        if not isinstance(elem, dict):
            return None, f"element {i} is not an object"
        missing = REQUIRED_KEYS - elem.keys()
        if missing:
            return None, f"element {i} missing keys: {sorted(missing)}"
        conf = elem["confidence"]
        if not isinstance(conf, (int, float)) or not 0 <= conf <= 1:
            return None, f"element {i} confidence must be a number in [0,1]"
        if not str(elem["kind"]).strip():
            return None, f"element {i} has an empty kind"
        if not str(elem["statement"]).strip():
            return None, f"element {i} has an empty statement"
        cleaned.append({k: elem[k] for k in REQUIRED_KEYS})

    return cleaned, None


# --- judge one item ---------------------------------------------------------

def judge_item(
    llm: LLM,
    source_text: str,
    *,
    context: str | None = None,
    engagement: dict | None = None,
    build_min_points: int = 50,
    build_min_comments: int = 30,
) -> JudgeResult:
    """Pass-2 deep read: extract once, then retry once with the error (§7.5)."""
    result = JudgeResult(ok=False, findings=None)
    error: str | None = None

    for attempt in range(2):  # first attempt, then one retry
        result.attempts = attempt + 1
        messages = build_messages(
            source_text,
            retry_error=error if attempt else None,
            context=context,
            engagement=engagement,
            build_min_points=build_min_points,
            build_min_comments=build_min_comments,
        )
        raw = llm.complete(messages)
        result.raw_outputs.append(raw)
        findings, error = parse_and_validate(raw)
        if error is None:
            result.ok = True
            result.findings = findings
            result.error = None
            return result

    result.error = error
    return result


# --- commit -----------------------------------------------------------------

def commit_judgement(db: DB, item_id: int, findings: list[dict]) -> int:
    """Record findings + null raw_text + mark done, atomically. Returns count."""
    resp = db.client.rpc(
        "record_judgement", {"p_item_id": item_id, "p_findings": findings}
    ).execute()
    return resp.data if isinstance(resp.data, int) else len(findings)


# --- run --------------------------------------------------------------------

def run_judge(
    db: DB,
    llm: LLM,
    config: Config,
    *,
    max_items: int | None = None,
    progress=None,
    context_fetcher=thread_context_mod.fetch_thread_context,
    http_client=None,
) -> JudgeRunSummary:
    """Drain the ready queue until empty, max_items, or the time budget.

    Each item is triaged (pass 1); survivors get thread context fetched and a
    deep read (pass 2). `progress`, if given, is called with the running
    JudgeRunSummary after each claimed batch. `context_fetcher`/`http_client`
    are injectable so tests can run offline (pass None to disable fetching).
    """
    preflight(llm, config)
    llm.warmup()

    summary = JudgeRunSummary()
    # max_run_minutes <= 0 means no cap: drain the whole ready queue.
    deadline = time.time() + config.max_run_minutes * 60 if config.max_run_minutes > 0 else None
    thread_counts: dict[str, int] = {}   # findings committed per thread, this run

    owns_client = False
    client = http_client
    if client is None and context_fetcher is not None:
        client = _make_client()
        owns_client = True

    def _budget_hit() -> bool:
        return deadline is not None and time.time() >= deadline

    def _limit_hit() -> bool:
        return max_items is not None and (
            summary.items_judged + summary.items_failed
        ) >= max_items

    consecutive_llm_errors = 0   # resets on any successful LLM call

    def _handle_llm_error(item: dict, exc: Exception) -> None:
        """A single LLM call failed after its own internal retries. Release
        the item (not the item's fault, don't burn a strike) and only abort
        the whole run once several calls IN A ROW have failed — that's the
        real "Machine B is down" signal, not a normal blip under load."""
        nonlocal consecutive_llm_errors
        queue_ops.release(db, item["id"])
        consecutive_llm_errors += 1
        if consecutive_llm_errors >= MAX_CONSECUTIVE_LLM_ERRORS:
            raise PreflightError(
                f"{consecutive_llm_errors} consecutive LLM errors mid-run "
                f"(last: {exc}); aborting — this looks like a genuine outage, "
                "not a blip."
            ) from exc

    def _process(item: dict) -> None:
        """Triage (pass 1) then, for survivors, deep read (pass 2). Updates
        summary/thread_counts in place. May raise PreflightError — only on a
        sustained run of LLM errors (see _handle_llm_error).

        Both calls share ONE try/except and the error streak resets only once
        an item's LLM work fully completes: resetting right after a triage
        success would let a healthy triage endpoint mask a broken deep-read
        endpoint forever (every new item's triage success would zero the
        streak before deep read ever got 3 in a row), which would silently
        spin — releasing and re-claiming items — rather than ever detecting
        the outage.
        """
        nonlocal consecutive_llm_errors
        key = thread_key(item)
        if thread_counts.get(key, 0) >= MAX_FINDINGS_PER_THREAD:
            commit_judgement(db, item["id"], [])   # thread at cap: retire, no call
            summary.capped_items += 1
            return

        text = item["raw_text"] or ""
        try:
            verdict = llm.triage(text[: config.triage_max_chars])
            if not verdict.get("worth_reading"):
                commit_judgement(db, item["id"], [])
                summary.items_judged += 1
                summary.empty_results += 1
                summary.triaged_out += 1
                consecutive_llm_errors = 0
                return

            summary.deep_reads += 1
            context = None
            if context_fetcher is not None and client is not None:
                context = context_fetcher(item, client, max_chars=config.thread_context_chars)
            result = judge_item(
                llm,
                text,
                context=context,
                engagement=item.get("metadata") or None,
                build_min_points=config.build_min_points,
                build_min_comments=config.build_min_comments,
            )
        except openai.APIError as exc:
            _handle_llm_error(item, exc)
            return
        consecutive_llm_errors = 0   # both calls succeeded

        if result.ok and result.findings is not None:
            allowed = cap_for_thread(thread_counts, key, len(result.findings))
            n = commit_judgement(db, item["id"], result.findings[:allowed])
            thread_counts[key] = thread_counts.get(key, 0) + n
            summary.items_judged += 1
            summary.findings_created += n
            if not result.findings:
                summary.empty_results += 1
        else:
            queue_ops.mark_failed(db, item["id"])
            summary.items_failed += 1

    # Round-robin across enabled sources so a high-volume source (HN) can't
    # drain the budget before the others are reached (§6). Each cycle claims a
    # small batch per source; a source that returns nothing drops out of the
    # rotation. With no sources listed we fall back to a single global claim.
    rows = db.table("sources").select("name").eq("enabled", True).order("name").execute().data
    active: list[str | None] = [r["name"] for r in rows] or [None]

    try:
        while active and not _budget_hit() and not _limit_hit():
            progressed = False
            for name in list(active):
                if _budget_hit() or _limit_hit():
                    break
                n = PER_SOURCE_CLAIM
                if max_items is not None:
                    n = min(n, max_items - (summary.items_judged + summary.items_failed))
                    if n <= 0:
                        break
                batch = queue_ops.claim(db, n, source=name)
                if not batch:
                    active.remove(name)          # this source is drained
                    continue
                progressed = True
                for item in batch:
                    if _budget_hit() or _limit_hit():
                        queue_ops.release(db, item["id"])   # give it back
                        if _budget_hit():
                            summary.stopped_on_budget = True
                        break
                    _process(item)
                if progress is not None:
                    progress(summary)
                if summary.stopped_on_budget:
                    break
            if not progressed:
                break
    finally:
        if owns_client and client is not None:
            client.close()

    return summary


def main() -> None:
    db = DB.from_config()
    config = load_config()
    llm = LLM(config)
    summary = run_judge(db, llm, config)
    print(
        f"judge: {summary.items_judged} processed "
        f"({summary.triaged_out} triaged out, {summary.deep_reads} deep-read), "
        f"{summary.findings_created} findings, "
        f"{summary.capped_items} thread-capped, {summary.items_failed} failed"
        + (" [stopped on time budget]" if summary.stopped_on_budget else "")
    )


if __name__ == "__main__":
    main()
