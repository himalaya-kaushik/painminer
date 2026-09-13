"""Configuration for painminer, loaded from .env.

No secrets in code (brief ground rules). Everything comes from .env, which
already exists with the six keys below. `load_config()` reads it and fails
loudly if anything is missing, rather than letting a stage crash later with a
confusing error.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    supabase_url: str
    supabase_service_key: str
    telegram_token: str
    telegram_chat_id: str
    llm_base_url: str
    llm_model: str


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
    for field in fields(Config):
        env_key = _ENV_KEYS[field.name]
        raw = os.getenv(env_key)
        if raw is None or raw.strip() == "":
            missing.append(env_key)
        else:
            values[field.name] = raw.strip()

    if missing:
        raise RuntimeError(
            "Missing required environment variable(s): " + ", ".join(missing)
        )

    return Config(**values)
