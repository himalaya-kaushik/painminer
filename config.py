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
    max_run_minutes: int = 60          # wall-clock run budget (§5.2)
    llm_timeout_seconds: float = 30.0  # per-call timeout (§7.6)
    llm_api_key: str = "lm-studio"     # LM Studio ignores it; the SDK needs one
    # `build` engagement rule: a launch with no stated problem still counts if
    # the submission cleared one of these (configurable).
    build_min_points: int = 50
    build_min_comments: int = 30


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

    return Config(**values, **optional)
