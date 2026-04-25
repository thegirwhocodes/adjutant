# Adjutant

**Voice-first, fully offline AI assistant for Army paperwork.**

> *"Speak it. Sign it. Move out."*

A junior NCO talks to Adjutant the way they'd talk to their S1 — *"I need to file leave for ten days starting June 3"* — and gets back a regulation-cited answer plus a populated DA-31 PDF. Same flow for TDY: *"I need to attend the JRTC mission rehearsal at Fort Polk for 5 days"* generates a DD-1351-2 with JTR per-diem math already done.

Runs entirely on-device. No internet required. Cites Army Regulations and Field Manuals by section + paragraph.

---

## Team & Submission

- **Team Name:** Charlie Mike
- **Track:** GenAI.mil
- **Location:** Boston (Phase 1, April 25–26, 2026)
- **Members:** Naomi Ivie *(team in formation; final roster confirmed by 2pm Saturday April 25)*

---

## What we built

A vertical AI app for the Army's "bureaucratic tail." Five pieces wired together, all running locally:

1. **Voice in** — browser mic → Whisper STT (local model)
2. **Local RAG** — FAISS vector store over a curated corpus: AR 600-8-10 (Leaves & Passes), Joint Travel Regulations (June 2025), AR 623-3 (Evaluation Reporting System), DA Pam 600-25 (NCO Guide), GSA per-diem rates
3. **Local LLM** — Llama 3.1 8B via Ollama, constrained to retrieved-context-only responses
4. **Form schema reasoning** — pdfplumber extracts field schemas from blank DA-31, DD-1351-2, DA-4856; LLM populates structured JSON
5. **PDF auto-fill + voice out** — pypdf writes filled PDFs; Chatterbox TTS reads back the summary

The entire stack runs on a laptop with no network access. The demo proves it by pulling the wifi cable mid-flow.

## Why this is novel

| Capability | GenAI.mil | CamoGPT | Ask Sage | Milnerva | EdgeRunner | **Adjutant** |
|---|---|---|---|---|---|---|
| Voice I/O | ❌ | ❌ | ❌ | ❌ | listens 1-way | **✅ conversational** |
| Offline / air-gapped | ❌ | ❌ | ❌ | ❌ | ✅ tactical | **✅ admin** |
| Cites AR/FM by section | generic | generic | generic | partial | generic | **✅ multi-source** |
| Auto-fills real DA-form PDF | ❌ | ❌ | ❌ | ❌ | ❌ | **✅** |
| End-to-end persona flow | ❌ | ❌ | ❌ | ❌ | ❌ | **✅** |

EdgeRunner serves tactical / SOF doctrine. GenAI.mil serves desk-bound officers. **Adjutant serves the rank-and-file with paperwork problems**, which the Pentagon's own framing calls out as the most-immediate friction (`"Not everyone is sitting in a nice, cushy, air-conditioned office."` — DefenseScoop, Dec 2025).

## Why this matters

- **RAND Corporation:** Army company leaders work 12.5-hour days — longer than 96% of all American workers. **Less than one-third** of that time is on actual unit readiness.
- **Modern War Institute (West Point):** Companies submit *"three to four dozen monthly reports."* Completion consumes *"one week every month for company command teams."*
- **Pentagon's own framing on GenAI.mil:** Use cases include *"automating routine staff processes that currently consume thousands of man-hours."*

3M service members × 6 hrs/wk on paperwork × $25/hr loaded = **$23.4B/yr in recoverable labor**.

## Datasets / APIs used

All public, all unclassified. Per the SCSP brief's allowed dataset list:

- **Army Publishing Directorate** — AR 600-8-10, AR 623-3, DA Pam 600-25, FM 6-22, blank DA-31 / DA-4856 PDFs (`https://armypubs.army.mil`)
- **Joint Travel Regulations** — June 2025 PDF (`https://api.army.mil/e2/c/downloads/2025/06/10/0da05172/jtr-june-2025.pdf`)
- **GSA Per-Diem Rates API** — daily lodging + M&IE rates by city/state (`https://open.gsa.gov/api/perdiem-api/`)
- **DD-1351-2** — DoD travel voucher (`https://www.esd.whs.mil/Portals/54/Documents/DD/forms/dd/dd1351-2.pdf`)

No classified material. No CAC-gated systems. No ITAR-controlled tech.

## How to run it

### Prerequisites

```bash
# macOS / Linux. Python 3.11+. ~10GB disk for models.
brew install ollama  # or: curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.1:8b
```

### Install

```bash
git clone https://github.com/<your-username>/adjutant.git
cd adjutant
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# One-time: download the corpus (Army Pubs + JTR + GSA rates)
python scripts/download_corpus.py

# One-time: build the FAISS vector index from the corpus
python scripts/ingest_corpus.py

# One-time: extract form-field schemas from blank PDFs
python scripts/extract_form_schemas.py
```

### Run the server

```bash
# Terminal 1: make sure Ollama is up
ollama serve

# Terminal 2: start Adjutant
uvicorn adjutant.server:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000` in a browser. Hit the mic button. Say:

> *"I need to file leave for ten days starting June 3 to visit family in Atlanta."*

You'll get back:
1. A spoken summary citing AR 600-8-10 paragraph 4-3
2. A pre-filled DA-31 PDF you can download

Pull your wifi cable. Repeat the flow. Watch it still work.

### Demo script

See [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md) for the 5-minute walkthrough we'll run for judges.

## Architecture

```
   ┌─────────────┐
   │  Browser    │  microphone + form-preview pane
   │  (web/)     │
   └──────┬──────┘
          │ WebSocket / HTTP
   ┌──────▼──────┐
   │  FastAPI    │  adjutant/server.py
   │  (offline)  │
   └──┬─┬─┬─┬─┬──┘
      │ │ │ │ │
      │ │ │ │ └──> pypdf  (forms/*.pdf field-fill)
      │ │ │ └────> per_diem.py (GSA rates JSON, local cache)
      │ │ └──────> Chatterbox TTS (port 8001, optional; system TTS fallback)
      │ └────────> Ollama Llama 3.1 8B (port 11434)
      └──────────> faster-whisper (local, large-v3)
                  ↑
                  └── FAISS index over corpus/ARs + JTR + DA Pams
```

No external APIs. No telemetry. No CAC. Runs on a 2023 MacBook Pro M2 with 16GB RAM.

## Repo structure

```
adjutant/
├── adjutant/                   # Python package
│   ├── server.py               # FastAPI entry; routes voice + form endpoints
│   ├── stt.py                  # faster-whisper wrapper
│   ├── llm.py                  # Ollama client + retrieval-grounded prompts
│   ├── tts.py                  # Chatterbox + system fallback
│   ├── rag.py                  # FAISS retrieval; cites section/paragraph
│   ├── forms.py                # DA-31 / DD-1351-2 / DA-4856 schemas
│   ├── pdf_fill.py             # pypdf field-population
│   ├── per_diem.py             # GSA rate lookup (cached locally)
│   └── prompts.py              # System prompts; "must cite source" guardrails
├── corpus/                     # Source PDFs (downloaded by scripts/)
├── forms/                      # Blank DA-31, DD-1351-2, DA-4856 PDFs
├── scripts/
│   ├── download_corpus.py      # Pulls public PDFs from Army Pubs + DTMO
│   ├── ingest_corpus.py        # Chunks + embeds + builds FAISS index
│   └── extract_form_schemas.py # Maps PDF field names to JSON schema
├── web/                        # Vanilla HTML/JS frontend
│   ├── index.html
│   ├── app.js
│   └── styles.css
├── tests/
│   ├── test_rag.py             # Retrieval accuracy + citation verification
│   ├── test_forms.py           # Field-fill round-trip
│   └── test_offline.py         # Asserts no network calls during inference
├── docs/
│   ├── DEMO_SCRIPT.md          # Judge-facing 5-min walkthrough
│   └── PERSONA.md              # Junior NCO TDY scenario
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

## Limitations + scope

What we deliberately scoped OUT for the 30-hour build:

- **Not classified.** Public corpus only. SIPR/JWICS deployment is a follow-on, not this hackathon.
- **Not write-back to IPPS-A or DTS.** We generate the form. The S1 / approving authority still has authority. Adjutant removes friction, not humans.
- **Three forms, not thirty.** DA-31, DD-1351-2, DA-4856 cover the daily 80% for our junior-NCO persona.
- **English only.** Not addressing translation use cases.
- **One persona end-to-end.** A second persona (company XO doing property) is a sub-30-min pivot but we want depth over breadth.

What we acknowledge as risks:

- **Hallucination on regulation citations.** Mitigated by retrieval-context-only constraint + on-screen source quotes. Tested with deliberate out-of-corpus questions that the system correctly refuses.
- **Background noise in motor-pool / FOB conditions.** Whisper handles 60+ dB; tested with simulated noise.
- **PDF field-name drift.** We re-extract schemas at install time so updates to blank DA forms are caught.

## License

MIT. The code is yours. The forms and ARs are public-domain US Government works.

---

Built in 30 hours at the SCSP Hackathon Boston, April 25–26, 2026.
