# Adjutant — Tiered Fallback Architecture (HOT / WARM / COLD)

**The thesis:** Adjutant should never block the soldier. Cloud is a luxury, not a dependency. Network is a sometimes-luxury, not a dependency. The on-device layer is the only thing that *must* always work; everything else gracefully enriches when reachable.

This is the architectural pattern Pentagon vocabulary calls **DDIL-native** (Denied / Degraded / Intermittent / Limited). EdgeRunner ($17.5M raised) ships pure on-device only — no enrichment when network is up. Adjutant goes one step further: **on-device foundation + opportunistic tier enrichment.**

---

## 1. The three tiers

| Tier | Lives on | Latency | Corpus | What it knows |
|---|---|---|---|---|
| **HOT** | The soldier's laptop, in-process | <50 ms | ~30 docs / ~80K chunks / ~150 MB | The 3 demo forms (DA-31, DD-1351-2, DA-4856) and their governing ARs. Common regulations every soldier touches monthly. |
| **WARM** | On-base NIPR file server | 100–500 ms | ~500 docs / ~500K chunks / ~1.5 GB | Broader Army admin: discipline, property, awards, body comp, substance abuse, in/out processing. The next ring of regulations. |
| **COLD** | FedRAMP-High SaaS in cloud | 500 ms – 3 s | ~5,000 docs / ~5M chunks / ~15 GB | Full APD + DTIC technical reports + eCFR Title 32 + Joint Pubs. Everything authoritative. |

The data does **not** trickle down — each tier has its own indexed corpus. WARM does not contain HOT (no need; HOT answers locally). COLD does not contain WARM. Each tier owns the documents that are most efficiently served *at that latency tier*.

**Why this partition matters.** A soldier asking about ordinary leave needs <100ms latency — that's HOT. A soldier asking about a rare urinalysis edge case can tolerate ~300ms — that's WARM. A soldier doing an AR 15-6 investigation read might tolerate ~1s for the underlying RAND analysis — that's COLD. Match latency budget to query frequency.

---

## 2. The protocol — same JSON, three transports

Every tier exposes the same `/retrieve` POST endpoint:

```json
POST /retrieve
{ "query": "How does ordinary leave accrue?", "top_k": 5 }

→ 200 OK
{
  "tier": "WARM",
  "chunks": [
    {"text": "...", "source": "AR 600-8-10", "section": "Paragraph 4-3",
     "score": 0.87, "page": 12}
  ]
}
```

HOT calls `adjutant.rag.retrieve()` directly (no HTTP). WARM and COLD are FastAPI mock servers in development (`scripts/run_corpus_server.py`) — same module, just bound to different ports with different `FAISS_INDEX_PATH`. In production, those endpoints are real services on different machines; the client code doesn't care.

---

## 3. Fan-out + merge

`adjutant.tiers.retrieve_tiered(query, top_k=5)`:

1. **Parallel fan-out.** `asyncio.gather()` issues HOT (in-process), WARM (HTTP localhost:8001), COLD (HTTP localhost:8002). Each tier has a per-tier timeout (HOT: none, WARM: 1s, COLD: 3s).
2. **Tier failure ≠ query failure.** If WARM times out or returns 5xx, log it, drop it from the merge, set `tier_status[WARM] = down`. The query still returns whatever HOT + COLD produced.
3. **Merge.** Concatenate chunks. Dedupe by `(source, section)` keeping the higher-scored copy.
4. **Cross-encoder rerank.** `cross-encoder/ms-marco-MiniLM-L-6-v2` re-scores all merged candidates. ~12 ms per candidate; 50 candidates = ~600 ms. Only runs on the merged pool, not on each tier's pool.
5. **Top-K with tier provenance.** Each returned chunk has a `tier` field. The frontend renders citation badges (HOT 🔵 / WARM 🟢 / COLD 🟡).

**Threshold logic.** If HOT returns chunks above the score threshold (currently 0.35 cosine), we *don't* short-circuit — we still wait for WARM/COLD. The point isn't to minimize calls; it's to maximize answer quality when network is up. Short-circuit only when:
- A tier *errors* (HTTP 5xx, timeout, ConnectionRefused → mark `down`, skip)
- A tier returns empty chunks (mark `empty`, skip from merge but still count as up)

---

## 4. The kill-shot demo upgrade

Existing Beat 4 (offline kill shot): pull wifi cable. Adjutant still works.

**New Beat 3.5 (graceful degradation):**

> "Watch this. All three tiers green — HOT on this laptop, WARM on the simulated base server, COLD on the simulated cloud. I'll ask about leave."
> [Voice query → answer with mixed `[HOT]` `[WARM]` `[COLD]` citation badges visible on screen]
>
> "Now I kill the cold tier — that's the cloud going dark."
> [`Ctrl-C` on cold server process. Cold LED flashes red.]
> [Same voice query → answer still arrives, citations now only `[HOT]` `[WARM]`]
>
> "Now I kill the warm tier — that's the base server going dark too."
> [`Ctrl-C` on warm server. Warm LED red.]
> [Same voice query → answer still arrives, citations only `[HOT]`]
>
> "And finally I pull the wifi cable. Adjutant is now fully air-gapped — no internet, no LAN, nothing. Same query."
> [Wifi yank. Browser badge OFFLINE.]
> [Same voice query → answer arrives unchanged. `[HOT]` only.]
>
> "Soldier is never blocked. The richer tiers enrich when reachable. The local tier is what *guarantees* uptime."

That's the new kill shot. It plays in 90 seconds and proves three things:
1. **Multi-tier orchestration works** (Novelty + Tech Difficulty)
2. **Graceful degradation is real** (Reliability — Mohindra-bait)
3. **Soldier-first design** (Problem-Solution Fit — never blocked)

---

## 5. Production deployment shape

What changes between localhost demo and field deployment:

| Component | Demo (localhost) | Field deployment |
|---|---|---|
| HOT | In-process Python module | Same — in-process Python module on the soldier's NIPR laptop |
| WARM endpoint | `http://localhost:8001` | `https://corpus.unit.army.mil` (on-base file server, NIPR LAN) |
| COLD endpoint | `http://localhost:8002` | `https://corpus.adjutant.mil` (FedRAMP-High SaaS, requires SIPR/IL5) |
| Auth | None | mTLS with CAC-issued client certs |
| Corpus update | Manual rebuild | `manifest.json` opportunistic delta-sync (see §6) |
| WARM corpus owner | Demo's mock server | Brigade S6 or Garrison G6 |
| COLD corpus owner | Demo's mock server | DoD Chief Data Officer, federated APD/DTIC/eCFR mirror |

**Authority mapping.** WARM is owned at the unit level so it can carry unit-specific guidance (the brigade's local SOPs, base-specific JTR supplements, training calendars). COLD is owned centrally because it's the universal authoritative corpus — no unit forks AR 600-8-10. This separation means a brigade can update its WARM tier weekly without going through the DoD CDO.

---

## 6. Sync semantics (Phase 2 — not for the demo)

Each tier has a `manifest.json` listing every doc with `last_modified` + `sha256`. When a higher tier (WARM or COLD) is reachable:

1. Adjutant's HOT layer pulls `manifest.json` from WARM (and from COLD if reachable).
2. Diffs against its local `manifest.json`.
3. For docs that changed *AND* are in the HOT-tier whitelist, Adjutant downloads them, re-embeds the changed chunks, updates the local FAISS index in place. (FAISS supports `add_with_ids` + `remove_ids` for incremental updates.)
4. Logs the delta to the soldier as a one-line notification: *"Adjutant updated 3 regulations from base server."*

Same pattern WARM ← COLD: the base server's WARM tier opportunistically pulls deltas from the COLD cloud tier when it has bandwidth.

This is the *eventually consistent* model. Each tier becomes a cache of the tier above it, with the soldier's HOT tier as the authoritative leaf for low-latency offline use.

**Phase 2 deliverable.** Not in the demo. But the architecture diagram includes it, and the README mentions it as the v2 path.

---

## 7. Why this beats EdgeRunner architecturally

EdgeRunner is `pure offline only`. Their architecture says: model + data baked into the binary, ship the binary, re-ship the binary to update. That's fine for SOF behind enemy lines where there's no friendly network.

Adjutant's user is *not* SOF behind enemy lines. Adjutant's user is a junior NCO at Fort Bragg with intermittent NIPR access. Pure offline is wasteful — when the soldier *does* have NIPR, why not enrich? When the soldier *doesn't*, the local layer carries the load.

In a single sentence: **EdgeRunner trades enrichment for guaranteed uptime. Adjutant gets both.**

---

## 8. Files involved

| New | Path | What |
|---|---|---|
| ✓ | [foundry/TIERS.md](/Users/naomiivie/adjutant/foundry/TIERS.md) | This document |
| → | [adjutant/tiers.py](/Users/naomiivie/adjutant/adjutant/tiers.py) | Orchestrator: parallel fan-out, merge, dedupe, rerank |
| → | [scripts/build_tier_indexes.py](/Users/naomiivie/adjutant/scripts/build_tier_indexes.py) | Partition corpus → 3 separate FAISS indexes |
| → | [scripts/run_corpus_server.py](/Users/naomiivie/adjutant/scripts/run_corpus_server.py) | FastAPI mock for WARM/COLD (same code, different `--tier`/`--port`) |

| Modified | Path | What |
|---|---|---|
| → | [adjutant/rag.py](/Users/naomiivie/adjutant/adjutant/rag.py) | Accept index path parameter |
| → | [adjutant/server.py](/Users/naomiivie/adjutant/adjutant/server.py) | `/query` uses `tiers.retrieve_tiered()`; `/health` returns tier status |
| → | [web/index.html](/Users/naomiivie/adjutant/web/index.html) | 3 tier-status LEDs in header |
| → | [web/app.js](/Users/naomiivie/adjutant/web/app.js) | Polls `/health`, renders tier badges on citations |
| → | [web/styles.css](/Users/naomiivie/adjutant/web/styles.css) | LED + badge styling |

| Demo runtime | Command |
|---|---|
| Boot all 3 tiers | `make run-tiers` (parallel: WARM:8001, COLD:8002, main:8000) |
| Kill cold | `Ctrl-C` on the cold terminal |
| Kill warm | `Ctrl-C` on the warm terminal |
| Wifi yank | Pull cable / disable wifi — main process unaffected |

---

## 9. The pitch line this unlocks

> "Most AI assistants are either online-only or air-gapped. Adjutant is **graceful** — three corpus tiers, on-device foundation that always works, network tiers that enrich when reachable. The soldier is never blocked. The cloud is a luxury, not a dependency. That's the only architecture that survives a real DDIL deployment."

For Mohindra: *"Architecturally guaranteed graceful degradation, not best-effort."*
For Wagner: *"DDIL-native — works on FOB, on the helicopter, in the motor pool."*
