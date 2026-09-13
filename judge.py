"""Judge stage: one LLM call per `ready` item, extract 0..n findings (§7).

Contract:
- Preflight GET /v1/models before doing anything; on failure, a Telegram
  message naming the VPN and Machine B, then abort (§7.6).
- One warm-up call to absorb cold start (§7.6).
- Per item: judge, parse+validate against the schema; on failure retry once
  with the error appended; on second failure mark the item `failed` (§7.5).
- On success, record findings + null raw_text + mark done in one transaction
  (record_judgement, §6).
- Respect max_run_minutes; on expiry stop cleanly, leaving the watermark (§5.2).

The watermark itself is advanced by /scan (Phase 7) after a full judge pass,
not here — a partial run must never mark unprocessed data as seen (§5.1).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

import openai

from config import Config, load_config
from db import DB
from llm import LLM
from notify import send_telegram
from prompt import REQUIRED_KEYS, build_messages
import queue_ops

CLAIM_BATCH = 20
MAX_FINDINGS_PER_THREAD = 2   # cap findings per parent HN thread per run


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
    items_judged: int = 0
    items_failed: int = 0
    findings_created: int = 0
    empty_results: int = 0
    capped_items: int = 0        # retired without judging: thread already at cap
    stopped_on_budget: bool = False


# --- preflight --------------------------------------------------------------

def preflight(llm: LLM, config: Config) -> None:
    """Verify Machine B is reachable. On failure, alert and raise (§7.6)."""
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

    if config.llm_model not in models:
        send_telegram(
            config,
            f"painminer: Machine B is up but model {config.llm_model!r} is not "
            f"loaded (have: {', '.join(models)}). Aborting the run.",
        )
        raise PreflightError(f"model {config.llm_model!r} not loaded on Machine B")


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

def judge_item(llm: LLM, source_text: str) -> JudgeResult:
    """Judge once, then retry once with the error appended (§7.5)."""
    result = JudgeResult(ok=False, findings=None)
    error: str | None = None

    for attempt in range(2):  # first attempt, then one retry
        result.attempts = attempt + 1
        messages = build_messages(source_text, retry_error=error if attempt else None)
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
) -> JudgeRunSummary:
    """Drain the ready queue until empty, max_items, or the time budget."""
    preflight(llm, config)
    llm.warmup()

    summary = JudgeRunSummary()
    deadline = time.time() + config.max_run_minutes * 60
    thread_counts: dict[str, int] = {}   # findings committed per thread, this run

    while max_items is None or summary.items_judged + summary.items_failed < max_items:
        if time.time() >= deadline:
            summary.stopped_on_budget = True
            break

        remaining = CLAIM_BATCH
        if max_items is not None:
            remaining = min(
                CLAIM_BATCH, max_items - (summary.items_judged + summary.items_failed)
            )
        batch = queue_ops.claim(db, remaining)
        if not batch:
            break

        for item in batch:
            if time.time() >= deadline:
                queue_ops.release(db, item["id"])  # give it back, don't burn it
                summary.stopped_on_budget = True
                break

            key = thread_key(item)
            if thread_counts.get(key, 0) >= MAX_FINDINGS_PER_THREAD:
                # Thread already at its per-run cap: retire the item without
                # spending an LLM call on it.
                commit_judgement(db, item["id"], [])
                summary.capped_items += 1
                continue

            try:
                result = judge_item(llm, item["raw_text"] or "")
            except openai.APIError as exc:
                # Transient LLM/network trouble: release, don't burn a strike.
                queue_ops.release(db, item["id"])
                summary.stopped_on_budget = False
                raise PreflightError(f"LLM error mid-run: {exc}") from exc

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

        if summary.stopped_on_budget:
            break

    return summary


def main() -> None:
    db = DB.from_config()
    config = load_config()
    llm = LLM(config)
    summary = run_judge(db, llm, config)
    print(
        f"judge: {summary.items_judged} judged "
        f"({summary.empty_results} empty), {summary.findings_created} findings, "
        f"{summary.capped_items} thread-capped, {summary.items_failed} failed"
        + (" [stopped on time budget]" if summary.stopped_on_budget else "")
    )


if __name__ == "__main__":
    main()
