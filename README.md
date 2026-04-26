# Adjutant

**Voice-first, fully offline AI assistant for the Army's bureaucratic tail.**

> *Speak it. Sign it. Move out.*

A junior NCO speaks naturally — *"I need ten days of leave starting June 3 for my sister's wedding"* — and gets a regulation-cited answer plus a populated **DA‑31 PDF** in under 15 seconds. The same voice flow handles TDY (DD‑1351‑2 with JTR per‑diem math) and counseling (DA‑4856).

**One sentence, three filled forms.** *"Going to JRTC at Fort Polk for 5 days, need to counsel SPC Garcia tomorrow, want 2 days of leave when I get back"* → DD‑1351‑2 + DA‑4856 + DA‑31, all signed‑ready, in one pass.

**Runs entirely on this laptop.** Whisper + FAISS + Llama + Kokoro, no internet. The wifi cable is a stage prop in our demo — we pull it before the first query and the room sees nothing change.

---

## Team & Submission

| Field | Value |
|---|---|
| **Team Name** | Adjutant |
| **Track** | GenAI.mil |
| **Location** | Boston (Phase 1, April 25–26, 2026) |
| **Members** | Naomi Ivie (solo) |
| **Repo** | https://github.com/thegirwhocodes/adjutant *(public)* |

---

## What we built

Adjutant is a vertical AI app that lives where the rank-and-file actually work: a soldier's NIPR laptop, with no expectation of network connectivity. The Pentagon's own framing on GenAI.mil:

> *"We have a lot of mechanics, we got a lot of people turning wrenches. Not everyone is sitting in a nice, cushy, air-conditioned office typing away at a computer all day."* — DefenseScoop, December 2025

GenAI.mil reaches the desk officers. Adjutant reaches everyone else.

Five stages, all local, no cloud round-trip:

| Stage | What | Implementation |
|---|---|---|
| **STT** | Browser mic → text | `faster-whisper small.en` int8 on CPU (~250 MB), ffmpeg WebM/MP4 transcode |
| **RAG** | Retrieve relevant regs | FAISS `IndexFlatIP` over **271,333 chunks across 933 documents**, MiniLM-L6 384-dim embedder, score-threshold guard for out-of-corpus refusal |
| **LLM** | Generate cited answer | Llama 3.2 3B via Ollama, retrieved-context-only system prompt, `_clean_nulls` post-processor |
| **Form fill** | Populate DA-form PDF | pikepdf for AcroForm fields (DD-1351-2), reportlab text-on-coordinates overlay for XFA-only forms (DA-31), deterministic GSA per-diem math (`_wire_per_diem`) |
| **TTS** | Speak the summary back | Kokoro 82M ONNX (`af_heart` voice, 24 kHz) primary, pyttsx3 + macOS `say` fallback |

---

## The wow move — multi-form from one prompt

`_infer_forms` in [`adjutant/server.py`](adjutant/server.py) returns a **list** of form IDs. A single voice request that mentions leave AND TDY AND counseling produces **three filled PDFs in parallel** — none of GenAI.mil, CamoGPT, Ask Sage, Milnerva, or EdgeRunner can do this.

**Live demo:**

```
Soldier: "I'm going to JRTC at Fort Polk July 14 for 5 days,
          need to counsel SPC Garcia tomorrow,
          and want 2 days of leave when I get back."

Adjutant: → DD-1351-2 (TDY voucher)
            • Per diem: Leesville, LA = $110 lodging × 5 days
            • M&IE = $68 × 5 days
            • Travel-day rule: 75% on departure + return
            • Total: $746
          → DA-4856 (counseling)
            • Soldier: SPC Garcia
            • Type: Performance / Corrective
          → DA-31 (leave, 2 days post-return)
```

Three real signed-ready PDFs. One voice request. ~15 seconds end-to-end.

---

## Why this is novel

| Capability | GenAI.mil | CamoGPT | Ask Sage | Milnerva | SergeantAI | EdgeRunner | **Adjutant** |
|---|---|---|---|---|---|---|---|
| Voice I/O conversational | ❌ | ❌ | ❌ | ❌ | ❌ | listens 1-way | **✅** |
| Offline / air-gapped | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ tactical | **✅ admin** |
| Cites AR/FM by section | generic | generic | generic | partial | one AR | generic | **✅ multi-source** |
| Auto-fills real DA-form PDF | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | **✅** |
| End-to-end persona flow | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | **✅** |
| Multi-form from one voice request | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | **✅** |
| 3-tier graceful-degradation architecture | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | **✅** |
| Hands-free | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | **✅** |

EdgeRunner serves tactical / SOF doctrine. Adjutant serves the bureaucratic tail.

---

## Why this matters

| Stat | Source |
|---|---|
| **Army company leaders work 12.5-hour days — longer than 96% of all American workers.** Less than ⅓ on actual readiness. | RAND Corporation |
| Companies submit *"three to four dozen monthly reports"* — *"one week every month for company command teams"* | Modern War Institute (West Point) |
| GenAI.mil rolled to 1.2M users, but admits *"automating thousands of man-hours"* of paperwork is the gap | DefenseScoop / War.gov, Dec 2025 |
| GenAI.mil is **web-only, desk-only, requires CAC** | Small Wars Journal, Jan 2026 |

**TAM:** 3M service members × 6 hrs/wk on paperwork × $25/hr loaded = **$23.4 billion/year** in recoverable mission-readiness labor.

---

## Reliability — architecturally cannot hallucinate

This is the Mohindra-targeting move (MIT Lincoln Lab AI Test & Evaluation lead is one of our judges):

```
Soldier asks about a regulation NOT in our corpus
    ↓
RAG retrieval returns 0 chunks above score-threshold (0.35)
    ↓
LLM is constrained to retrieved-context-only prompt
    ↓
Adjutant replies: "I don't have AR <X> in my regulation corpus.
                   Check with your S1 or pull it from armypubs.army.mil.
                   I won't guess on regulation language."
```

**Side-by-side eval planned for Phase 2:** 25 questions × Adjutant vs. GenAI.mil's hosted Gemini, measuring hallucination rate on out-of-corpus regulation queries. Adjutant's retrieval-grounded refusal vs. Gemini's confident fabrications.

---

## Tiered retrieval architecture (HOT / WARM / COLD)

Adjutant's retrieval gracefully degrades when any tier goes down — designed for **DDIL** (Denied / Degraded / Intermittent / Limited) deployment:

| Tier | Lives on | Latency | Demo corpus | Production scale |
|---|---|---|---|---|
| **HOT** | The soldier's laptop, in-process | < 50 ms | 34 docs (form-target ARs + most-touched regs) | ~30 docs |
| **WARM** | Simulated on-base NIPR file server (`localhost:8001`) | ~200 ms | 172 docs (broader admin) | ~500 docs |
| **COLD** | Simulated cloud (`localhost:8002`) | ~1.5 s | 727 docs (specialty regs) | ~5,000 docs (with DTIC integration) |

All three speak the same `POST /retrieve` JSON protocol with versioned headers. **Pull the network plug** mid-demo and Adjutant keeps working — HOT alone serves SGT Chen's leave + TDY + counseling questions correctly. The richer tiers enrich when reachable, never block when unreachable.

See [`foundry/TIERS.md`](foundry/TIERS.md) for the full architecture spec.

---

## Datasets / APIs used

All public, all unclassified, all Distribution Statement A. Per the SCSP brief's allowed dataset list:

- **Army Publishing Directorate** (`https://armypubs.army.mil`) — 933 documents bulk-crawled across 20 type-indexes (AR / DA Pam / FM / ADP / Army Directives / ALARACT / MCM / Strategic Documents). Bulk crawler in [`scripts/bulk_crawl_apd.py`](scripts/bulk_crawl_apd.py).
- **Joint Travel Regulations** (`https://api.army.mil/.../jtr-june-2025.pdf`) — June 2025 edition for DD-1351-2 governing
- **GSA Per-Diem Rates API** (`https://open.gsa.gov/api/perdiem-api/`) — daily lodging + M&IE rates by city/state
- **DD-1351-2** (`https://www.esd.whs.mil/.../dd1351-2.pdf`) — DoD travel voucher template
- **DTIC public records** (`https://apps.dtic.mil`) — bulk async crawler in [`scripts/fetch_dtic_async.py`](scripts/fetch_dtic_async.py); 22,000+ DTIC research reports successfully pulled in our test crawl. Embedding pipeline in [`colab/dtic_pipeline.py`](colab/dtic_pipeline.py) (Colab/RunPod-runnable).

No classified material. No CAC-gated systems. No ITAR-controlled tech.

---

## Stack

```
Python 3.13          FastAPI + Uvicorn      WebSocket /ws/voice
faster-whisper       sentence-transformers  Llama 3.2 3B (Ollama)
faiss-cpu            pikepdf + reportlab    Kokoro 82M ONNX TTS
ffmpeg               Silero VAD             pypdf
```

**Footprint:** ~3 GB resident RAM, ~7 GB disk including models. Runs on M1/M2 Air-class hardware with no GPU. Verified end-to-end on 8 GB M1 MacBook Air.

---

## How to run it

### Prerequisites

```bash
# macOS / Linux. Python 3.11+. ~10 GB disk for models + corpus.
brew install ollama        # or: curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.2:3b
```

### Install

```bash
git clone https://github.com/thegirwhocodes/adjutant.git
cd adjutant
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# One-time: download the Tier-1 corpus (Army Pubs + JTR + GSA rates)
python scripts/download_corpus.py

# (Optional) bulk APD crawl — adds ~900 more docs over ~30 min
python scripts/bulk_crawl_apd.py --apd-only

# One-time: partition corpus into HOT/WARM/COLD + build 3 FAISS indexes
python scripts/build_tier_indexes.py
```

### Run the tiered demo

Three terminals:

```bash
# Terminal 1 — WARM tier
python scripts/run_corpus_server.py --tier warm --port 8001

# Terminal 2 — COLD tier (artificial latency for demo authenticity)
python scripts/run_corpus_server.py --tier cold --port 8002 --artificial-delay-ms 800

# Terminal 3 — main Adjutant server (HOT runs in-process)
uvicorn adjutant.server:app --port 8000
```

Open `http://localhost:8000/web/`. Hit the mic. Say:

> *"I'm going to JRTC at Fort Polk July 14 for 5 days, need to counsel SPC Garcia tomorrow, and want 2 days of leave when I get back."*

You'll get back:
1. Spoken summary citing AR 600-8-10 ¶ 4-3 and JTR Chapter 5
2. Three filled PDFs in the iframe: DD-1351-2 + DA-4856 + DA-31
3. Per-diem totals computed deterministically ($746 for the 5-day Fort Polk trip)

Pull your wifi cable. Repeat the flow. Watch every tier LED still report up.

---

## Architecture diagram

```
                     ┌─────────────────────────────────┐
                     │  Browser  (web/, vanilla JS)    │
                     │  · WebSocket /ws/voice          │
                     │  · AudioWorklet → 32 ms PCM     │
                     │  · Tier-status LEDs             │
                     └────────────────┬────────────────┘
                                      │
                     ┌────────────────▼────────────────┐
                     │  FastAPI + Uvicorn  (port 8000) │
                     │  adjutant/server.py             │
                     └──┬───────┬───────┬───────┬──────┘
                        │       │       │       │
            ┌───────────┘       │       │       └─────────────┐
            ▼                   ▼       ▼                     ▼
    ┌──────────────┐   ┌─────────────┐  ┌──────────────┐  ┌──────────────┐
    │  STT         │   │  TIERED     │  │  LLM         │  │  PDF FILL    │
    │  faster-     │   │  RAG        │  │  Llama 3.2 3B│  │  pikepdf +   │
    │  whisper     │   │  (HOT in-   │  │  via Ollama  │  │  reportlab   │
    │  small.en    │   │  process)   │  │  (port 11434)│  │  +per_diem.py│
    └──────────────┘   └─────┬───────┘  └──────────────┘  └──────────────┘
                             │
                  ┌──────────┴───────────┐
                  ▼                      ▼
    ┌────────────────────┐   ┌────────────────────┐
    │  WARM corpus svc   │   │  COLD corpus svc   │
    │  port 8001         │   │  port 8002         │
    │  172 docs / 95K    │   │  727 docs / 150K   │
    │  chunks            │   │  chunks            │
    └────────────────────┘   └────────────────────┘
                  ↓                      ↓
              [In production: NIPR LAN]  [In production: FedRAMP cloud]
```

No external APIs at runtime. No telemetry. No CAC required. **One sealed binary per soldier.**

---

## Repo layout

```
adjutant/
├── adjutant/
│   ├── server.py             # FastAPI; /query, /voice, /transcribe, /forms, /health
│   ├── voice_loop.py         # Continuous WS voice loop with Silero VAD + barge-in
│   ├── stt.py                # faster-whisper wrapper + ffmpeg transcode
│   ├── llm.py                # Ollama client + retrieval-grounded prompts
│   ├── tts.py                # Kokoro primary + pyttsx3/say fallback
│   ├── rag.py                # FAISS retrieval; cites section/paragraph
│   ├── tiers.py              # HOT/WARM/COLD orchestrator + cross-encoder rerank
│   ├── forms.py              # DA-31, DD-1351-2, DA-4856 schemas
│   ├── pdf_fill.py           # pikepdf + reportlab field-population
│   ├── per_diem.py           # GSA rate lookup + 75% travel-day math
│   └── prompts.py            # System prompts; cite-or-refuse contracts
├── corpus/{hot,warm,cold}/   # Tiered Army Pubs corpus (933 PDFs)
├── forms/                    # Blank DA-31, DD-1351-2, DA-4856
├── scripts/
│   ├── download_corpus.py    # Curated Tier-1 download
│   ├── bulk_crawl_apd.py     # Playwright bulk crawler for APD
│   ├── ingest_corpus.py      # Single-tier FAISS build
│   ├── build_tier_indexes.py # Three-tier partition + FAISS build
│   ├── run_corpus_server.py  # WARM/COLD tier microservice
│   ├── fetch_dtic_async.py   # DTIC public sitemap walker (~30K docs)
│   └── orchestrate_crawls.py # Run APD + DTIC + ingest in parallel
├── colab/
│   ├── dtic_pipeline.py      # End-to-end Colab/RunPod notebook
│   └── RUNPOD_INSTRUCTIONS.md
├── foundry/
│   ├── TIERS.md              # Tiered architecture spec
│   ├── SCRAPING.md           # APD/DTIC/eCFR ingestion plan
│   ├── BUILD_PLAN.md         # 30-hour hackathon schedule
│   ├── CODE_PLAN.md          # File-by-file engineering brief
│   └── PDF_FORM_FILL_DOSSIER.md
├── docs/
│   ├── DEMO_SCRIPT.md        # 5-min judge walkthrough
│   └── PERSONA.md            # SGT Maya Chen
├── web/                      # Vanilla HTML/JS frontend
│   ├── index.html            # Hero + nav + voice orb
│   ├── app.js                # WebSocket voice loop, tier polling, multi-form render
│   ├── audio_worklet.js      # 48k → 16k downsample, 32 ms Int16 PCM
│   ├── styles.css            # Dark mode, OD-green accent
│   └── voice-orb.js          # Pulsing voice-activity indicator
└── README.md (you are here)
```

---

## Limitations + scope (honest)

What we deliberately scoped OUT for the 30-hour build:

- **Not classified.** Public corpus only. SIPR / JWICS deployment is a follow-on.
- **No write-back to IPPS-A or DTS.** We generate the form. The S1 / approving authority still has authority. Adjutant cleans up the inputs; humans keep the chain.
- **Three forms, not thirty.** DA-31 + DD-1351-2 + DA-4856 cover the 80% case for our junior-NCO persona. Adding more is a schema-extraction step, not a research project.
- **English only.**
- **One persona end-to-end.** A second persona (company XO doing property under AR 735-5) is a sub-30-min pivot, but we chose depth over breadth.

What we acknowledge as risks (and how we mitigate):

- **Hallucination on regulation citations** → retrieved-context-only constraint + score-threshold floor + on-screen verbatim source quotes. Tested with deliberate out-of-corpus questions; the system correctly refuses.
- **PDF field-name drift** → re-extract schemas at install time so updates to blank DA forms are caught.
- **Background noise in motor-pool / FOB conditions** → Whisper handles 60+ dB noise floor.

---

## Business model + path to scale

Three paths post-hackathon:

1. **GovCon** — license to a SI primary (Booz / Leidos / Accenture Federal) as a vertical AI app for their existing IL5 deployments.
2. **Direct** — SBIR Phase I → II → III with Army CDAO as customer.
3. **Open-source** — sell hosted/managed on top of the public repo.

The DSN voice surface (port the same Whisper-RAG-Llama pipeline onto FedRAMP-compliant gov telephony — soldier dials a landline, talks to Adjutant, form lands in their .mil inbox) is the **moat for path 2**. Reaches every soldier regardless of CAC, regardless of OPSEC posture.

---

## License

MIT. The code is yours. The forms and ARs are public-domain US Government works.

---

**Built in 30 hours at the SCSP Hackathon Boston, April 25–26, 2026.**
**Team Adjutant — Naomi Ivie.**
