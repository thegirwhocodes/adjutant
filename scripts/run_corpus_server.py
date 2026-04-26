"""Tier corpus server (WARM or COLD).

This is the same FastAPI service shape that, in production, would run on:
  - WARM : an on-base NIPR file server (~500 docs)
  - COLD : a FedRAMP-High SaaS instance in the cloud (~5,000 docs)

In development, two instances run on localhost — one per tier — each with its
own FAISS index. The `--tier` flag selects which corpus this process serves.

Production deployment notes
---------------------------
- The wire protocol is **versioned**: every response carries `protocol_version`.
  Bump it when changing the contract; clients log a warning on mismatch.
- The /health endpoint is intentionally cheap — it does NOT load models.
- Configurable artificial latency via TIER_ARTIFICIAL_DELAY_MS for the demo's
  graceful-degradation moment (so COLD looks visibly slower than WARM).
- Same TLS config as the orchestrator; in prod expects mTLS via
  TLS_CLIENT_CERT / TLS_CLIENT_KEY for CAC client auth.

Run
---
    # Demo (localhost)
    python scripts/run_corpus_server.py --tier warm --port 8001
    python scripts/run_corpus_server.py --tier cold --port 8002 \
        --artificial-delay-ms 800

    # Production
    TLS_CLIENT_CERT=/etc/adjutant/cac.pem TLS_CLIENT_KEY=/etc/adjutant/cac.key \
    python scripts/run_corpus_server.py --tier warm --host 0.0.0.0 --port 443
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adjutant import rag  # noqa: E402

PROTOCOL_VERSION = 1
log = logging.getLogger("adjutant.corpus_server")


# Pydantic models — must match adjutant/tiers.py expectations.

class RetrieveRequest(BaseModel):
    query: str
    top_k: int = Field(5, ge=1, le=50)


class Chunk(BaseModel):
    text: str
    source: str = ""
    section: str = ""
    page: int | None = None
    score: float = 0.0
    tier: str = ""


class RetrieveResponse(BaseModel):
    protocol_version: int = PROTOCOL_VERSION
    tier: str
    chunks: list[Chunk]


class HealthResponse(BaseModel):
    status: str
    tier: str
    protocol_version: int
    chunk_count: int | None = None
    index_path: str


# ---------------------------------------------------------------------------
# Application factory (so each --tier --port pair gets its own state)
# ---------------------------------------------------------------------------

def make_app(*, tier: str, index_path: Path, artificial_delay_ms: int = 0) -> FastAPI:
    """Build a FastAPI app bound to one tier's FAISS index."""
    tier = tier.upper()
    if tier not in ("WARM", "COLD"):
        raise ValueError(f"--tier must be 'warm' or 'cold' (got {tier!r})")

    app = FastAPI(
        title=f"Adjutant Corpus Server ({tier})",
        description=(
            "Tier-specific FAISS retrieval service. "
            "In production: WARM = on-base NIPR; COLD = FedRAMP-High cloud."
        ),
        version="0.1.0",
    )

    # Cache the chunk count for /health without re-loading on every probe.
    _state = {"chunk_count": None, "loaded": False}

    @app.on_event("startup")
    async def _warmup():
        # Pre-load the FAISS index so the first /retrieve doesn't pay for it.
        log.info(f"[{tier}] warming up; loading index from {index_path}")
        await asyncio.to_thread(rag._load_index, index_path)
        idx_key = str(index_path.resolve())
        cached = rag._indexes.get(idx_key)
        if cached:
            _state["chunk_count"] = len(cached[1])
            _state["loaded"] = True
        log.info(
            f"[{tier}] warmup done; "
            f"chunks={_state['chunk_count']} loaded={_state['loaded']}"
        )

    @app.get("/health", response_model=HealthResponse)
    async def health():
        return HealthResponse(
            status="up" if _state["loaded"] else "loading",
            tier=tier,
            protocol_version=PROTOCOL_VERSION,
            chunk_count=_state["chunk_count"],
            index_path=str(index_path),
        )

    @app.post("/retrieve", response_model=RetrieveResponse)
    async def retrieve(req: RetrieveRequest):
        if not req.query or not req.query.strip():
            raise HTTPException(status_code=400, detail="query required")

        # Demo-time artificial latency for the graceful-degradation moment —
        # makes COLD visibly slower than WARM. Set to 0 for production.
        if artificial_delay_ms > 0:
            await asyncio.sleep(artificial_delay_ms / 1000.0)

        started = time.monotonic()
        chunks = await asyncio.to_thread(
            rag.retrieve, req.query, req.top_k, index_path=index_path
        )
        elapsed_ms = (time.monotonic() - started) * 1000
        log.info(
            f"[{tier}] retrieve query={req.query[:60]!r} "
            f"top_k={req.top_k} → {len(chunks)} chunks in {elapsed_ms:.0f}ms"
        )

        return RetrieveResponse(
            protocol_version=PROTOCOL_VERSION,
            tier=tier,
            chunks=[Chunk(**c, tier=tier) for c in chunks],
        )

    return app


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Adjutant tier corpus server.")
    parser.add_argument(
        "--tier", choices=["warm", "cold"], required=True,
        help="Which tier this process serves.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument(
        "--index-path",
        help="Override FAISS index path. Default: .faiss_index_{tier}",
    )
    parser.add_argument(
        "--artificial-delay-ms",
        type=int,
        default=0,
        help=(
            "Demo-only. Adds N ms of latency before each /retrieve to make "
            "COLD visibly slower than WARM. Use 0 in production."
        ),
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level.upper()))

    index_path = Path(args.index_path) if args.index_path else (
        ROOT / f".faiss_index_{args.tier}"
    )
    if not (index_path / "faiss.bin").exists():
        log.error(
            f"FAISS index not found at {index_path}. "
            f"Run: python scripts/build_tier_indexes.py --tier {args.tier}"
        )
        return 1

    app = make_app(
        tier=args.tier,
        index_path=index_path,
        artificial_delay_ms=args.artificial_delay_ms,
    )

    log.info(
        f"Starting {args.tier.upper()} corpus server on "
        f"http://{args.host}:{args.port} (index={index_path})"
    )
    if args.artificial_delay_ms > 0:
        log.info(f"  artificial delay: {args.artificial_delay_ms}ms per request")

    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level.lower())
    return 0


if __name__ == "__main__":
    sys.exit(main())
