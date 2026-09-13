"""Embedding stage (§8): embed finding statements for clustering only.

`bge-small-en-v1.5` via sentence-transformers, MPS with CPU fallback, model
loaded once per run, batched. Embeddings are used for clustering, never for
filtering (§8). Written to findings.embedding (halfvec 384) via the set_embedding
RPC, which carries the text->halfvec cast.

    python embed.py
"""

from __future__ import annotations

import numpy as np

from config import Config, load_config
from db import DB


def format_vector(vec: np.ndarray) -> str:
    """pgvector text form, e.g. '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def parse_vector(text: str) -> np.ndarray:
    """Parse pgvector text form back to a float array."""
    return np.fromstring(text.strip().lstrip("[").rstrip("]"), sep=",")


class Embedder:
    """Loads the embedding model once; encodes normalized vectors for cosine."""

    def __init__(self, model_name: str, batch_size: int = 64) -> None:
        from sentence_transformers import SentenceTransformer
        import torch

        device = "mps" if torch.backends.mps.is_available() else "cpu"
        try:
            self.model = SentenceTransformer(model_name, device=device)
        except Exception:                       # MPS init can fail on some setups
            self.model = SentenceTransformer(model_name, device="cpu")
        self.batch_size = batch_size

    def encode(self, texts: list[str]) -> np.ndarray:
        return self.model.encode(
            texts,
            batch_size=self.batch_size,
            normalize_embeddings=True,          # unit vectors: cosine == dot
            convert_to_numpy=True,
        )

    @classmethod
    def from_config(cls, config: Config) -> "Embedder":
        return cls(config.embed_model, config.embed_batch_size)


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
