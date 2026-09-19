"""judge.preflight — verifies llm_model and embed_model are
both loaded on Machine B before a run starts (task: embedding-on-B preflight
extension). Offline: monkeypatches judge.send_telegram so no network call is
made, and restores it afterward (plain functions, no pytest fixtures, matching
the repo's test-runner conventions)."""

from __future__ import annotations

import painminer.pipeline.judge as judge_mod
from painminer.config import Config
from painminer.pipeline.judge import PreflightError, preflight


class FakeLLM:
    def __init__(self, models=None, raise_on_list=None):
        self._models = models or []
        self._raise = raise_on_list

    def list_models(self):
        if self._raise is not None:
            raise self._raise
        return self._models


def _config(**overrides):
    base = dict(
        supabase_url="u", supabase_service_key="k", telegram_token="t",
        telegram_chat_id="c", llm_base_url="http://x/v1", llm_model="qwen-extract",
        embed_model="bge-small",
    )
    base.update(overrides)
    return Config(**base)


def _patched(fn):
    """Run fn with judge_mod.send_telegram replaced by a recording stub;
    returns (result_or_exception, alerts_sent)."""
    alerts = []
    original = judge_mod.send_telegram

    def fake_send_telegram(config, text):
        alerts.append(text)
        return True

    judge_mod.send_telegram = fake_send_telegram
    try:
        fn(alerts)
    finally:
        judge_mod.send_telegram = original
    return alerts


def test_preflight_passes_when_all_three_models_loaded():
    llm = FakeLLM(["qwen-extract", "bge-small", "qwen3.6-35b"])
    config = _config()

    def run(alerts):
        preflight(llm, config)   # must not raise
        assert alerts == []      # and must not alert

    _patched(run)


def test_preflight_raises_and_alerts_on_missing_embed_model():
    llm = FakeLLM(["qwen-extract", "qwen3.6-35b"])   # embed_model absent
    config = _config()

    def run(alerts):
        try:
            preflight(llm, config)
            assert False, "expected PreflightError"
        except PreflightError as exc:
            assert "embed_model" in str(exc)
        assert len(alerts) == 1
        assert "embed_model" in alerts[0]

    _patched(run)


def test_preflight_raises_on_missing_llm_model():
    llm = FakeLLM(["bge-small", "qwen3.6-35b"])   # llm_model absent
    config = _config()

    def run(alerts):
        try:
            preflight(llm, config)
            assert False, "expected PreflightError"
        except PreflightError as exc:
            assert "llm_model" in str(exc)

    _patched(run)


def test_preflight_reports_all_missing_models_together():
    llm = FakeLLM([])   # nothing loaded
    config = _config()

    def run(alerts):
        try:
            preflight(llm, config)
            assert False, "expected PreflightError"
        except PreflightError as exc:
            msg = str(exc)
            assert "llm_model" in msg and "embed_model" in msg

    _patched(run)


def test_preflight_transport_failure_still_alerts_and_raises():
    llm = FakeLLM(raise_on_list=ConnectionError("machine B unreachable"))
    config = _config()

    def run(alerts):
        try:
            preflight(llm, config)
            assert False, "expected PreflightError"
        except PreflightError as exc:
            assert "preflight failed" in str(exc)
        assert len(alerts) == 1
        assert "cannot reach" in alerts[0].lower()

    _patched(run)
