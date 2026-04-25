# Adjutant — Code Plan (for the implementer)

**Audience:** Claude (or any pair-coder) working with Naomi over the 30-hour SCSP build.
**Purpose:** unambiguous engineering instructions — file-by-file, in order, with acceptance gates and remediation paths.
**Companion:** see `BUILD_PLAN.md` for the founder-facing schedule.

---

## Operating principles

1. **Working > pretty.** Every commit must keep the demo path runnable. No "will fix later" half-states on `main`.
2. **One change per commit.** Easier to revert when something breaks at 2 AM.
3. **The demo path is the contract.** Voice → transcript → RAG → cited answer → filled PDF → spoken reply. Every change is judged by whether it preserves or improves this path.
4. **Trust the scaffolding.** The skeleton already does the right thing. Most of this plan is *enabling* what's there, not rewriting.
5. **Naomi confirms — don't autonomously refactor.** Ask before touching anything outside the listed paths.
6. **No network calls at inference time.** If a new dependency wants to phone home, find an alternative. `tests/test_offline.py` is the contract.
7. **Cite or refuse.** Every LLM answer is constrained to retrieved context. Hallucination kills us with Mohindra.

---

## File map (current state of `/Users/naomiivie/adjutant/`)

```
.
├── README.md                       # Judge-facing
├── requirements.txt
├── .env.example
├── .gitignore
├── adjutant/
│   ├── __init__.py
│   ├── server.py                   # FastAPI: /health /forms /transcribe /query /voice
│   ├── stt.py                      # faster-whisper, lazy-loaded
│   ├── llm.py                      # Ollama client + answer_query + extract_form_data
│   ├── rag.py                      # FAISS retrieval, lazy-loaded
│   ├── tts.py                      # Chatterbox primary, pyttsx3 fallback
│   ├── forms.py                    # DA-31, DD-1351-2, DA-4856 schemas (PLACEHOLDER FIELD NAMES)
│   ├── pdf_fill.py                 # pypdf AcroForm fill
│   ├── per_diem.py                 # GSA cache + 75% travel-day math
│   └── prompts.py                  # SYSTEM_PROMPT, REFUSAL_OUT_OF_CORPUS, FORM_EXTRACTION_PROMPT
├── corpus/                         # downloaded by scripts/, not committed
├── forms/                          # blank PDFs, not committed
├── scripts/
│   ├── download_corpus.py
│   ├── ingest_corpus.py
│   └── extract_form_schemas.py
├── web/
│   ├── index.html
│   ├── styles.css
│   └── app.js
├── tests/
│   ├── __init__.py
│   ├── test_forms.py
│   └── test_offline.py
├── docs/
│   ├── DEMO_SCRIPT.md
│   ├── PERSONA.md
│   ├── SCSP_REGISTRATION_EMAIL.md
│   └── 30_HOUR_PLAN.md
└── foundry/
    ├── BUILD_PLAN.md
    └── CODE_PLAN.md  ← you are here
```

---

## Build order (each step has an acceptance gate — do not advance until green)

### STEP 1 — Bootstrap (30 min)

**Commands:**
```bash
cd /Users/naomiivie/adjutant
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
ollama pull llama3.1:8b   # ~5GB, do in a parallel terminal
git init && git add . && git commit -m "Initial scaffolding"
gh repo create adjutant --public --source=. --remote=origin --push
```

**Acceptance gate:**
- `pytest tests/test_forms.py` passes (4 tests).
- `python -c "from adjutant.forms import REGISTRY; print(REGISTRY)"` prints 3 forms.
- `ollama list` shows `llama3.1:8b`.
- Repo URL is reachable in the browser.

**Common failures:**
- `faiss-cpu` build fails on Apple Silicon → `pip install faiss-cpu==1.9.0 --no-cache-dir`. If still broken, swap to `pip install faiss-cpu==1.7.4` (older but Apple-Silicon-friendly).
- `faster-whisper` complains about `ctranslate2` → `pip install --upgrade ctranslate2`.
- `pyttsx3` breaks on macOS Sonoma+ → keep it (we'll fall through to Chatterbox or just use the system `say` command via subprocess as a last resort).

---

### STEP 2 — Corpus download + manual fallback (45 min)

**Run:**
```bash
python scripts/download_corpus.py
```

**What we expect to land in `corpus/`:**
- `AR_600-8-10_Leaves_and_Passes.pdf`
- `JTR_2025-06.pdf`
- `per_diem.json` (seed cache)

**What we expect to land in `forms/`:**
- `da_31_blank.pdf`
- `dd_1351_2_blank.pdf`
- `da_4856_blank.pdf`

**Failure modes + remediation:**

| Symptom | Fix |
|---|---|
| `403` from `armypubs.army.mil` | Visit URL in Chrome with normal headers, save PDF, drop into `corpus/` or `forms/` |
| `404` because URL changed | Find current URL via `https://armypubs.army.mil/ProductMaps/PubForm/...`, drop in manually |
| `da_4856_blank.pdf` is a flat scan (no AcroForm) | Sub-in `https://armypubs.army.mil/pub/eforms/DR_a/ARN37571_DA_FORM_4856_FINAL.pdf` (search "DA Form 4856 fillable"). If still flat, drop DA-4856 from scope (cut-list approved). |
| `JTR.pdf` is 1,600 pages → ingest is slow | Acceptable. The demo only needs Chapter 2 (per-diem). If ingest >10 min, pre-extract pages 50–250 with `pdftk in.pdf cat 50-250 output JTR_chap2.pdf`. |

**Acceptance gate:** all 5 files present, each `>50KB`. `ls -la corpus/ forms/` — eyeball it.

---

### STEP 3 — Ingest corpus into FAISS (15 min)

**Run:**
```bash
python scripts/ingest_corpus.py
```

Expect to see `~500–2,000 chunks indexed`. Output `.faiss_index/faiss.bin` + `.faiss_index/chunks.pkl`.

**Smoke test in REPL:**
```python
from adjutant.rag import retrieve
chunks = retrieve("How does ordinary leave accrue?", top_k=3)
for c in chunks:
    print(c["source"], "—", c["section"], "—", c["text"][:120])
```

**Expected output:** at least 1 chunk with `source == "AR 600-8-10"`, ideally with a `section` like `Paragraph 4-3`.

**If retrieval is poor:**
1. Increase `CHUNK_SIZE` in `scripts/ingest_corpus.py` from 800 → 1200. Re-ingest.
2. Switch embedder to `sentence-transformers/all-mpnet-base-v2` (better quality, ~3× slower). Update `EMBED_MODEL` in both `scripts/ingest_corpus.py` and `.env` `EMBEDDING_MODEL`.
3. Top-K too low → bump `TOP_K` in `.env` from 5 → 8.

**Acceptance gate:** REPL test returns ≥3 chunks, at least 1 cites AR 600-8-10 with a section label.

---

### STEP 4 — End-to-end text query (no voice yet) (45 min)

**Start the server:**
```bash
ollama serve  # Terminal 1
uvicorn adjutant.server:app --host 0.0.0.0 --port 8000 --reload  # Terminal 2
```

**Hit the endpoint:**
```bash
curl -s http://localhost:8000/health
curl -s http://localhost:8000/forms | python -m json.tool
curl -s -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"query": "How does ordinary leave accrue per AR 600-8-10?"}' \
  | python -m json.tool
```

**Expected response shape:**
```json
{
  "spoken_summary": "Per AR 600-8-10 paragraph 4-3, ...",
  "citations": [
    {"source": "AR 600-8-10", "section": "Paragraph 4-3", "quote": "..."},
    ...
  ],
  "form_data": null,
  "missing_fields": [],
  "pdf_url": null,
  "audio_url": "/audio/reply-xxxx.wav"
}
```

**Failure modes:**

| Symptom | Fix |
|---|---|
| `spoken_summary` is empty | LLM is timing out — check `ollama serve` is running; `ollama list` shows the model; try smaller model (`llama3.2:3b`) |
| `spoken_summary` cites no source | Prompt isn't being followed — verify `prompts.SYSTEM_PROMPT` is the one being sent; lower `temperature` to 0.1 |
| LLM returns prose with no `Per AR ...` | Model is too small or not instruct-tuned — switch to `llama3.1:8b-instruct-q4_K_M` |
| `audio_url` is null | TTS failed — check `tts.py` log; OK to skip during this step |

**Acceptance gate:** curl returns valid JSON with at least 1 citation, spoken_summary references AR 600-8-10 by name.

---

### STEP 5 — Browser voice flow (1.5h)

**Open** `http://localhost:8000/web/` in Chrome.

**Test:**
1. Click mic button (it's a hold-to-talk button — `mousedown`/`mouseup`).
2. Say: *"How does ordinary leave accrue?"*
3. Release mic. Watch transcript appear, then citation panel, then audio play.

**Failure modes:**

| Symptom | Fix |
|---|---|
| "Mic error: Permission denied" | Browser permissions; reload, click "Allow" |
| Transcript empty | First-call Whisper download takes ~3GB; wait 2 min, retry. Verify with `ls ~/.cache/huggingface/hub/` |
| Transcript wrong (e.g. "How is order" for "ordinary") | Whisper without enough context — already fixed via military-jargon `initial_prompt` in `stt.py`; if still bad, use `large-v3` (already default) instead of base |
| Audio doesn't play | Browser autoplay policy — first click on the page enables audio. Already handled in `app.js` via user gesture |
| WebM blob fails on Whisper | macOS Safari sends `audio/mp4` not `audio/webm`. In `stt.py`, change `tempfile.NamedTemporaryFile(suffix=".webm")` to `suffix=".bin"` (faster-whisper sniffs the format) |

**Acceptance gate:** voice in → transcript displayed → citations rendered → audio plays back. End-to-end <12 seconds.

---

### STEP 6 — PDF schema extraction + form-fill round-trip (1h) ⚠️ HIGH-RISK STEP

**Run:**
```bash
python scripts/extract_form_schemas.py
```

This prints actual AcroForm field names. **They will not match the placeholder names in `adjutant/forms.py`** — Army Pubs PDFs use names like `topmostSubform[0].Page1[0].LeaveDates[0]`.

**The fix is manual schema mapping:**

1. Open `forms/extracted_schemas.json`.
2. Open `adjutant/forms.py`.
3. For each form (DA-31, DD-1351-2, DA-4856), update the keys in `fields` dict to match real PDF field names. Keep the *values* (description, type, required) the same.
4. Add an explicit mapping from semantic-name → PDF-field-name in the same file:

```python
# Add this near the top of adjutant/forms.py
DA_31_FIELD_MAP = {
    "name":              "topmostSubform[0].Page1[0].FormalName[0]",        # paste actual name from extracted JSON
    "ssn":               "topmostSubform[0].Page1[0].SSN[0]",
    "rank":              "topmostSubform[0].Page1[0].Rank[0]",
    # ... etc
}
```

5. Modify `pdf_fill.fill_pdf` to translate semantic keys → PDF keys using the map before writing. **Or** simpler: keep `forms.py.fields` keyed by *PDF field names directly*, and let the LLM extraction return data keyed by PDF field names (since the schema sent to the LLM uses those names).

**Pick option 2 (simpler):**
- Update `forms.py` so `fields` is keyed by actual PDF field name.
- The `desc` value tells the LLM what the field means.
- LLM returns JSON with PDF-field-name keys.
- `pdf_fill` writes them directly. No translation layer.

**Smoke test:**
```python
from adjutant.pdf_fill import fill_pdf
from adjutant.forms import DA_31

test_data = {
    # use the ACTUAL field names from the extracted schema
    "topmostSubform[0].Page1[0].FormalName[0]": "CHEN, MAYA L",
    "topmostSubform[0].Page1[0].SSN[0]": "1234",
    # ... etc
}
fill_pdf(DA_31["pdf_path"], test_data, "test_filled.pdf")
```

Open `test_filled.pdf` in Preview. Fields should display.

**Failure modes:**

| Symptom | Fix |
|---|---|
| Fields blank in Preview | Wrong field name. Re-check `extracted_schemas.json`. Or: `NeedAppearances` not honored — try opening in Acrobat (it always honors). |
| `pypdf` raises on `clone_from` | Old version — pin `pypdf==5.0.1` |
| Some fields are checkboxes | Their value should be `"/Yes"` not `"True"`. Detect with `fields[name]["/FT"] == "/Btn"`. |
| Date fields render in wrong format | Match the format the PDF expects — usually `YYYYMMDD` or `MMDDYYYY`. Try both. |
| Form has multiple pages | `writer.update_page_form_field_values(page, data)` only updates that page — already iterating all pages, good. |

**Acceptance gate:** open `test_filled.pdf` in Preview → all 11 DA-31 fields populated.

---

### STEP 7 — Wire LLM extraction → PDF fill (45 min)

The pipeline already exists in `server.py` `/query`. The change is making sure the LLM returns JSON whose keys match the (now-actual) PDF field names.

**Update `prompts.FORM_EXTRACTION_PROMPT`:**
- Replace `schema` substitution to send the *actual PDF field names* with their descriptions.
- Add an explicit instruction: *"Use these field names exactly. Do not invent new keys."*

**Test via curl:**
```bash
curl -s -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{
    "query": "I am Sergeant Maya Chen at Fort Bragg. I need to file ten days of ordinary leave starting June 3 to visit family in Atlanta. Emergency contact Maria Chen 919-555-0144.",
    "form_id": "DA-31"
  }' | python -m json.tool
```

**Expected:** `pdf_url` is set; downloading it shows a populated DA-31.

**Failure modes:**

| Symptom | Fix |
|---|---|
| `form_data` is `{}` | LLM didn't return JSON — check `format="json"` is passed to ollama.chat. If unsupported, end prompt with "Return ONLY a JSON object, no prose." |
| `form_data` has wrong keys | LLM invented field names — strengthen prompt with explicit `Use ONLY these field names: <list>` |
| `missing_fields` is huge | LLM is conservative — that's actually fine, it forces user to clarify. For demo: ensure your demo voice request includes all required fields verbatim |
| `pdf_url` set but PDF blank when opened | Field-name mismatch — re-run `extract_form_schemas.py`, double-check |

**Acceptance gate:** curl returns `pdf_url`; downloading the PDF shows all required DA-31 fields populated correctly with the demo data.

---

### STEP 8 — Browser-end demo of the leave flow (30 min)

Hit `http://localhost:8000/web/`. Hold mic. Say:
> *"I am Sergeant Maya Chen at Fort Bragg. I need to file ten days of ordinary leave starting June 3 to visit family in Atlanta. Emergency contact Maria Chen 919 555 0144."*

**Expected behavior:**
1. Transcript appears
2. Spoken reply plays (cites AR 600-8-10)
3. Citations panel populates
4. Form section shows "Filled 11 fields"
5. PDF iframe shows the populated DA-31
6. Download link works

**Acceptance gate:** Naomi can run the entire flow without touching the keyboard.

---

### STEP 9 — DD-1351-2 with deterministic per-diem math (1.5h)

**Modify `server.py` `/query` route** so when `target_form == "DD-1351-2"`, after LLM extraction, override the per-diem fields with `per_diem.calculate_tdy_total(...)` output. This guarantees the math is right regardless of LLM math ability.

```python
# Inside server.py /query, in the form-fill branch:
if target_form == "DD-1351-2" and form_data:
    from adjutant.per_diem import calculate_tdy_total
    location_field = form_data.get(<DD_1351_2 destination field name>)
    days_field = form_data.get(<DD_1351_2 total days field name>)
    if location_field and days_field:
        # crude parse — "Fort Polk, LA" or "Leesville, LA"
        city, _, state = location_field.partition(",")
        per_diem = calculate_tdy_total(city.strip(), state.strip(), int(days_field))
        form_data[<lodging field>] = per_diem["lodging_per_day"]
        form_data[<m&ie field>] = per_diem["mie_per_day"]
        form_data[<estimated total field>] = per_diem["estimated_total"]
```

**Note:** the field-name placeholders depend on what `extract_form_schemas.py` returned for DD-1351-2. Substitute real names.

**Test via curl:**
```bash
curl -s -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{
    "query": "I need to attend the JRTC mission rehearsal at Fort Polk for 5 days starting July 14. Home station Fort Bragg. Sergeant Chen, E-5.",
    "form_id": "DD-1351-2"
  }' | python -m json.tool
```

**Expected:** `lodging_per_day` = 110, `mie_per_day` = 68, `estimated_total` ≈ 822.

**Acceptance gate:** PDF opens with correct per-diem rates and total.

---

### STEP 10 — DA-4856 counseling form (45 min)

Less math, more language. Test with: *"I need to counsel Specialist Marcus Garcia for being late to formation twice this week. Purpose is corrective."*

**Expected:** `purpose`, `key_points`, `plan_of_action` populated in Army-bullet style. PDF renders without text clipping.

**Failure modes:**

| Symptom | Fix |
|---|---|
| Bullets too long, clipped in PDF | Add to system prompt: "Each key_points entry must fit on one line, ≤80 chars" |
| Wrong counseling type (Performance vs Event) | Strengthen prompt to choose based on stated purpose: tardiness → Event-Oriented |
| Missing counselor name | LLM has to ask — add "If counselor name not stated, return null and add to missing_fields" |

**Acceptance gate:** demo voice request → DA-4856 PDF with all required fields populated.

---

### STEP 11 — Multi-form from one prompt (1h) ⭐ HIGH-IMPACT WIN

**Modify `server.py`:**

```python
def _infer_forms(query: str) -> list[str]:
    """Return a LIST of forms to generate (was: single form)."""
    q = query.lower()
    forms = []
    if any(k in q for k in ("leave", "vacation", "time off")):
        forms.append("DA-31")
    if any(k in q for k in ("tdy", "travel", "voucher", "trip", "jrtc", "ntc", "per diem")):
        forms.append("DD-1351-2")
    if any(k in q for k in ("counsel", "counseling", "corrective", "magic-bullet")):
        forms.append("DA-4856")
    return forms
```

**Modify the `/query` route:**
- Accept `form_id` as `str | list[str] | None`
- If `None`, call `_infer_forms` (returns list)
- Iterate over forms, generate each PDF, return a list

**Update `QueryResponse` model:**
```python
class FormResult(BaseModel):
    form_id: str
    form_data: dict
    missing_fields: list[str] = []
    pdf_url: str | None = None

class QueryResponse(BaseModel):
    spoken_summary: str
    citations: list[dict]
    forms: list[FormResult] = []
    audio_url: str | None = None
```

**Update `web/app.js` `renderReply`** to iterate over `forms[]` and render an iframe for each.

**Test:**
```bash
curl -s -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Sergeant Chen, E-5, Fort Bragg. I am going to JRTC at Fort Polk July 14 for 5 days. I need to counsel Specialist Garcia tomorrow for being late to formation. And I want 2 days of leave June 3 and 4."
  }' | python -m json.tool
```

**Expected:** 3 PDFs returned in `forms[]`. Each has its own `pdf_url` and `form_data`.

**Acceptance gate:** browser shows 3 iframes, each with a different filled PDF.

---

### STEP 12 — Context-aware refusal (20 min)

**Modify `prompts.SYSTEM_PROMPT`** — add COMPLIANCE RULES section:

```
COMPLIANCE RULES (apply BEFORE generating any form):

- If user requests >30 consecutive days of ordinary leave: respond
  "Per AR 600-8-10 paragraph 4-3, leave over 30 days requires next-higher-commander approval.
   I'll draft for 30 days and flag the extension for your battalion commander."
   Then draft for exactly 30 days.

- If user requests TDY without stating a purpose: ask for the purpose. Do not generate
  the form until purpose is provided.

- If retrieved context does not contain regulation language directly relevant to the
  user's request: refuse with REFUSAL_OUT_OF_CORPUS.
```

**Test:**
```bash
curl -s -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"query": "I want 35 days of ordinary leave"}' | python -m json.tool
```

**Expected:** spoken_summary cites AR 600-8-10 4-3, mentions battalion commander approval, generates form for 30 days only.

**Acceptance gate:** the demo "35 days" query produces the correct cited refusal.

---

### STEP 13 — Out-of-corpus refusal demo (10 min)

Already implemented in `llm.answer_query` — if `chunks` is empty, returns `REFUSAL_OUT_OF_CORPUS`.

**Test:**
```bash
curl -s -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"query": "What does AR 27-10 say about court-martial convening authority?"}' | python -m json.tool
```

(AR 27-10 is NOT in our corpus.)

**Expected:** `spoken_summary` is the refusal message; `citations` is `[]`; `forms` is `[]`.

**Failure mode:** RAG returns a tangentially-related chunk from AR 600-8-10 with low score → LLM tries to answer. **Fix:** add a score threshold in `rag.retrieve` — if top score < 0.5, return `[]`.

```python
# In rag.retrieve, add:
SCORE_THRESHOLD = 0.45  # tune empirically
out = [c for c in out if c["score"] >= SCORE_THRESHOLD]
```

**Acceptance gate:** AR 27-10 query → refusal; AR 600-8-10 query → cited answer.

---

### STEP 14 — Eval harness (1.5h) ⭐ THE MOHINDRA MOVE

**Create `tests/eval_questions.json`:**

```json
[
  {"id": "leave-accrual",         "q": "How many days of ordinary leave does an enlisted soldier accrue per month?",          "must_cite": "AR 600-8-10", "expected_keyword": "2.5"},
  {"id": "max-leave-days",        "q": "What is the maximum days of ordinary leave a soldier can take without higher approval?", "must_cite": "AR 600-8-10", "expected_keyword": "30"},
  {"id": "emergency-leave",       "q": "What qualifies as emergency leave under Army regulation?",                              "must_cite": "AR 600-8-10", "expected_keyword": "emergency"},
  {"id": "convalescent-leave",    "q": "What is convalescent leave and who authorizes it?",                                     "must_cite": "AR 600-8-10", "expected_keyword": "medical"},
  {"id": "leave-form-number",     "q": "What is the DA form number for requesting leave?",                                      "must_cite": "AR 600-8-10", "expected_keyword": "31"},
  {"id": "tdy-per-diem-conus",    "q": "What is the standard CONUS lodging per-diem rate when no specific city rate applies?",  "must_cite": "Joint Travel Regulations", "expected_keyword": "110"},
  {"id": "tdy-mie-default",       "q": "What is the default M&IE rate per day on TDY?",                                         "must_cite": "Joint Travel Regulations", "expected_keyword": "68"},
  {"id": "tdy-travel-day-rate",   "q": "What percentage of M&IE applies on travel days?",                                       "must_cite": "Joint Travel Regulations", "expected_keyword": "75"},
  {"id": "tdy-voucher-deadline",  "q": "Within how many days after TDY must a travel voucher be filed?",                        "must_cite": "Joint Travel Regulations", "expected_keyword": "5"},
  {"id": "leave-pass-distinction","q": "What is the difference between leave and a pass?",                                      "must_cite": "AR 600-8-10", "expected_keyword": "pass"},
  {"id": "ord-leave-cap",         "q": "What is the cap on the amount of leave a soldier may carry over fiscal years?",         "must_cite": "AR 600-8-10", "expected_keyword": "60"},
  {"id": "OUT-1-court-martial",   "q": "What does AR 27-10 say about court-martial convening authority?",                       "must_cite": "OUT_OF_CORPUS"},
  {"id": "OUT-2-uniform",         "q": "What does AR 670-1 say about beard authorization?",                                     "must_cite": "OUT_OF_CORPUS"},
  {"id": "OUT-3-promotion",       "q": "How many promotion points are required for promotion to Sergeant under HRC policy?",    "must_cite": "OUT_OF_CORPUS"},
  {"id": "OUT-4-fitness",         "q": "What is the minimum ACFT score required for graduation from BLC?",                      "must_cite": "OUT_OF_CORPUS"},
  ...
]
```

(15 questions minimum: 11 in-corpus, 4 out-of-corpus.)

**Create `tests/run_eval.py`:**

```python
"""Run the eval set against Adjutant. Prints per-question pass/fail + total."""

import json
from pathlib import Path

from adjutant.llm import answer_query
from adjutant.rag import retrieve

QUESTIONS = Path("tests/eval_questions.json")


def evaluate_one(q: dict) -> dict:
    chunks = retrieve(q["q"], top_k=5)
    answer = answer_query(q["q"], chunks)
    summary = answer["spoken_summary"].lower()
    cites = [c["source"] for c in answer["citations"]]

    if q["must_cite"] == "OUT_OF_CORPUS":
        # Pass if Adjutant correctly refused
        passed = (
            "don't have" in summary
            or "regulation corpus" in summary
            or len(answer["citations"]) == 0
        )
        reason = "refused as expected" if passed else f"hallucinated citation: {cites}"
    else:
        # Pass if it cited the right source AND mentioned the expected keyword
        cited_correctly = any(q["must_cite"] in src for src in cites)
        keyword_present = q.get("expected_keyword", "").lower() in summary
        passed = cited_correctly and keyword_present
        reason = (
            f"cited={cited_correctly} keyword={keyword_present} "
            f"(citations={cites})"
        )

    return {"id": q["id"], "passed": passed, "reason": reason}


def main() -> None:
    questions = json.loads(QUESTIONS.read_text())
    results = [evaluate_one(q) for q in questions]
    passed = sum(1 for r in results if r["passed"])

    print(f"\n=== Adjutant eval: {passed}/{len(results)} ({100*passed/len(results):.0f}%) ===\n")
    for r in results:
        mark = "✓" if r["passed"] else "✗"
        print(f"{mark} {r['id']:30s} {r['reason']}")


if __name__ == "__main__":
    main()
```

**Run:**
```bash
python tests/run_eval.py
```

**Target:** 80%+ pass rate. Below that → investigate failures, tune prompts/threshold.

**Manual GenAI.mil comparison (separate, not automated):**
- Open https://gemini.genai.mil/ in browser (requires CAC).
- For each in-corpus question, paste it into Gemini chat.
- Screenshot any response that hallucinates a regulation it doesn't actually have.
- Drop screenshots in `docs/screenshots/`.

**Acceptance gate:** Adjutant eval ≥80%, ≥2 GenAI.mil hallucination screenshots saved.

---

### STEP 15 — Startup warmup for fast demo (15 min)

**Add to `adjutant/server.py`:**

```python
@app.on_event("startup")
async def warmup():
    """Pre-load Whisper + Ollama so the first user request is fast."""
    log.info("Warming Whisper…")
    from adjutant.stt import _get_model
    _get_model()  # forces lazy load

    log.info("Warming Ollama…")
    try:
        from adjutant.llm import _client, MODEL
        _client.chat(
            model=MODEL,
            messages=[{"role": "user", "content": "ready?"}],
            options={"num_predict": 5},
        )
    except Exception as e:
        log.warning(f"Ollama warmup failed (non-fatal): {e}")

    log.info("Warmup done.")
```

**Acceptance gate:** server takes ~15s to start, but first user request returns in <8s.

---

### STEP 16 — Wifi-disconnect verification (15 min)

Run server, perform full demo flow with wifi ON. Then:
1. `sudo ifconfig en0 down` (Mac wired off) and turn wifi off in menu bar
2. Reload browser, repeat demo flow
3. Verify everything still works
4. `sudo ifconfig en0 up` + wifi back on

**Failure mode:** browser blocks `MediaRecorder` if it perceives "no permissions" — should not happen if mic was already granted. If it does, refresh page after disabling network and re-grant.

**Acceptance gate:** full demo flow works with no network. Online/offline badge visible.

---

### STEP 17 — README, demo script, tag, push (1h)

1. **README** — already drafted; update the "Team" section with final member names.
2. **Demo GIF** — record one full run with QuickTime. Convert to GIF (`brew install ffmpeg gifski`, `ffmpeg -i demo.mov demo.gif`). Drop in `docs/`. Reference in README hero.
3. **`git status`** clean — no `.env`, no model weights, no filled PDFs committed.
4. **Tag** — `git tag v0.1-scsp && git push origin --tags`
5. **Submission email** — fill from `docs/SCSP_REGISTRATION_EMAIL.md` template, send 5:00 PM Sunday.

**Acceptance gate:** repo URL renders cleanly in browser; submission email sent on time.

---

## Module-by-module reference

### `adjutant/server.py` — FastAPI entry

**Routes:**
- `GET /` — `{"name", "version", "offline": True}`
- `GET /health` — `{"status": "ok"}`
- `GET /forms` — list of registered forms
- `POST /transcribe` — multipart audio → `{"text": str}`
- `POST /query` — `{query, form_id?}` → `QueryResponse` (text + citations + forms[] + audio_url)
- `POST /voice` — multipart audio → full pipeline → `QueryResponse`

**Mounts:**
- `/web/*` → static frontend
- `/filled/*` → generated PDFs
- `/audio/*` → generated TTS

**Adds during build:** `_infer_forms` (Step 11), `warmup` (Step 15), per-diem post-processing for DD-1351-2 (Step 9).

### `adjutant/stt.py` — faster-whisper

- `transcribe(audio_bytes: bytes) -> str`
- Lazy-loads `WhisperModel`, configurable via env (`WHISPER_MODEL`, `WHISPER_DEVICE`, `WHISPER_COMPUTE_TYPE`).
- `initial_prompt` includes military jargon to steer transcription.

**Don't touch unless STT is broken.** It's solid.

### `adjutant/llm.py` — Ollama client

- `answer_query(query, chunks) -> {"spoken_summary", "citations"}` — refuses if chunks empty.
- `extract_form_data(query, chunks, schema) -> {"data", "missing_fields"}` — uses Ollama `format="json"`.

**Adds during build:** none. Just verify behavior.

### `adjutant/rag.py` — FAISS retrieval

- `retrieve(query, top_k=5) -> list[chunk]`
- Lazy-loads embedder + index.
- **Add a score threshold** during Step 13 to suppress low-confidence retrievals.

### `adjutant/forms.py` — form schemas

- `DA_31`, `DD_1351_2`, `DA_4856` — placeholder field names.
- **Replace with extracted field names during Step 6.**

### `adjutant/pdf_fill.py` — pypdf wrapper

- `fill_pdf(template_path, data, output_path)` — keys in `data` must match PDF field names.
- Handles `NeedAppearances`.

### `adjutant/tts.py`

- `synthesize(text, output_path)` — Chatterbox if `CHATTERBOX_URL` set, else pyttsx3.
- pyttsx3 is bulletproof. Robot voice is acceptable.

### `adjutant/per_diem.py` — GSA cache + math

- `lookup(city, state, travel_date)` — local JSON cache; FY26 defaults if miss.
- `calculate_tdy_total(city, state, days, travel_date)` — handles 75% travel-day rule.

### `adjutant/prompts.py`

- `SYSTEM_PROMPT` — answer constraints, citation requirement, voice format rules.
- `REFUSAL_OUT_OF_CORPUS` — verbatim refusal text.
- `FORM_EXTRACTION_PROMPT` — JSON-only output for form-fill.
- **Add COMPLIANCE RULES during Step 12.**

---

## Test suite contract

Tests in `tests/`:

- `test_forms.py` — registry sanity (4 tests). Run any time.
- `test_offline.py` — monkey-patches `socket.getaddrinfo` to ensure no external DNS lookups during inference. **Critical**. Run before submitting.
- `test_eval.py` (Step 14, optional but recommended) — runs `eval_questions.json` against the live system.

Run: `pytest tests/ -v`.

---

## Branch and commit strategy

- `main` is always demo-runnable. If a change might break the demo, branch.
- Branches: `feat/multi-form`, `feat/refusal`, `feat/eval-harness`, `fix/pdf-fields`.
- Merge to main only after the acceptance gate is green.
- Tag at end of each phase: `v0.1-phase1`, `v0.1-phase2`, etc. Easy rollback.
- Final tag: `v0.1-scsp`.

---

## Risk matrix and remediation

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| `gh repo create` blocked by GitHub auth issues | Low | High | Pre-auth `gh auth login` before Saturday |
| `ollama pull llama3.1:8b` fails on hackathon wifi | Medium | High | Tether to phone hotspot; or use already-cached model |
| AcroForm field names from extracted PDFs are unstable | High | Medium | Simpler fallback: render filled values as overlay text using `reportlab` over the blank PDF (last resort) |
| Whisper too slow on M2 CPU | Medium | Medium | Switch to `medium.en` model (4× faster, ~5% accuracy hit) |
| `ollama` chokes on 8B at int8 | Low | High | Fall back to `llama3.2:3b` or `phi3:mini` |
| Eval harness shows poor refusal rate | Medium | Medium | Lower the score threshold in rag; tighten prompt with explicit refusal triggers |
| Demo laptop crashes mid-presentation | Low | Catastrophic | Have a recorded video backup on USB stick |

---

## What to do when stuck (the escalation order)

1. **Check `acceptance gate` for the current step.** If it's not met, the prior step had a hidden failure.
2. **Check the `Failure modes` table** for the current step.
3. **Revert to the last green tag.** `git checkout v0.1-phaseN`. Re-attempt with the cut-list applied.
4. **Cut the current feature.** Reference `BUILD_PLAN.md` cut-list.
5. **Sleep.** Most "I can't figure this out" moments at hour 22 resolve in 5 minutes after a 20-minute nap.

---

## The non-negotiables

- ✅ Voice in
- ✅ Citation by source name
- ✅ At least one filled PDF on screen
- ✅ Wifi-cable yank moment
- ✅ Out-of-corpus refusal demo
- ✅ Public GitHub repo URL in submission email by 5:00 PM Sunday

If those 6 things are true at submission time, we shipped a defensible entry. Everything else is upside.
