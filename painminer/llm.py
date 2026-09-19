"""LM Studio (Machine B) client — the one coupling point, `LLM_BASE_URL` (§12).

Wraps the OpenAI-compatible API with schema-constrained JSON decoding, a
timeout, and up to three attempts on transient network errors. v3 drives four
different calls through it:

  * triage()      — pass 1, cheap, reasoning off, tiny output.
  * complete()    — pass 2 deep read, findings schema, reasoning per config.
  * synthesize()  — pass 3, one call, reasoning on, large output.
  * embed()       — finding-statement embeddings for clustering (§8). Machine A
                    never loads a model locally (no torch): embedding is just
                    another Machine B endpoint, same client, same retry
                    contract as every other call here.

Thinking is on by default in LM Studio and burns ~95% of tokens, so
`reasoning_effort` defaults to "none" everywhere and is only raised for the
passes where judgment actually matters (deep read, synthesis) via config.
"""

from __future__ import annotations

import json
import time
from typing import Any

from openai import APIConnectionError, APITimeoutError, OpenAI

from painminer.config import Config, load_config
from painminer.prompt import (
    FINDINGS_SCHEMA,
    TRIAGE_SCHEMA,
    build_triage_messages,
)

MAX_NETWORK_ATTEMPTS = 3          # §7.6: three attempts, then fail
_RETRYABLE = (APIConnectionError, APITimeoutError)


def _response_format(name: str, schema: dict) -> dict:
    return {
        "type": "json_schema",
        "json_schema": {"name": name, "strict": True, "schema": schema},
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

    # --- embeddings (§8) -------------------------------------------------

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts via Machine B's /v1/embeddings.

        Same retry contract as complete(): up to MAX_NETWORK_ATTEMPTS on a
        transient network error, other errors (e.g. a 4xx) propagate
        immediately. Returns one vector per input text, in order.
        """
        last_exc: Exception | None = None
        for attempt in range(1, MAX_NETWORK_ATTEMPTS + 1):
            try:
                resp = self.client.embeddings.create(
                    model=self.config.embed_model, input=texts
                )
                return [d.embedding for d in resp.data]
            except _RETRYABLE as exc:
                last_exc = exc
                if attempt < MAX_NETWORK_ATTEMPTS:
                    time.sleep(min(2 ** (attempt - 1), 5))
        assert last_exc is not None
        raise last_exc

    # --- core structured completion -----------------------------------------

    def _structured(
        self,
        messages: list[dict[str, Any]],
        *,
        schema: dict,
        schema_name: str,
        reasoning_effort: str = "none",
        max_tokens: int = 2000,
        timeout: float | None = None,
        model: str | None = None,
    ) -> str:
        """One schema-constrained completion; returns the raw content string.

        Retries transient network errors up to MAX_NETWORK_ATTEMPTS; other
        errors (e.g. a 4xx) propagate immediately.
        """
        last_exc: Exception | None = None
        for attempt in range(1, MAX_NETWORK_ATTEMPTS + 1):
            try:
                resp = self.client.chat.completions.create(
                    model=model or self.model,
                    messages=messages,
                    temperature=0,
                    response_format=_response_format(schema_name, schema),
                    extra_body={"reasoning_effort": reasoning_effort},
                    max_tokens=max_tokens,
                    **({"timeout": timeout} if timeout is not None else {}),
                )
                return resp.choices[0].message.content or ""
            except _RETRYABLE as exc:
                last_exc = exc
                if attempt < MAX_NETWORK_ATTEMPTS:
                    time.sleep(min(2 ** (attempt - 1), 5))
        assert last_exc is not None
        raise last_exc

    # --- pass 1: triage ------------------------------------------------------

    def triage(self, source_text: str) -> dict:
        """Pass-1 triage: {"worth_reading": bool, "one_line": str}.

        Reasoning stays off — this runs on everything, so speed wins. A
        malformed answer is treated conservatively as worth_reading=True (a
        false positive is cheap; a dropped signal is not).
        """
        raw = self._structured(
            build_triage_messages(source_text),
            schema=TRIAGE_SCHEMA,
            schema_name="triage",
            reasoning_effort="none",
            max_tokens=120,
        )
        try:
            data = json.loads(raw)
            return {
                "worth_reading": bool(data.get("worth_reading")),
                "one_line": str(data.get("one_line") or ""),
            }
        except (json.JSONDecodeError, AttributeError):
            return {"worth_reading": True, "one_line": ""}

    # --- pass 2: deep read ---------------------------------------------------

    def complete(self, messages: list[dict[str, Any]]) -> str:
        """Pass-2 deep-read completion (findings schema). Raw content string."""
        return self._structured(
            messages,
            schema=FINDINGS_SCHEMA,
            schema_name="findings",
            reasoning_effort=self.config.deep_read_reasoning,
            max_tokens=2000,
        )

    # --- pass 3: synthesis ---------------------------------------------------

    def synthesize(self, messages: list[dict[str, Any]], schema: dict) -> str:
        """Pass-3 synthesis: one call, reasoning on, large output budget."""
        return self._structured(
            messages,
            schema=schema,
            schema_name="briefing",
            reasoning_effort=self.config.synthesis_reasoning,
            max_tokens=self.config.synthesis_max_tokens,
            timeout=self.config.synthesis_timeout_seconds,
            model=self.config.synthesis_model or None,
        )

    # --- clustering tiebreak (§8) -------------------------------------------

    def same_underlying_thing(self, statement_a: str, statement_b: str) -> bool:
        """Clustering tiebreak (§8): do two statements describe the same thing?

        Used only in the ambiguous similarity band. Schema-constrained to a
        boolean so the answer is always parseable.
        """
        schema = {
            "type": "object",
            "properties": {"same": {"type": "boolean"}},
            "required": ["same"],
            "additionalProperties": False,
        }
        for attempt in range(1, MAX_NETWORK_ATTEMPTS + 1):
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You decide whether two short statements describe the "
                                "same underlying thing (same specific problem, build, or "
                                "signal). Answer with a JSON object {\"same\": true|false}."
                            ),
                        },
                        {"role": "user", "content": f"A: {statement_a}\nB: {statement_b}"},
                    ],
                    temperature=0,
                    response_format=_response_format(
                        "tiebreak",
                        schema,
                    ),
                    extra_body={"reasoning_effort": "none"},
                    max_tokens=50,
                )
                return bool(json.loads(resp.choices[0].message.content or "{}").get("same"))
            except _RETRYABLE:
                if attempt < MAX_NETWORK_ATTEMPTS:
                    time.sleep(min(2 ** (attempt - 1), 5))
        return False   # on persistent failure, don't merge (safer: new cluster)
