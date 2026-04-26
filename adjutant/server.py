"""Adjutant server — FastAPI entry point. Voice + form endpoints, all offline."""

import logging
import os
import uuid
from pathlib import Path

# Force offline mode for HuggingFace + Transformers BEFORE any HF library
# imports happen. Without these, faster-whisper and sentence-transformers
# do network HEAD/GET freshness checks against huggingface.co on every
# model load — fine when online, slow timeout disasters at the wifi-pulled
# demo. The actual model files are cached locally; we just don't want HF
# checking for updates. Set ADJUTANT_ALLOW_NET=1 in dev to disable.
if not os.getenv("ADJUTANT_ALLOW_NET"):
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from adjutant.forms import list_forms, get_schema
from adjutant.llm import answer_query, extract_form_data
from adjutant.pdf_fill import fill_pdf
from adjutant.rag import retrieve
from adjutant.stt import transcribe
from adjutant.tiers import retrieve_tiered, tier_status, shutdown as tiers_shutdown
from adjutant.tts import synthesize, warmup as tts_warmup
from adjutant.voice_loop import VoiceLoop

load_dotenv()

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("adjutant")

app = FastAPI(
    title="Adjutant",
    description="Voice-first, offline AI assistant for Army paperwork.",
    version="0.1.0",
)

FILLED_DIR = Path("filled_forms")
FILLED_DIR.mkdir(exist_ok=True)
AUDIO_DIR = Path("audio_cache")
AUDIO_DIR.mkdir(exist_ok=True)

app.mount("/web", StaticFiles(directory="web", html=True), name="web")
app.mount("/filled", StaticFiles(directory="filled_forms"), name="filled")
app.mount("/audio", StaticFiles(directory="audio_cache"), name="audio")


# ---------------------------------------------------------------------------
# Startup warmup — preload Kokoro + Silero + cue files + Whisper + Ollama so
# the first turn after server boot doesn't pay any cold-load tax. Without
# this, the demo's first voice query can take 15+ seconds.
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def _warmup() -> None:
    log.info("Warming Adjutant pipeline…")
    try:
        tts_warmup()
    except Exception as e:
        log.warning(f"TTS warmup failed (will fall back at runtime): {e}")

    try:
        from adjutant.stt import _get_model
        _get_model()
    except Exception as e:
        log.warning(f"STT warmup failed: {e}")

    try:
        from silero_vad import load_silero_vad
        load_silero_vad(onnx=True)
    except Exception as e:
        log.warning(f"VAD warmup failed: {e}")

    try:
        from adjutant.llm import _client, MODEL
        _client.chat(
            model=MODEL,
            messages=[{"role": "user", "content": "ready"}],
            options={"num_predict": 5},
        )
    except Exception as e:
        log.warning(f"Ollama warmup failed: {e}")

    # RAG cold-start: sentence-transformers' first .encode() does HF freshness
    # checks + builds the FAISS query path. ~15s without this. After this
    # call, every retrieve() is sub-100ms.
    try:
        retrieve("warmup query", top_k=1)
    except Exception as e:
        log.warning(f"RAG warmup failed: {e}")

    log.info("Warmup complete. Adjutant ready.")


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class QueryRequest(BaseModel):
    query: str
    # Either a single form id or a list. None = auto-infer from the query.
    form_id: str | list[str] | None = None


class FormResult(BaseModel):
    """One filled form. /query returns a list of these so a single voice
    request like 'I'm going to JRTC, need to counsel Garcia, want 2 days
    of leave when I get back' produces three PDFs in one shot."""
    form_id: str
    form_data: dict
    missing_fields: list[str] = []
    pdf_url: str | None = None


class QueryResponse(BaseModel):
    spoken_summary: str
    citations: list[dict]
    forms: list[FormResult] = []
    audio_url: str | None = None
    # Tier provenance for the answer's evidence (which tiers contributed).
    # Populated when chunks have a `tier` attribute (HOT/WARM/COLD).
    tiers_used: list[str] = []
    # Legacy single-form fields kept so existing frontends don't break.
    # Populated from forms[0] when forms is non-empty.
    form_data: dict | None = None
    missing_fields: list[str] = []
    pdf_url: str | None = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
def root():
    return {"name": "Adjutant", "version": "0.1.0", "offline": True}


@app.get("/health")
async def health():
    """Tier-aware health. Returns per-tier {status, latency_ms, chunk_count}.

    Frontend polls this every 2s to drive the HOT/WARM/COLD status LEDs.
    """
    tiers = await tier_status()
    return {
        "status": "ok",
        "tiers": tiers,
        "graceful_degradation": tiers["HOT"]["status"] == "up",
    }


@app.on_event("shutdown")
async def _shutdown():
    await tiers_shutdown()


@app.get("/forms")
def forms():
    return list_forms()


@app.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    """Take an audio blob from the browser mic, return transcribed text."""
    audio_bytes = await file.read()
    text = transcribe(audio_bytes)
    return {"text": text}


@app.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest):
    """End-to-end pipeline: text query → RAG → LLM → 0-N form fills → TTS audio.

    Returns a list of FormResult under `forms[]` so a single voice request
    can produce DA-31 + DD-1351-2 + DA-4856 from one sentence.
    """
    # 1. Retrieve relevant regulation chunks (HOT + WARM + COLD in parallel,
    #    each tier degrades independently — see adjutant/tiers.py).
    top_k = int(os.getenv("TOP_K", "5"))
    chunks = await retrieve_tiered(req.query, top_k=top_k)
    log.info(f"Retrieved {len(chunks)} chunks for: {req.query!r}")

    # 2. Generate grounded answer
    answer = answer_query(req.query, chunks)

    # 3. Determine which forms to generate.
    if req.form_id is None:
        target_forms = _infer_forms(req.query)
    elif isinstance(req.form_id, list):
        target_forms = req.form_id
    else:
        target_forms = [req.form_id]

    if target_forms:
        log.info(f"Generating {len(target_forms)} form(s): {target_forms}")

    # 4. Iterate: extract → post-process → fill PDF for each form.
    results: list[FormResult] = []
    for fid in target_forms:
        try:
            schema = get_schema(fid)
        except KeyError as e:
            log.warning(f"Form not registered: {e}")
            continue

        extraction = extract_form_data(req.query, chunks, schema)
        form_data = extraction.get("data") or {}
        missing = extraction.get("missing_fields", [])

        # Per-form post-processing
        if fid == "DA-31":
            _correct_leave_type(form_data, req.query)
        if fid == "DD-1351-2":
            _wire_per_diem(form_data)

        if not form_data:
            log.info(f"{fid}: no form_data extracted, skipping PDF")
            continue

        out_pdf = FILLED_DIR / f"{fid}-{uuid.uuid4().hex[:8]}.pdf"
        try:
            fill_pdf(schema["pdf_path"], form_data, str(out_pdf), schema=schema)
            pdf_url = f"/filled/{out_pdf.name}"
            log.info(f"Filled {fid} → {pdf_url} (missing: {missing or 'none'})")
        except Exception as e:
            log.warning(f"Fill {fid} failed (continuing): {e}")
            pdf_url = None

        results.append(FormResult(
            form_id=fid,
            form_data=form_data,
            missing_fields=missing,
            pdf_url=pdf_url,
        ))

    # 5. Synthesize spoken summary
    audio_url = None
    try:
        audio_path = AUDIO_DIR / f"reply-{uuid.uuid4().hex[:8]}.wav"
        synthesize(answer["spoken_summary"], str(audio_path))
        audio_url = f"/audio/{audio_path.name}"
    except Exception as e:
        log.warning(f"TTS failed (non-fatal): {e}")

    # Backwards-compat: populate legacy single-form fields from forms[0].
    legacy_data = results[0].form_data if results else None
    legacy_missing = results[0].missing_fields if results else []
    legacy_pdf = results[0].pdf_url if results else None

    # Tier provenance — which tiers' chunks contributed to the answer.
    tiers_used = sorted({c.get("tier", "HOT") for c in chunks if c})

    # Tag each citation with its tier so the frontend can render badges.
    citations_with_tier = []
    for cit, chunk_obj in zip(answer["citations"], chunks):
        cit_dict = dict(cit) if isinstance(cit, dict) else cit
        cit_dict["tier"] = chunk_obj.get("tier", "HOT")
        citations_with_tier.append(cit_dict)

    return QueryResponse(
        spoken_summary=answer["spoken_summary"],
        citations=citations_with_tier,
        forms=results,
        audio_url=audio_url,
        tiers_used=tiers_used,
        form_data=legacy_data,
        missing_fields=legacy_missing,
        pdf_url=legacy_pdf,
    )


def _correct_leave_type(form_data: dict, query: str) -> None:
    """DA-31 leave_type override: don't trust the LLM (RAG anchors it on
    'emergency' chunks). Default Ordinary unless the soldier explicitly said
    otherwise. Strips 'emergency contact ...' phrase first to avoid false
    positives."""
    import re as _re
    q_lower = _re.sub(r"emergency contact[^.,;]*", " ", query.lower())
    explicit = [
        ("Emergency",    ("emergency leave", "family emergency", "death in",
                          "funeral", "family member died", "next of kin")),
        ("Convalescent", ("convalescent", "surgery", "recovery", "medical leave")),
        ("Terminal",     ("terminal leave", "ets-ing", "separation",
                          "retiring", "ets leave")),
        ("PTDY",         ("ptdy", "permissive tdy", "house hunting")),
    ]
    detected = "Ordinary"
    for label, kws in explicit:
        if any(k in q_lower for k in kws):
            detected = label
            break
    if form_data.get("leave_type") != detected:
        log.info(f"Override leave_type {form_data.get('leave_type')!r} → {detected!r}")
        form_data["leave_type"] = detected


@app.post("/voice")
async def voice_pipeline(file: UploadFile = File(...), form_id: str | None = None):
    """Full voice-in → voice-out pipeline. Single endpoint for the demo."""
    audio_bytes = await file.read()
    text = transcribe(audio_bytes)
    log.info(f"Transcribed: {text!r}")
    return await query(QueryRequest(query=text, form_id=form_id))


@app.websocket("/ws/voice")
async def ws_voice(ws: WebSocket) -> None:
    """Continuous voice loop. Browser sends 32 ms PCM16 frames as binary;
    server streams back JSON state events + WAV audio chunks. See
    adjutant/voice_loop.py for the pipeline.
    """
    await ws.accept()
    loop = VoiceLoop(ws)
    log.info("WS /ws/voice connected")
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if "bytes" in msg and msg["bytes"] is not None:
                await loop.feed_audio(msg["bytes"])
            elif "text" in msg and msg["text"] is not None:
                await loop.handle_text(msg["text"])
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.error(f"ws_voice error: {e}", exc_info=True)
    finally:
        await loop.shutdown()
        log.info("WS /ws/voice closed")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wire_per_diem(form_data: dict) -> None:
    """Mutate DD-1351-2 form_data in place: replace any LLM-null lodging /
    M&IE / total values with deterministically-calculated GSA per-diem from
    adjutant.per_diem. Also normalizes start_date/end_date the LLM produced
    into depart_date/return_date the form schema expects.
    """
    from adjutant.per_diem import calculate_tdy_total

    # The LLM may emit dates under DA-31 keys ("start_date") or DD-1351-2
    # keys ("depart_date"). Normalize.
    if not form_data.get("depart_date") and form_data.get("start_date"):
        form_data["depart_date"] = form_data["start_date"]
    if not form_data.get("return_date") and form_data.get("end_date"):
        form_data["return_date"] = form_data["end_date"]

    location = form_data.get("tdy_location") or ""
    days = form_data.get("total_days") or form_data.get("days_requested") or 0
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 0
    if not (location and days > 0):
        log.info(f"per_diem skip: location={location!r} days={days!r}")
        return

    city, _, state = location.partition(",")
    pd = calculate_tdy_total(city.strip(), state.strip(), days)
    form_data["lodging_per_day"] = pd["lodging_per_day"]
    form_data["mie_per_day"] = pd["mie_per_day"]
    form_data["estimated_total"] = pd["estimated_total"]
    form_data["total_days"] = days
    log.info(
        f"per_diem: {city.strip()},{state.strip()} {days}d "
        f"lodging=${pd['lodging_per_day']} M&IE=${pd['mie_per_day']} "
        f"total=${pd['estimated_total']} ({pd['source']})"
    )


_INTENT_PROMPT = """You are an intent classifier for a U.S. Army voice paperwork assistant.

Read the soldier's transcript and decide which DA / DD forms (if any) they
are asking to file. Return a JSON object exactly like:

  {"forms": ["DA-31", "DD-1351-2"], "reason": "<one short sentence>"}

The only valid form IDs are:
  - "DA-31"      — Request and Authority for Leave   (any time off / vacation request)
  - "DD-1351-2"  — Travel Voucher                    (TDY trips, JRTC/NTC, per-diem reimbursement)
  - "DA-4856"    — Developmental Counseling Form     (counseling a subordinate, corrective action)

CRITICAL RULES:
  1. Return an EMPTY array if the soldier is only asking a question OR
     making a casual statement that isn't a paperwork request
     ("how does leave accrue?", "what is per diem?", "tell me about AR
     600-8-10", "I leave the office at 1700", "Garcia is late again").
     Statements without a clear paperwork-filing intent → no forms.
  2. A single sentence can trigger MULTIPLE forms — list them all.
     ("I'm going to JRTC for 5 days AND need 2 days of leave when I get back"
      → ["DD-1351-2", "DA-31"]).
  3. Use the soldier's intent verbs as the trigger. The trigger MUST be
     "file/submit/draft/request a form" or "need/want X days of leave"
     or "counsel <person>" (with explicit subject) or "going to attend".
     Bare statements describing a routine ("I leave the office at 1700")
     are NOT paperwork requests.
  4. Phrases like "I leave on Tuesday" (verb sense) DO NOT count as DA-31.
     Only the noun sense ("ten days of leave", "request leave") counts.
  5. "Counsel <name>" or "write up <name>" with an explicit subordinate
     name = DA-4856. Without a name, no DA-4856.
  6. Return ONLY the JSON object. No prose, no markdown, no <tags>.

EXAMPLES:

Transcript: "How does ordinary leave accrue?"
JSON: {"forms": [], "reason": "Pure Q&A about leave accrual policy."}

Transcript: "I need to file ten days of ordinary leave starting June 3."
JSON: {"forms": ["DA-31"], "reason": "Soldier requesting ordinary leave."}

Transcript: "I need to attend the JRTC mission rehearsal at Fort Polk for 5 days."
JSON: {"forms": ["DD-1351-2"], "reason": "TDY travel to JRTC."}

Transcript: "Need to counsel SPC Garcia for being late to formation."
JSON: {"forms": ["DA-4856"], "reason": "Corrective counseling."}

Transcript: "I'm going to JRTC July 14 for 5 days, need to counsel Garcia tomorrow, and want 2 days of leave when I get back."
JSON: {"forms": ["DD-1351-2", "DA-4856", "DA-31"], "reason": "TDY plus counseling plus leave in one breath."}

Transcript: "I leave the office at 1700."
JSON: {"forms": [], "reason": "Verb sense of 'leave' — no DA-31 intent."}

TRANSCRIPT: {query}

JSON:
"""


def _infer_forms_llm(query: str) -> list[str] | None:
    """LLM-based intent classifier. Returns the list of forms the soldier
    wants filled, or None if the LLM call / parse failed (caller should
    fall back to regex).

    Uses the same Ollama client + model the rest of the pipeline uses.
    Cheap call: ~50–80 input tokens of prompt + transcript, ~30 output
    tokens. Adds ~300–500 ms warm to the turn — worth it for the
    accuracy boost over regex pattern matching on real soldier speech.
    """
    import json
    import os
    import re as _re
    try:
        from adjutant.llm import _client, MODEL
        prompt = _INTENT_PROMPT.replace("{query}", query)
        resp = _client.chat(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.0, "num_predict": 100},
            format="json",
        )
        raw = resp["message"]["content"].strip()
        # Trim any stray prose before/after the JSON object.
        m = _re.search(r"\{.*\}", raw, _re.DOTALL)
        if m:
            raw = m.group(0)
        data = json.loads(raw)
        forms = data.get("forms", [])
        # Validate against the known form IDs — defend against hallucinated ones.
        valid = {"DA-31", "DD-1351-2", "DA-4856"}
        forms = [f for f in forms if f in valid]
        log.info(f"_infer_forms_llm({query[:60]!r}): {forms} — {data.get('reason','')}")
        return forms
    except Exception as e:
        log.warning(f"_infer_forms_llm failed ({e!r}) — falling back to regex")
        return None


def _infer_forms(query: str) -> list[str]:
    """Hybrid intent classifier — combines a fast regex Q&A filter with the
    LLM's semantic understanding.

    The regex alone misses real intents like "Counsel SPC Garcia" because
    its "actionable verb" gate is strict. The LLM (llama3.2:3b) catches
    those but over-triggers on pure Q&A like "how does leave accrue?"
    because keyword presence outweighs intent at this model size.

    Hybrid: use the regex's Q&A test first to short-circuit obvious
    questions; ask the LLM only when the transcript is plausibly an
    action request. Best of both, no false positives on Q&A, full
    semantic recall on phrased-naturally requests.

    Override env: ADJUTANT_INTENT_REGEX_ONLY=1 forces pure regex.
    """
    import os
    if os.getenv("ADJUTANT_INTENT_REGEX_ONLY") in ("1", "true", "yes"):
        return _infer_forms_regex(query)

    # Fast Q&A filter — if the regex thinks this is a pure question,
    # don't waste an LLM call.
    if _looks_like_pure_question(query):
        log.info(f"_infer_forms: pure-Q&A short-circuit on {query[:60]!r}")
        return []

    forms = _infer_forms_llm(query)
    if forms is not None:
        return forms
    # Ollama unhealthy — fall back so the demo never silently fails.
    return _infer_forms_regex(query)


def _looks_like_pure_question(query: str) -> bool:
    """True if the transcript is structured as a question without any
    action-request verb. Catches 'How does X work?', 'Tell me about Y',
    'What is Z?' etc. — the soldier wants information, not a form.
    """
    import re
    q = query.lower().strip()
    starts_q = q.startswith((
        "how ", "what ", "when ", "why ", "where ", "who ",
        "does ", "do ", "is ", "are ", "tell me", "explain",
        "show me", "describe",
    ))
    has_action_verb = bool(re.search(
        r"\b(file|submit|draft|fill|request|put in|need|want|going to|"
        r"counsel|i'?ll|i'?m going|i will|would like)\b",
        q,
    ))
    if starts_q and not has_action_verb:
        return True
    return False


def _infer_forms_regex(query: str) -> list[str]:
    """Original regex-based classifier kept as a fallback for when the
    LLM is unhealthy. Same contract as _infer_forms — returns a list of
    valid form IDs (possibly empty)."""
    import re
    q = query.lower()

    # Hard-exclude pure questions
    starts_with_q = q.lstrip().startswith(
        ("how ", "what ", "when ", "why ", "where ", "who ", "does ", "do ")
    )
    if starts_with_q and "?" in q and not any(
        v in q for v in ("file", "submit", "draft", "request")
    ):
        return []

    actionable = bool(
        re.search(r"\bi\s+(need|want|am going|'m going|'ll|will|would like)\b", q)
        or re.search(r"\b(file|submit|draft|fill|request|put in)\b", q)
        or re.search(r"\b\d+\s+days?\s+of\b", q)
        or re.search(r"\bstarting\s+(june|july|aug|sep|oct|nov|dec|jan|feb|mar|apr|may|next)\b", q)
        or re.search(r"\bgoing to\s+(attend|the|fort|jrtc|ntc|atlanta|\w+)\b", q)
    )
    if not actionable:
        return []

    forms: list[str] = []
    if any(k in q for k in ("tdy", "travel", "voucher", "per diem", "trip",
                            "dd-1351", "dd 1351", "jrtc", "ntc",
                            "mission rehearsal", "training trip")):
        forms.append("DD-1351-2")
    if re.search(r"\b(leave|vacation|time off|da-?31)\b", q):
        if re.search(r"\b\d+\s+days?\b.*\bleave\b", q) or re.search(
            r"\bleave\b.*(starting|from|june|july|aug|sep|oct|nov|dec|jan|feb|mar|apr|may)", q
        ) or re.search(r"\b(file|submit|draft|request|put in|need|want|days? of)\b.*\bleave\b", q) \
           or re.search(r"\bleave\b.*\b(starting|when|after|before)\b", q):
            forms.append("DA-31")
    if any(k in q for k in ("counsel", "counseling", "da-4856", "da 4856",
                            "corrective", "performance review")):
        forms.append("DA-4856")
    return forms


def _infer_form(query: str) -> str | None:
    """Backwards-compat wrapper for code that expects a single form."""
    forms = _infer_forms(query)
    return forms[0] if forms else None
