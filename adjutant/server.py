"""Adjutant server — FastAPI entry point. Voice + form endpoints, all offline."""

import logging
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from adjutant.forms import list_forms, get_schema
from adjutant.llm import answer_query, extract_form_data
from adjutant.pdf_fill import fill_pdf
from adjutant.rag import retrieve
from adjutant.stt import transcribe
from adjutant.tts import synthesize

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
def health():
    return {"status": "ok"}


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
    # 1. Retrieve relevant regulation chunks
    chunks = retrieve(req.query, top_k=int(os.getenv("TOP_K", "5")))
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

    return QueryResponse(
        spoken_summary=answer["spoken_summary"],
        citations=answer["citations"],
        forms=results,
        audio_url=audio_url,
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


def _infer_forms(query: str) -> list[str]:
    """Intent classifier — returns a LIST of forms to generate.

    A single utterance can trigger MULTIPLE forms when the soldier mentions
    leave AND TDY AND counseling in the same breath:

      "I'm going to JRTC at Fort Polk July 14 for 5 days, need to counsel
       SPC Garcia tomorrow, and want 2 days of leave when I get back."

    This is the demo's wow moment: one voice request → three filled PDFs.

    Pure Q&A ("How does leave accrue?", "What is per diem?") does NOT
    trigger anything.
    """
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
    # TDY first (more specific) — keywords that imply travel
    if any(k in q for k in ("tdy", "travel", "voucher", "per diem", "trip",
                            "dd-1351", "dd 1351", "jrtc", "ntc",
                            "mission rehearsal", "training trip")):
        forms.append("DD-1351-2")
    # Leave (only if 'leave' is used in the request-sense, not 'when I get back')
    if re.search(r"\b(leave|vacation|time off|da-?31)\b", q):
        # Avoid matching "I leave on Tuesday" — only count when leave looks like a noun
        if re.search(r"\b\d+\s+days?\b.*\bleave\b", q) or re.search(
            r"\bleave\b.*(starting|from|june|july|aug|sep|oct|nov|dec|jan|feb|mar|apr|may)", q
        ) or re.search(r"\b(file|submit|draft|request|put in|need|want|days? of)\b.*\bleave\b", q) \
           or re.search(r"\bleave\b.*\b(starting|when|after|before)\b", q):
            forms.append("DA-31")
    # Counseling
    if any(k in q for k in ("counsel", "counseling", "da-4856", "da 4856",
                            "corrective", "performance review")):
        forms.append("DA-4856")
    return forms


def _infer_form(query: str) -> str | None:
    """Backwards-compat wrapper for code that expects a single form."""
    forms = _infer_forms(query)
    return forms[0] if forms else None
