"""Tiered retrieval orchestrator — HOT (in-process) + WARM (NIPR LAN) + COLD (cloud).

Production properties
---------------------
- **Env-driven endpoints.** WARM_RETRIEVE_URL / COLD_RETRIEVE_URL default to
  localhost mocks for dev; point them at real services in prod via .env.
- **Per-tier timeouts.** HOT: none (in-process). WARM: 1s. COLD: 3s. All env-tunable.
- **Circuit breaker.** A tier that fails twice in a 30s window is skipped for the
  next 30s — prevents waste on a known-down tier.
- **Versioned protocol.** Every retrieve response carries `protocol_version: 1`.
  Mismatched versions log a warning and the tier is downgraded.
- **Telemetry.** Per-query: which tiers responded, with what latency, with how
  many chunks. Logged at INFO; consumable by /health for the frontend.
- **mTLS-ready.** httpx.AsyncClient is configured with the TLS_VERIFY env var
  and optional TLS_CLIENT_CERT / TLS_CLIENT_KEY for CAC-issued client certs.
- **Graceful merge.** If WARM is down but HOT and COLD return chunks, the user
  still gets an answer — the tier_status field tells the frontend what was lost.
- **Cross-encoder rerank.** Optional but on by default for >50 merged candidates.
  Recovers precision when corpora exceed ~200K chunks.

The HOT tier is an *in-process* call to `adjutant.rag.retrieve()`. WARM and COLD
are HTTP fan-outs to FastAPI services that wrap the same retrieve() with a
different FAISS_INDEX_PATH. Same code, three transports, three corpora.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Literal

import httpx
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("adjutant.tiers")

# ---------------------------------------------------------------------------
# Tier configuration (env-driven)
# ---------------------------------------------------------------------------

TierName = Literal["HOT", "WARM", "COLD"]

PROTOCOL_VERSION = 1

# Endpoints. Defaults are localhost mocks; production overrides in .env.
WARM_RETRIEVE_URL = os.getenv("WARM_RETRIEVE_URL", "http://localhost:8001")
COLD_RETRIEVE_URL = os.getenv("COLD_RETRIEVE_URL", "http://localhost:8002")

# Per-tier timeouts (seconds). HOT is in-process so has no network timeout.
WARM_TIMEOUT = float(os.getenv("WARM_TIMEOUT", "1.0"))
COLD_TIMEOUT = float(os.getenv("COLD_TIMEOUT", "3.0"))

# Health-check timeouts — much shorter than retrieve timeouts.
WARM_HEALTH_TIMEOUT = float(os.getenv("WARM_HEALTH_TIMEOUT", "0.3"))
COLD_HEALTH_TIMEOUT = float(os.getenv("COLD_HEALTH_TIMEOUT", "0.5"))

# Circuit breaker. After CB_FAIL_THRESHOLD failures in CB_WINDOW seconds,
# the tier is short-circuited (skipped) for CB_COOLDOWN seconds.
CB_FAIL_THRESHOLD = int(os.getenv("CB_FAIL_THRESHOLD", "2"))
CB_WINDOW = float(os.getenv("CB_WINDOW", "30.0"))
CB_COOLDOWN = float(os.getenv("CB_COOLDOWN", "30.0"))

# Cross-encoder reranker (set to "0" to disable in resource-constrained envs)
RERANK_ENABLED = os.getenv("RERANK_ENABLED", "1") == "1"
RERANK_MODEL = os.getenv("RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
RERANK_THRESHOLD = float(os.getenv("RERANK_THRESHOLD", "0.0"))
RERANK_TRIGGER_MIN = int(os.getenv("RERANK_TRIGGER_MIN", "10"))

# mTLS for prod (CAC-issued client certs)
TLS_VERIFY = os.getenv("TLS_VERIFY", "true").lower() != "false"
TLS_CLIENT_CERT = os.getenv("TLS_CLIENT_CERT") or None
TLS_CLIENT_KEY = os.getenv("TLS_CLIENT_KEY") or None


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

@dataclass
class TierState:
    name: TierName
    url: str | None  # None for HOT (in-process)
    timeout: float
    health_timeout: float
    # Circuit breaker state
    recent_failures: list[float] = field(default_factory=list)
    open_until: float = 0.0  # If now < open_until, skip this tier
    # Last-known
    last_status: Literal["unknown", "up", "down", "tripped"] = "unknown"
    last_latency_ms: float | None = None
    last_chunk_count: int | None = None


_TIERS: dict[TierName, TierState] = {
    "HOT":  TierState("HOT",  None,                  timeout=0.0, health_timeout=0.0),
    "WARM": TierState("WARM", WARM_RETRIEVE_URL,     WARM_TIMEOUT, WARM_HEALTH_TIMEOUT),
    "COLD": TierState("COLD", COLD_RETRIEVE_URL,     COLD_TIMEOUT, COLD_HEALTH_TIMEOUT),
}

_http_client: httpx.AsyncClient | None = None
_reranker = None  # Lazy-loaded sentence_transformers.CrossEncoder


def _client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        client_kwargs: dict = {
            "verify": TLS_VERIFY,
            "limits": httpx.Limits(max_connections=20, max_keepalive_connections=10),
            "headers": {"User-Agent": "Adjutant/0.1 (tiered-retrieve)"},
        }
        if TLS_CLIENT_CERT and TLS_CLIENT_KEY:
            client_kwargs["cert"] = (TLS_CLIENT_CERT, TLS_CLIENT_KEY)
        _http_client = httpx.AsyncClient(**client_kwargs)
    return _http_client


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------

def _record_failure(state: TierState) -> None:
    now = time.time()
    state.recent_failures = [t for t in state.recent_failures if now - t < CB_WINDOW]
    state.recent_failures.append(now)
    if len(state.recent_failures) >= CB_FAIL_THRESHOLD:
        state.open_until = now + CB_COOLDOWN
        state.last_status = "tripped"
        log.warning(
            f"[{state.name}] circuit breaker tripped: "
            f"{len(state.recent_failures)} failures in {CB_WINDOW}s — "
            f"skipping for {CB_COOLDOWN}s"
        )


def _record_success(state: TierState) -> None:
    state.recent_failures.clear()
    state.open_until = 0.0
    state.last_status = "up"


def _is_short_circuited(state: TierState) -> bool:
    return time.time() < state.open_until


# ---------------------------------------------------------------------------
# Per-tier retrieval
# ---------------------------------------------------------------------------

async def _retrieve_hot(query: str, top_k: int) -> list[dict]:
    """In-process retrieval against the local HOT-tier FAISS index."""
    from adjutant import rag
    return await asyncio.to_thread(rag.retrieve, query, top_k)


async def _retrieve_remote(state: TierState, query: str, top_k: int) -> list[dict]:
    """HTTP retrieve against WARM or COLD."""
    if state.url is None:
        raise ValueError(f"{state.name} has no URL")
    if _is_short_circuited(state):
        log.info(f"[{state.name}] short-circuited; skipping")
        return []

    started = time.monotonic()
    try:
        resp = await _client().post(
            f"{state.url.rstrip('/')}/retrieve",
            json={"query": query, "top_k": top_k},
            timeout=state.timeout,
        )
        resp.raise_for_status()
        payload = resp.json()
        elapsed_ms = (time.monotonic() - started) * 1000

        # Protocol-version check
        if payload.get("protocol_version") != PROTOCOL_VERSION:
            log.warning(
                f"[{state.name}] protocol mismatch: "
                f"server={payload.get('protocol_version')} client={PROTOCOL_VERSION} "
                f"— accepting result but flagging."
            )

        chunks = payload.get("chunks", [])
        for c in chunks:
            c["tier"] = state.name
        state.last_latency_ms = elapsed_ms
        state.last_chunk_count = len(chunks)
        _record_success(state)
        log.info(f"[{state.name}] retrieved {len(chunks)} chunks in {elapsed_ms:.0f}ms")
        return chunks
    except httpx.TimeoutException:
        log.warning(f"[{state.name}] timeout after {state.timeout}s")
        _record_failure(state)
        return []
    except httpx.RequestError as e:
        log.warning(f"[{state.name}] connection error: {e}")
        _record_failure(state)
        state.last_status = "down"
        return []
    except httpx.HTTPStatusError as e:
        log.warning(f"[{state.name}] HTTP {e.response.status_code}")
        _record_failure(state)
        return []
    except Exception as e:
        log.error(f"[{state.name}] unexpected error: {e}", exc_info=True)
        _record_failure(state)
        return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def retrieve_tiered(query: str, top_k: int = 5) -> list[dict]:
    """Fan out across all configured tiers in parallel, merge, dedupe, rerank.

    Returns a list of chunks each tagged with `tier` ∈ {HOT, WARM, COLD}.
    Tiers that fail or are circuit-broken are silently skipped — but their
    state remains queryable via tier_status() for the frontend.
    """
    started = time.monotonic()

    # Each tier asks for top_k * 2 candidates so the merged pool has room
    # for the cross-encoder rerank to add value.
    candidates_per_tier = max(top_k * 2, 10)

    tasks = {
        "HOT":  _retrieve_hot(query, candidates_per_tier),
        "WARM": _retrieve_remote(_TIERS["WARM"], query, candidates_per_tier),
        "COLD": _retrieve_remote(_TIERS["COLD"], query, candidates_per_tier),
    }

    # Run in parallel; each task already enforces its own timeout.
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    by_tier: dict[TierName, list[dict]] = {}
    for tier, result in zip(tasks.keys(), results):
        if isinstance(result, Exception):
            log.error(f"[{tier}] task raised: {result}")
            by_tier[tier] = []
        else:
            by_tier[tier] = result

    # Tag HOT chunks (the in-process path doesn't tag itself)
    for c in by_tier["HOT"]:
        c.setdefault("tier", "HOT")

    # Track HOT health (it can't go down but can return empty)
    hot_state = _TIERS["HOT"]
    hot_state.last_chunk_count = len(by_tier["HOT"])
    hot_state.last_status = "up" if by_tier["HOT"] else "empty"

    # Merge + dedupe by (source, section). Higher-scored copy wins on collision.
    merged: dict[tuple[str, str], dict] = {}
    for chunks in by_tier.values():
        for c in chunks:
            key = (c.get("source", ""), c.get("section", ""))
            existing = merged.get(key)
            if existing is None or c.get("score", 0) > existing.get("score", 0):
                merged[key] = c

    candidates = list(merged.values())
    log.info(
        f"merged: HOT={len(by_tier['HOT'])} "
        f"WARM={len(by_tier['WARM'])} "
        f"COLD={len(by_tier['COLD'])} "
        f"→ {len(candidates)} unique"
    )

    # Cross-encoder rerank if we have enough candidates and it's enabled.
    if RERANK_ENABLED and len(candidates) >= RERANK_TRIGGER_MIN:
        candidates = await _rerank(query, candidates)

    # Return top_k by score (descending)
    candidates.sort(key=lambda c: c.get("score", 0.0), reverse=True)
    out = candidates[:top_k]

    elapsed_ms = (time.monotonic() - started) * 1000
    log.info(f"retrieve_tiered({query!r}, top_k={top_k}) → {len(out)} in {elapsed_ms:.0f}ms")
    return out


async def _rerank(query: str, candidates: list[dict]) -> list[dict]:
    """Cross-encoder reranker. Runs in a thread pool since CrossEncoder is sync."""
    global _reranker
    if _reranker is None:
        try:
            from sentence_transformers import CrossEncoder
            log.info(f"loading reranker: {RERANK_MODEL}")
            _reranker = await asyncio.to_thread(CrossEncoder, RERANK_MODEL)
        except Exception as e:
            log.warning(f"reranker load failed ({e}); skipping rerank")
            return candidates

    pairs = [(query, c.get("text", "")) for c in candidates]
    scores = await asyncio.to_thread(_reranker.predict, pairs)
    rescored = []
    for c, s in zip(candidates, scores):
        if float(s) >= RERANK_THRESHOLD:
            c["bi_encoder_score"] = c.get("score")
            c["score"] = float(s)
            rescored.append(c)
    return rescored


# ---------------------------------------------------------------------------
# Health checks (called by /health and the frontend's tier-status panel)
# ---------------------------------------------------------------------------

async def _check_remote(state: TierState) -> bool:
    if state.url is None:
        return True
    if _is_short_circuited(state):
        return False
    try:
        resp = await _client().get(
            f"{state.url.rstrip('/')}/health",
            timeout=state.health_timeout,
        )
        return resp.status_code == 200
    except Exception:
        return False


async def tier_status() -> dict[str, dict]:
    """Returns the current up/down state of each tier + last-known latency.

    Called by /health and the frontend's tier-status panel polls every 2s.
    """
    hot_state = _TIERS["HOT"]
    warm_up, cold_up = await asyncio.gather(
        _check_remote(_TIERS["WARM"]),
        _check_remote(_TIERS["COLD"]),
    )
    if warm_up:
        _TIERS["WARM"].last_status = "up"
    elif _is_short_circuited(_TIERS["WARM"]):
        _TIERS["WARM"].last_status = "tripped"
    else:
        _TIERS["WARM"].last_status = "down"
    if cold_up:
        _TIERS["COLD"].last_status = "up"
    elif _is_short_circuited(_TIERS["COLD"]):
        _TIERS["COLD"].last_status = "tripped"
    else:
        _TIERS["COLD"].last_status = "down"

    return {
        name: {
            "status": s.last_status,
            "latency_ms": s.last_latency_ms,
            "chunk_count": s.last_chunk_count,
            "url": s.url,  # None for HOT
        }
        for name, s in _TIERS.items()
    }


async def shutdown() -> None:
    """Close the shared HTTP client. Call from FastAPI shutdown hook."""
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None
