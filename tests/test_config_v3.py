"""Offline tests for painminer.config v3 defaults and env overrides.

load_config() calls load_dotenv(), which may load the repo .env and override
values in surprising ways in this environment. To avoid depending on that
file's contents, the "defaults" test constructs Config directly instead of
going through load_config().
"""

from __future__ import annotations

import os

from painminer import config

_REQUIRED = dict(
    SUPABASE_URL="u",
    SUPABASE_SERVICE_KEY="k",
    TELEGRAM_TOKEN="t",
    TELEGRAM_CHAT_ID="c",
    LLM_BASE_URL="http://x/v1",
    LLM_MODEL="m",
)


def _with_env(env, fn):
    old = dict(os.environ)
    os.environ.clear()
    os.environ.update(env)
    try:
        return fn()
    finally:
        os.environ.clear()
        os.environ.update(old)


def test_defaults_via_direct_construction():
    cfg = config.Config(
        supabase_url="u",
        supabase_service_key="k",
        telegram_token="t",
        telegram_chat_id="c",
        llm_base_url="x",
        llm_model="m",
    )
    assert cfg.digest_someone_built_cap == 3
    assert cfg.digest_total_cap == 8
    assert cfg.synthesis_model == "qwen/qwen3.6-35b-a3b"
    assert cfg.synthesis_reasoning == "none"
    assert cfg.max_run_minutes == 0


def test_env_overrides_via_load_config():
    env = dict(_REQUIRED, DIGEST_TOTAL_CAP="5", SYNTHESIS_MODEL="foo")
    cfg = _with_env(env, config.load_config)
    assert cfg.digest_total_cap == 5
    assert cfg.synthesis_model == "foo"
