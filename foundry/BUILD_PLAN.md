# Adjutant — comprehensive 30-hour build plan

**Founder-facing plan.** Read top-to-bottom Saturday morning, work the phases, ship Sunday 5pm.
**Companion:** see `CODE_PLAN.md` for the engineering detail Claude follows when writing code.

---

## Time anchors

| Anchor | Time |
|---|---|
| Now | Saturday April 25, ~12:00 noon Boston |
| Team registration deadline | **Saturday 2:00 PM** to `hack@scsp.ai` |
| Submission deadline | **Sunday 5:00 PM** — GitHub link + README to `hack@scsp.ai` |
| Demo window | **Sunday 5:00–7:00 PM** in front of judges |

---

## Phase 0 — Right now, before anything else (45 min)

### 0.1 Confirm the team (15 min)
- Decide solo or recruit onsite. Ideal: find one veteran or active-duty hacker. Walk up: *"I'm building voice-first offline AI for Army paperwork — I need someone who's filed a DA-31."* You'll get a yes within 20 minutes.
- Backup: pair with one Python/web dev — even a stranger willing to handle the frontend buys 6 hours.
- Solo plan is fine. The repo is structured to be solo-buildable.

### 0.2 Send the registration email (5 min) — HARD DEADLINE 2:00 PM
- Open `docs/SCSP_REGISTRATION_EMAIL.md`, fill in member names, send to `hack@scsp.ai` with subject `SCSP Hackathon Charlie Mike FINAL`.

### 0.3 Get the laptop ready (15 min)
- Mac on power. External keyboard if you have one — you'll be typing for 24+ hours.
- `brew install ollama`
- Start the model pull NOW so it runs while you do other setup: `ollama pull llama3.1:8b`
- Confirm `python3 --version` is 3.11+

### 0.4 Initialize the GitHub repo (10 min)
```bash
cd /Users/naomiivie/adjutant
git init && git add . && git commit -m "Initial scaffolding"
gh repo create adjutant --public --source=. --remote=origin --push
```
You need the public URL by 2:00 PM for the registration email.

---

## Phase 1 — Core stack running (Sat 1:00–4:00 PM, 3h)

**Goal:** by 4 PM, you can talk to the laptop and get a regulation-grounded text answer with citations. No PDF yet, no TTS yet.

1. **Install deps** (15 min) — `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && cp .env.example .env`
2. **Download corpus** (15 min) — `python scripts/download_corpus.py`. If a DoD URL 403s, download manually in browser, drop into `corpus/` or `forms/`.
3. **Build FAISS index** (15 min) — `python scripts/ingest_corpus.py`. Confirm `.faiss_index/faiss.bin` and `.faiss_index/chunks.pkl` exist. ~500–2,000 chunks expected.
4. **Smoke test retrieval** (15 min) — Python REPL: `from adjutant.rag import retrieve; retrieve("How does ordinary leave accrue?", top_k=3)`. If retrieval is poor, increase `CHUNK_SIZE` to 1200 in `scripts/ingest_corpus.py`, re-ingest.
5. **Smoke test the LLM** (15 min) — `ollama serve` in one terminal; in another, `from adjutant.llm import answer_query; from adjutant.rag import retrieve; chunks = retrieve("..."); answer_query("...", chunks)`. Should cite AR 600-8-10. If too slow, drop to `llama3.1:8b-instruct-q4_K_M` or `llama3.2:3b`.
6. **Run the server** (15 min) — `uvicorn adjutant.server:app --host 0.0.0.0 --port 8000 --reload`. Open `http://localhost:8000/web/`, hit `/health` and `/forms`.
7. **First end-to-end voice test** (1.5h) — most likely failures: mic permissions, WebM format quirks with faster-whisper, empty citations. Resolve each.

**Acceptance:** hold mic → say *"How does leave accrue?"* → see transcript + grounded answer + citations on screen.

---

## Phase 2 — Form-fill flow (Sat 4:00–8:00 PM, 4h)

**Goal:** end-to-end DA-31 demo flow with a real filled PDF in the browser.

1. **Extract PDF schemas** (45 min) — `python scripts/extract_form_schemas.py`. The actual AcroForm field names will NOT match the placeholder names in `adjutant/forms.py`. Open `forms/extracted_schemas.json`, update `adjutant/forms.py` so dict keys match real PDF field names.
2. **Test PDF fill round-trip** (30 min) — Python REPL: `fill_pdf(DA_31["pdf_path"], test_data, "test_filled.pdf")`. Open in Preview. If fields blank, schema is wrong. If they disappear when clicked, `NeedAppearances` is set but viewer ignores it (prints fine).
3. **Wire LLM extraction into the pipeline** (1h) — already exists in `server.py` `/query`. Test via curl. Should get back JSON with `form_data`, `pdf_url`, `spoken_summary`.
4. **Tighten LLM extraction** (1h) — common failures:
   - Returns prose not JSON → use Ollama `format="json"`; if unsupported, end prompt with "Return ONLY a JSON object."
   - Invents fields not in schema → narrow prompt with explicit field list, "Use ONLY these field names."
   - Hallucinates personal info → strengthen "return null for fields you cannot infer"; expose `missing_fields`.
   - Test with 5 different leave scenarios, all 5 must produce a fillable PDF.
5. **Add TTS** (45 min) — start with pyttsx3 (bulletproof, no models). If time, spin up Chatterbox locally for better voice. pyttsx3 robot voice is acceptable — it's on-brand for "built in 30 hours."

**Acceptance:** voice → transcribed → RAG → grounded answer with citations → filled DA-31 PDF in iframe → spoken reply.

**Take a 30-min break. Eat dinner.**

---

## Phase 3 — DD-1351-2 (TDY) and DA-4856 (counseling) flows (Sat 8:30–11:00 PM, 2.5h)

1. **DD-1351-2 with per-diem math** (1.5h) — LLM extracts city/state/days; `adjutant/per_diem.py` does deterministic math. Wire into `server.py` `/query` so when `target_form == "DD-1351-2"` it calls `calculate_tdy_total(...)` after extraction. Test with: *"5 days at Fort Polk starting July 14, home station Fort Bragg"* → looks up Leesville LA per-diem ($110/$68), 5 days × correct math, ~$822 total.
2. **DA-4856 counseling form** (1h) — less math, more language. Test: *"counseling for Specialist Garcia for being late to formation twice."* LLM populates `purpose`, `key_points`, `plan_of_action` in Army-bullet style. Verify long strings don't clip in the PDF.

---

## Phase 4 — High-impact polish (Sat 11:00 PM – Sun 2:00 AM, 3h)

These are the moves that win Novelty + Tech Difficulty + Mohindra's eval brain.

### 4.1 Multi-form from one prompt (1h) — the "wow" move
Modify `_infer_form` in `server.py` to return a **list** of form IDs. Iterate, generate each.
Test: *"I'm going to JRTC July 14 for 5 days, need to counsel SPC Garcia tomorrow, and want 2 days of leave when I get back."* → 3 forms generated.

### 4.2 Context-aware refusal (20 min)
Add compliance rules to `prompts.py`:
- Leave > 30 days → cite AR 600-8-10 4-3, draft for 30 + flag battalion commander
- Major training event conflict → flag deconfliction
- TDY without purpose → ask for purpose
Test: *"I want 35 days of leave"* → cited refusal. **The moment Mohindra notices.**

### 4.3 Eval harness vs GenAI.mil (1.5h) — the move that wins Mohindra
- `tests/eval_questions.json` — 20 hand-written questions about AR 600-8-10 with ground-truth answers + must_cite tags. Include 2–3 deliberate out-of-corpus questions where ground truth is `OUT_OF_CORPUS`.
- `tests/run_eval.py` — runs all 20 through Adjutant, scores citation accuracy + refusal rate.
- Manually run the same 20 through GenAI.mil's Gemini web UI. Screenshot hallucinations.
- Side-by-side comparison ready for the demo Q&A.

---

## Phase 5 — Sleep (Sun 2:00–6:00 AM, 4h minimum)

**Non-negotiable.** A demo done by an exhausted founder fails. Set an alarm.
If forced to choose: skip the eval harness and sleep 6 hours. Sleep beats polish.

---

## Phase 6 — Rehearsal and final polish (Sun 6:00 AM – 1:00 PM, 7h)

1. **First full solo rehearsal** (6:30–7:30, 1h) — sit at laptop, run `docs/DEMO_SCRIPT.md` exactly. Time it. Aim for **4:30, not 5:00** — buffer for nerves.
2. **Fix slow parts** (7:30–9:00, 1.5h) — most likely: first-call slowness from lazy model loading. Add `@app.on_event("startup")` warmup that runs a dummy STT + LLM call so every demo response feels instant. ~10s startup, but every subsequent call is fast.
3. **Wifi-disconnect rehearsal** (9:00–9:30, 30m) — practice cable yank 5x. Confirm online/offline badge flips, next request still works, you don't yank the power cord.
4. **README final polish + push** (9:30–10:30, 1h) — judge-readable in 60s, `git status` clean, no secrets committed, repo public, optional demo GIF in README hero. `git tag v0.1-scsp && git push --tags`.
5. **Friendly stranger demo** (10:30–12:00, 1.5h) — find someone who doesn't know what you built, run the 5-min demo, ask what was confusing. Fix what they flagged.
6. **Buffer + lunch** (12:00–1:00, 1h) — sit, breathe, eat. **No new features after 12:30 PM.**

---

## Phase 7 — Submission (Sun 1:00–7:00 PM)

1. **Final commit + push** (4:00 PM)
2. **Submission email** (5:00 PM HARD DEADLINE)
   - Subject: `SCSP Hackathon Charlie Mike GenAI.mil`
   - Body: GitHub link + inline README
   - To: `hack@scsp.ai`
3. **Demo to judges** (5:00–7:00 PM) — run the script, pull the wifi cable, smile, breathe between beats.

---

## Cut-list when behind schedule

| Behind by | Cut |
|---|---|
| 1h at end of Phase 1 | DA-4856 — go DA-31 + DD-1351-2 only |
| 2h at end of Phase 2 | Eval harness side-by-side — keep refusal demo, skip GenAI.mil compare |
| 4h at end of Phase 3 | Multi-form-from-one-prompt — single-form pipeline only |
| 6h+ at end of Phase 4 | Per-diem math (use static dummy values) |
| Catastrophic | Drop voice — keep typed input. STT is the most fragile piece; the rest is the innovation |

---

## The floor — what MUST work for the demo

1. Voice input → transcript visible
2. RAG returns ≥1 cited chunk from AR 600-8-10
3. LLM answer references source by name (*"Per AR 600-8-10, ..."*)
4. ONE filled PDF appears on screen
5. Wifi-cable yank moment works (offline indicator flips)
6. Out-of-corpus question demo (refusal moment)

If those 6 things work, you are ahead of every team building generic chat over field manuals.

---

## TL;DR of the plan

| Block | Sat | Sun |
|---|---|---|
| Setup + registration | 12:00–1:00 | — |
| Core pipeline | 1:00–4:00 | — |
| Form-fill | 4:00–8:00 | — |
| TDY + counseling | 8:30–11:00 | — |
| Polish (multi-form, refusal, eval) | 11:00–2:00 | — |
| Sleep | 2:00–6:00 | — |
| Rehearsal + fixes | — | 6:00–12:00 |
| Submission + demo | — | 1:00–7:00 |
