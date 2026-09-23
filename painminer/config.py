"""Configuration for painminer, loaded from .env.

No secrets in code (brief ground rules). Everything comes from .env, which
already exists with the six keys below. `load_config()` reads it and fails
loudly if anything is missing, rather than letting a stage crash later with a
confusing error.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    supabase_url: str
    supabase_service_key: str
    telegram_token: str
    telegram_chat_id: str
    llm_base_url: str
    llm_model: str

    # Optional settings with defaults (not required in .env).
    # Wall-clock cap on the judge stage. 0 (the default) = no cap: judge until
    # the ready queue is drained. The machine is a dedicated local box, so
    # there is nothing to budget against; set a positive value only to bound a
    # cloud/cron run.
    max_run_minutes: int = 0
    # Per-call timeout (§7.6). MUST exceed Machine B's cold start: warmup was
    # measured at 53.7s, and LM Studio evicts/reloads models when several are
    # resident against its memory ceiling — so any call landing during a reload
    # takes far longer than a naive 30s. A too-short timeout here was the root
    # cause of both the "crashed on one timeout" and "one item retried 46
    # times" failures: the timeouts were never the item's fault, they were the
    # budget being shorter than a model load.
    llm_timeout_seconds: float = 120.0
    llm_api_key: str = "lm-studio"     # LM Studio ignores it; the SDK needs one
    # `build` engagement rule: a launch with no stated problem still counts if
    # the submission cleared one of these (configurable).
    build_min_points: int = 50
    build_min_comments: int = 30
    # Embedding + clustering (§8). Embedding runs on Machine B via
    # /v1/embeddings, same as every other model call — Machine A never loads a
    # model locally (no torch/sentence-transformers). embed_model is the model
    # id LM Studio reports (check `lms ps` or GET /v1/models), not a HF repo
    # path. Thresholds below are starting points to tune.
    embed_model: str = "text-embedding-bge-small-en-v1.5"
    embed_batch_size: int = 64
    cluster_merge_threshold: float = 0.90    # >= -> auto-merge
    cluster_tiebreak_low: float = 0.75       # [low, merge) -> LLM tiebreak; below -> new

    # Three-pass reader (v3 brief §3). Reasoning is off by default (LM Studio
    # thinks by default and it burns ~95% of tokens); raise only where judgment
    # matters. Values are LM Studio's reasoning_effort levels.
    # Pass-1 truncation. Triage only needs enough to judge plausibility, and
    # the deep read always sees the FULL text, so truncating here costs no
    # extraction quality — only candidate recall. Measured on real queue items
    # (boolean-only triage): 4000 chars = 0.70s/item, 1200 = 0.48s, with
    # identical verdicts. 2000 is the middle: keeps launch/announcement context
    # (the "Shipped" section depends on it) while roughly halving prefill.
    triage_max_chars: int = 2000
    deep_read_reasoning: str = "none"  # pass 2; A/B "low" once running
    thread_context_chars: int = 6000   # cap on fetched thread context (pass 2)
    # Pass 3: qwen-extract is extraction-tuned — any reasoning_effort > none
    # spends the whole token budget on thinking and returns EMPTY content
    # (A/B'd live: "medium" -> unparseable, "none" -> clean JSON). Keep it off.
    synthesis_reasoning: str = "none"            # pass 3: the product
    # NB: there is deliberately no separate synthesis model. All three passes
    # run on llm_model. An earlier version pointed pass 3 at
    # "qwen/qwen3.6-35b-a3b" believing it was a bigger model, but that is just
    # another LM Studio alias for the same weights (a request for it comes back
    # `served as: qwen-extract`, byte-identical output). The digest quality win
    # attributed to it actually came from de-parroting SYNTHESIS_SYSTEM_PROMPT.
    synthesis_max_tokens: int = 6000             # room for a full briefing
    # Pass 3 is one long call and scales with the findings block: it took
    # ~149s on a ~33k-char prompt, so a backlog night (hundreds of findings)
    # needs real headroom or the digest dies after the whole judge pass.
    synthesis_timeout_seconds: float = 900.0
    synthesis_shortlist: int = 40      # top-N recurring clusters fed to pass 3
    # Hard cap on how many of tonight's findings go into the pass-3 prompt,
    # highest-confidence first. Without it a 3000-item backlog produces a
    # ~135k-char prompt that risks both the timeout and the context window.
    # 250 is still far more material than a 10-item digest can use.
    synthesis_max_findings: int = 250
    # When the cap above binds, no one source may fill more than this share
    # of it before the others are seen (unused slots are refilled). Measured
    # Sep 2026: arXiv alone was 40-50% of a night's findings.
    synthesis_max_source_share: float = 0.35
    digests_dir: str = "digests"       # where YYYY-MM-DD.md is written (§4)
    # Hard caps on the digest, enforced deterministically after synthesis so
    # they don't depend on the model obeying the prompt.
    digest_someone_built_cap: int = 3  # max items under "Someone built"
    digest_total_cap: int = 10         # max items across the whole briefing


# Maps a Config field to its .env variable name.
_ENV_KEYS = {
    "supabase_url": "SUPABASE_URL",
    "supabase_service_key": "SUPABASE_SERVICE_KEY",
    "telegram_token": "TELEGRAM_TOKEN",
    "telegram_chat_id": "TELEGRAM_CHAT_ID",
    "llm_base_url": "LLM_BASE_URL",
    "llm_model": "LLM_MODEL",
}


def load_config() -> Config:
    """Load and validate configuration from .env.

    Raises RuntimeError listing every missing key, so a fresh checkout gets
    one clear error instead of six.
    """
    load_dotenv()

    values: dict[str, str] = {}
    missing: list[str] = []
    for field_name, env_key in _ENV_KEYS.items():
        raw = os.getenv(env_key)
        if raw is None or raw.strip() == "":
            missing.append(env_key)
        else:
            values[field_name] = raw.strip()

    if missing:
        raise RuntimeError(
            "Missing required environment variable(s): " + ", ".join(missing)
        )

    # Optional overrides.
    optional: dict[str, object] = {}
    if os.getenv("MAX_RUN_MINUTES"):
        optional["max_run_minutes"] = int(os.environ["MAX_RUN_MINUTES"])
    if os.getenv("LLM_TIMEOUT_SECONDS"):
        optional["llm_timeout_seconds"] = float(os.environ["LLM_TIMEOUT_SECONDS"])
    if os.getenv("LLM_API_KEY"):
        optional["llm_api_key"] = os.environ["LLM_API_KEY"]
    if os.getenv("BUILD_MIN_POINTS"):
        optional["build_min_points"] = int(os.environ["BUILD_MIN_POINTS"])
    if os.getenv("BUILD_MIN_COMMENTS"):
        optional["build_min_comments"] = int(os.environ["BUILD_MIN_COMMENTS"])
    if os.getenv("CLUSTER_MERGE_THRESHOLD"):
        optional["cluster_merge_threshold"] = float(os.environ["CLUSTER_MERGE_THRESHOLD"])
    if os.getenv("CLUSTER_TIEBREAK_LOW"):
        optional["cluster_tiebreak_low"] = float(os.environ["CLUSTER_TIEBREAK_LOW"])
    if os.getenv("EMBED_MODEL"):
        optional["embed_model"] = os.environ["EMBED_MODEL"].strip()
    if os.getenv("EMBED_BATCH_SIZE"):
        optional["embed_batch_size"] = int(os.environ["EMBED_BATCH_SIZE"])
    if os.getenv("TRIAGE_MAX_CHARS"):
        optional["triage_max_chars"] = int(os.environ["TRIAGE_MAX_CHARS"])
    if os.getenv("DEEP_READ_REASONING"):
        optional["deep_read_reasoning"] = os.environ["DEEP_READ_REASONING"].strip()
    if os.getenv("THREAD_CONTEXT_CHARS"):
        optional["thread_context_chars"] = int(os.environ["THREAD_CONTEXT_CHARS"])
    if os.getenv("SYNTHESIS_REASONING"):
        optional["synthesis_reasoning"] = os.environ["SYNTHESIS_REASONING"].strip()
    if os.getenv("SYNTHESIS_MAX_TOKENS"):
        optional["synthesis_max_tokens"] = int(os.environ["SYNTHESIS_MAX_TOKENS"])
    if os.getenv("SYNTHESIS_TIMEOUT_SECONDS"):
        optional["synthesis_timeout_seconds"] = float(os.environ["SYNTHESIS_TIMEOUT_SECONDS"])
    if os.getenv("SYNTHESIS_SHORTLIST"):
        optional["synthesis_shortlist"] = int(os.environ["SYNTHESIS_SHORTLIST"])
    if os.getenv("SYNTHESIS_MAX_FINDINGS"):
        optional["synthesis_max_findings"] = int(os.environ["SYNTHESIS_MAX_FINDINGS"])
    if os.getenv("SYNTHESIS_MAX_SOURCE_SHARE"):
        optional["synthesis_max_source_share"] = float(os.environ["SYNTHESIS_MAX_SOURCE_SHARE"])
    if os.getenv("DIGESTS_DIR"):
        optional["digests_dir"] = os.environ["DIGESTS_DIR"].strip()
    if os.getenv("DIGEST_SOMEONE_BUILT_CAP"):
        optional["digest_someone_built_cap"] = int(os.environ["DIGEST_SOMEONE_BUILT_CAP"])
    if os.getenv("DIGEST_TOTAL_CAP"):
        optional["digest_total_cap"] = int(os.environ["DIGEST_TOTAL_CAP"])

    return Config(**values, **optional)
