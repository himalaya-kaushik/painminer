"""LM Studio (Machine B) client — the one coupling point, `LLM_BASE_URL` (§12).

Wraps the OpenAI-compatible API with the judge's fixed contract (§7.6):
temperature 0, reasoning_effort "none" (thinking is on by default and burns
~95% of tokens), a 30s timeout, schema-constrained decoding, and up to three
attempts on transient network errors before giving up.
"""

from __future__ import annotations

import time
from typing import Any

from openai import APIConnectionError, APITimeoutError, OpenAI

from config import Config, load_config
from prompt import FINDINGS_SCHEMA

MAX_NETWORK_ATTEMPTS = 3          # §7.6: three attempts, then fail
_RETRYABLE = (APIConnectionError, APITimeoutError)

_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {"name": "findings", "strict": True, "schema": FINDINGS_SCHEMA},
}


class LLM:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.model = config.llm_model
        # max_retries=0: we run our own explicit retry loop.
        self.client = OpenAI(
            base_url=config.llm_base_url,
            api_key=config.llm_api_key,
            timeout=config.llm_timeout_seconds,
            max_retries=0,
        )

    @classmethod
    def from_config(cls, config: Config | None = None) -> "LLM":
        return cls(config or load_config())

    def list_models(self) -> list[str]:
        """Preflight probe (GET /v1/models). Raises if Machine B is unreachable."""
        return [m.id for m in self.client.models.list().data]

    def warmup(self) -> float:
        """One throwaway call to absorb the 20-30s cold start (§7.6). Returns secs."""
        started = time.time()
        self.complete([{"role": "user", "content": "warmup"}])
        return time.time() - started

    def complete(self, messages: list[dict[str, Any]]) -> str:
        """One schema-constrained completion; returns the raw content string.

        Retries transient network errors up to MAX_NETWORK_ATTEMPTS; other
        errors (e.g. a 4xx) propagate immediately.
        """
        last_exc: Exception | None = None
        for attempt in range(1, MAX_NETWORK_ATTEMPTS + 1):
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=0,
                    response_format=_RESPONSE_FORMAT,
                    extra_body={"reasoning_effort": "none"},
                    max_tokens=2000,
                )
                return resp.choices[0].message.content or ""
            except _RETRYABLE as exc:
                last_exc = exc
                if attempt < MAX_NETWORK_ATTEMPTS:
                    time.sleep(min(2 ** (attempt - 1), 5))
        assert last_exc is not None
        raise last_exc
