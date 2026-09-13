"""config.load_config — required-key validation and optional overrides."""

import os

import config


def _with_env(env, fn):
    """Run fn with os.environ replaced by env and load_dotenv disabled."""
    saved_environ = dict(os.environ)
    saved_loader = config.load_dotenv
    os.environ.clear()
    os.environ.update(env)
    config.load_dotenv = lambda *a, **k: None
    try:
        return fn()
    finally:
        os.environ.clear()
        os.environ.update(saved_environ)
        config.load_dotenv = saved_loader


_REQUIRED = {
    "SUPABASE_URL": "u",
    "SUPABASE_SERVICE_KEY": "k",
    "TELEGRAM_TOKEN": "t",
    "TELEGRAM_CHAT_ID": "c",
    "LLM_BASE_URL": "http://b/v1",
    "LLM_MODEL": "m",
}


def test_loads_all_required():
    cfg = _with_env(dict(_REQUIRED), config.load_config)
    assert cfg.supabase_url == "u" and cfg.llm_model == "m"


def test_defaults_for_optional():
    cfg = _with_env(dict(_REQUIRED), config.load_config)
    assert cfg.max_run_minutes == 60
    assert cfg.llm_timeout_seconds == 30.0
    assert cfg.llm_api_key == "lm-studio"


def test_optional_overrides():
    env = dict(_REQUIRED, MAX_RUN_MINUTES="15", LLM_TIMEOUT_SECONDS="45")
    cfg = _with_env(env, config.load_config)
    assert cfg.max_run_minutes == 15 and cfg.llm_timeout_seconds == 45.0


def test_missing_key_lists_all_missing():
    env = {"SUPABASE_URL": "u"}  # everything else missing
    try:
        _with_env(env, config.load_config)
    except RuntimeError as exc:
        msg = str(exc)
        assert "TELEGRAM_TOKEN" in msg and "LLM_MODEL" in msg
        return
    raise AssertionError("expected RuntimeError for missing keys")


def test_blank_value_counts_as_missing():
    env = dict(_REQUIRED, LLM_MODEL="   ")
    try:
        _with_env(env, config.load_config)
    except RuntimeError as exc:
        assert "LLM_MODEL" in str(exc)
        return
    raise AssertionError("expected RuntimeError for blank value")
