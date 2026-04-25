"""Local FAISS retrieval over the regulation corpus.

The index is built once by scripts/ingest_corpus.py and persisted to disk.
At query time we embed the query, top-K search, and return chunks with source labels.
"""

import logging
import os
import pickle
from pathlib import Path

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

log = logging.getLogger("adjutant.rag")

INDEX_PATH = Path(os.getenv("FAISS_INDEX_PATH", ".faiss_index"))
EMBED_MODEL_NAME = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")

_embedder: SentenceTransformer | None = None
_index: faiss.Index | None = None
_chunks: list[dict] = []


def _load() -> None:
    """Lazy-load the embedder + FAISS index + chunk metadata."""
    global _embedder, _index, _chunks

    if _embedder is None:
        log.info(f"Loading embedder: {EMBED_MODEL_NAME}")
        _embedder = SentenceTransformer(EMBED_MODEL_NAME)

    if _index is None:
        idx_file = INDEX_PATH / "faiss.bin"
        meta_file = INDEX_PATH / "chunks.pkl"
        if not idx_file.exists() or not meta_file.exists():
            log.warning(
                f"FAISS index not found at {INDEX_PATH}. "
                f"Run: python scripts/ingest_corpus.py"
            )
            _chunks = []
            return
        _index = faiss.read_index(str(idx_file))
        with open(meta_file, "rb") as f:
            _chunks = pickle.load(f)
        log.info(f"Loaded FAISS index ({_index.ntotal} vectors, {len(_chunks)} chunks)")


def retrieve(query: str, top_k: int = 5) -> list[dict]:
    """Embed query, search FAISS, return list of chunks with source metadata.

    Each chunk: {"text": str, "source": str, "section": str, "score": float}
    """
    _load()
    if _index is None or not _chunks:
        return []

    qv = _embedder.encode([query], normalize_embeddings=True).astype("float32")
    scores, idxs = _index.search(qv, top_k)

    out: list[dict] = []
    for score, i in zip(scores[0], idxs[0]):
        if i < 0 or i >= len(_chunks):
            continue
        c = dict(_chunks[i])
        c["score"] = float(score)
        out.append(c)
    return out
