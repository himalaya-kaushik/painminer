"""Embedding stage (§8): embed finding statements for clustering only.

Embeds via Machine B's OpenAI-compatible `/v1/embeddings` endpoint —
`bge-small`, 384-dim — the same client and retry contract as every other LLM
call (LLM.embed(), §7.6). Machine A never loads an embedding model locally:
no torch, no sentence-transformers. `config.embed_model` names the model id
LM Studio reports, not a Hugging Face repo path.

Embeddings are used for clustering, never for filtering (§8). Written to
findings.embedding (halfvec 384) via the set_embedding RPC, which carries the
text->halfvec cast.

    .venv/bin/python -m painminer.pipeline.embed
"""

from __future__ import annotations

import numpy as np

from painminer.config import Config, load_config
from painminer.db import DB
from painminer.llm import LLM


def format_vector(vec) -> str:
    """pgvector text form, e.g. '[0.1,0.2,...]'. Accepts any iterable of floats."""
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def parse_vector(text: str) -> np.ndarray:
    """Parse pgvector text form back to a float array."""
    return np.fromstring(text.strip().lstrip("[").rstrip("]"), sep=",")


class Embedder:
    """Encodes finding statements to vectors via Machine B's /v1/embeddings."""

    def __init__(self, llm: LLM, batch_size: int = 64) -> None:
        self.llm = llm
        self.batch_size = batch_size

    def encode(self, texts: list[str]) -> list[list[float]]:
        return self.llm.embed(texts)

    @classmethod
    def from_config(cls, config: Config) -> "Embedder":
        return cls(LLM(config), config.embed_batch_size)


def embed_findings(db: DB, embedder: Embedder, batch_size: int = 64) -> int:
    """Embed every finding whose embedding is still null. Idempotent."""
    total = 0
    while True:
        rows = (
            db.table("findings")
            .select("id, statement")
            .is_("embedding", "null")
            .order("id")
            .limit(batch_size)
            .execute()
            .data
        )
        if not rows:
            break
        vecs = embedder.encode([r["statement"] for r in rows])
        for row, vec in zip(rows, vecs):
            db.client.rpc(
                "set_embedding", {"p_id": row["id"], "p_vec": format_vector(vec)}
            ).execute()
        total += len(rows)
        if len(rows) < batch_size:
            break
    return total


def main() -> None:
    config = load_config()
    db = DB.from_config()
    embedder = Embedder.from_config(config)
    n = embed_findings(db, embedder, config.embed_batch_size)
    print(f"embed: {n} findings embedded")


if __name__ == "__main__":
    main()
