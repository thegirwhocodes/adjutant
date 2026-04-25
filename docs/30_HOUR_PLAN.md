# 30-hour build plan

| Block | Time | Task | Owner |
|---|---|---|---|
| Sat 10:00–11:00 | 1h | Registration; team confirmation; track selection | All |
| Sat 11:00–11:30 | 0.5h | Repo cloned to all laptops; `pip install -r requirements.txt`; `ollama pull llama3.1:8b` running in background | All |
| Sat 11:30–13:00 | 1.5h | `scripts/download_corpus.py` (downloads ARs + JTR + blank forms); ingestion runs (`scripts/ingest_corpus.py`); test retrieval against 5 known questions | Person A |
| Sat 11:30–13:00 | 1.5h | `scripts/extract_form_schemas.py` against blank DA-31 + DD-1351-2 + DA-4856; update `adjutant/forms.py` with real PDF field names; manual round-trip test of `pdf_fill.py` | Person B |
| Sat 11:30–13:00 | 1.5h | Find a veteran or active-duty teammate in the room; pressure-test the persona flow with them | Naomi |
| Sat 13:00–14:00 | 1h | LUNCH + venue switch (vacate hackathon space at 1:00) | All |
| Sat 14:00 | — | **TEAM REGISTRATION EMAIL DUE — `hack@scsp.ai`** | Naomi |
| Sat 14:00–17:00 | 3h | First end-to-end vertical slice: voice → STT → RAG → LLM (no form fill yet) → TTS reply. Get the latency budget right (~5s end-to-end). | Person A + Naomi |
| Sat 14:00–17:00 | 3h | DA-31 form-fill flow working from a typed query (no voice yet). Test with 5 leave scenarios from AR 600-8-10. | Person B |
| Sat 17:00–20:00 | 3h | Stitch voice + form-fill: full DA-31 flow end-to-end. Naomi rehearses the demo script aloud. | All |
| Sat 20:00–22:00 | 2h | DD-1351-2 + per-diem math. Test with Fort Polk, Fort Bragg, Atlanta scenarios. | Person A |
| Sat 20:00–22:00 | 2h | DA-4856 form-fill + counseling-language LLM constraint. Cite AR 623-3. | Person B |
| Sat 20:00–22:00 | 2h | Frontend polish: net-status badge, citation panel, audio playback | Naomi |
| Sat 22:00–24:00 | 2h | Hallucination tests: 10 deliberate out-of-corpus questions; assert refusal; tighten prompt if needed | Person A |
| Sat 22:00–24:00 | 2h | Offline test: pull wifi physically, run all three flows. Fix anything that breaks. | Person B |
| Sun 00:00–06:00 | 6h | SLEEP (negotiable; recommend at least 4h) | All |
| Sun 06:00–10:00 | 4h | Demo rehearsal × 5. Time it. Fix the slow parts. Practice the wifi cable yank. | All |
| Sun 10:00–13:00 | 3h | Buffer for last-minute breakage. README polish. Push to GitHub public. | All |
| Sun 13:00–16:00 | 3h | Final dry-run with a friendly stranger watching | All |
| Sun 16:00 | — | Submit GitHub link + README to hack@scsp.ai | Naomi |
| Sun 17:00–19:00 | 2h | **Judging window.** Demo to judges. | All |

## What can be cut if behind schedule

- **DA-4856** — drop it; lead with DA-31 + DD-1351-2 only. Saves ~3 hours.
- **Chatterbox TTS** — fall back to system TTS via pyttsx3 (already coded as fallback). Saves the time it takes to run Chatterbox locally.
- **AR 623-3 / FM 6-22 in corpus** — drop them; demo only needs AR 600-8-10 + JTR. Saves ingestion time and reduces hallucination risk on adjacent regs.
- **Custom CSS** — switch to a single Pico.css drop-in. Saves an hour.

## What can NOT be cut

- The wifi-disconnect demo
- Citing source + section/paragraph on every RAG answer
- The deliberate refusal demo (out-of-corpus question)
- At least one form rendering as a real PDF on screen
- README publicly accessible at submission time
