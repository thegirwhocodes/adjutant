"""Local FAISS retrieval over the regulation corpus.

Each FAISS index is built once by scripts/ingest_corpus.py (or
scripts/build_tier_indexes.py for the tiered architecture) and persisted to
disk. At query time we embed the query, top-K search, and return chunks with
source labels.

Multi-index support
-------------------
This module supports loading multiple FAISS indexes simultaneously — one per
tier (hot/warm/cold). The HOT tier loads via the default path; WARM and COLD
tier processes set FAISS_INDEX_PATH per-process to point at their own corpus.

The shared embedder is loaded once and reused across calls; FAISS indexes are
keyed by their on-disk path so a process serving WARM doesn't accidentally pick
up the HOT corpus.
"""

import logging
import os
import pickle
from pathlib import Path

import faiss
import numpy as np
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer

# Load .env before reading any env var (see note in llm.py).
load_dotenv()

log = logging.getLogger("adjutant.rag")

DEFAULT_INDEX_PATH = Path(os.getenv("FAISS_INDEX_PATH", ".faiss_index"))
EMBED_MODEL_NAME = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
DEFAULT_SCORE_THRESHOLD = float(os.getenv("RAG_SCORE_THRESHOLD", "0.35"))

_embedder: SentenceTransformer | None = None
# Indexes are keyed by their resolved on-disk path so a process can hold
# multiple indexes (rare; the tiered architecture runs one process per tier).
_indexes: dict[str, tuple[faiss.Index, list[dict]]] = {}


def _get_embedder() -> SentenceTransformer:
    global _embedder
    if _embedder is None:
        log.info(f"Loading embedder: {EMBED_MODEL_NAME}")
        _embedder = SentenceTransformer(EMBED_MODEL_NAME)
    return _embedder


def _load_index(index_path: Path) -> tuple[faiss.Index | None, list[dict]]:
    """Load (and cache) the FAISS index + chunk metadata at `index_path`."""
    key = str(index_path.resolve())
    cached = _indexes.get(key)
    if cached is not None:
        return cached

    idx_file = index_path / "faiss.bin"
    meta_file = index_path / "chunks.pkl"
    if not idx_file.exists() or not meta_file.exists():
        log.warning(
            f"FAISS index not found at {index_path}. "
            f"Run: python scripts/ingest_corpus.py "
            f"(or scripts/build_tier_indexes.py for tiered)"
        )
        return None, []

    index = faiss.read_index(str(idx_file))
    with open(meta_file, "rb") as f:
        chunks = pickle.load(f)
    _indexes[key] = (index, chunks)
    log.info(
        f"Loaded FAISS index from {index_path} "
        f"({index.ntotal} vectors, {len(chunks)} chunks)"
    )
    return index, chunks


def retrieve(
    query: str,
    top_k: int = 5,
    *,
    index_path: Path | str | None = None,
    score_threshold: float | None = None,
) -> list[dict]:
    """Embed query, search FAISS, return list of chunks with source metadata.

    Each chunk: {"text": str, "source": str, "section": str, "score": float}

    A relevance threshold is applied: chunks scoring below `score_threshold`
    (default `RAG_SCORE_THRESHOLD` env var, fallback 0.35) are dropped. This is
    what makes out-of-corpus refusal architecturally reliable — if the user
    asks about a regulation we don't have, RAG returns 0 relevant chunks and
    the LLM is forced to refuse.

    Parameters
    ----------
    index_path : optional override for the on-disk FAISS index location.
        Used by tier servers (WARM/COLD) to point at a per-tier corpus. When
        omitted, falls back to FAISS_INDEX_PATH env var, then `.faiss_index`.
    score_threshold : optional override for the relevance cut-off. Tier
        servers may relax this to surface more candidates that the cross-
        encoder reranker can then filter.
    """
    if index_path is None:
        index_path = DEFAULT_INDEX_PATH
    elif isinstance(index_path, str):
        index_path = Path(index_path)

    if score_threshold is None:
        score_threshold = DEFAULT_SCORE_THRESHOLD

    embedder = _get_embedder()
    index, chunks = _load_index(index_path)
    if index is None or not chunks:
        return []

    qv = embedder.encode([query], normalize_embeddings=True).astype("float32")
    scores, idxs = index.search(qv, top_k)

    out: list[dict] = []
    for score, i in zip(scores[0], idxs[0]):
        if i < 0 or i >= len(chunks):
            continue
        if float(score) < score_threshold:
            continue
        c = dict(chunks[i])
        c["score"] = float(score)
        out.append(c)

    if not out:
        log.info(
            f"RAG: 0 chunks above threshold {score_threshold} for query "
            f"{query[:60]!r} — LLM will refuse (or higher tier may answer)"
        )
    return out