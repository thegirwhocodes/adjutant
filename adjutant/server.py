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
    form_id: str | None = None  # if set, attempt form extraction


class QueryResponse(BaseModel):
    spoken_summary: str
    citations: list[dict]
    form_data: dict | None = None
    missing_fields: list[str] = []
    pdf_url: str | None = None
    audio_url: str | None = None


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
    """End-to-end pipeline: text query → RAG → LLM → optional form fill → TTS audio."""
    # 1. Retrieve relevant regulation chunks
    chunks = retrieve(req.query, top_k=int(os.getenv("TOP_K", "5")))
    log.info(f"Retrieved {len(chunks)} chunks for: {req.query!r}")

    # 2. Generate grounded answer
    answer = answer_query(req.query, chunks)

    # 3. If a form_id was specified or inferred, extract structured form data
    form_data, missing, pdf_url = None, [], None
    target_form = req.form_id or _infer_form(req.query)
    if target_form:
        try:
            schema = get_schema(target_form)
            extraction = extract_form_data(req.query, chunks, schema)
            form_data = extraction.get("data")
            missing = extraction.get("missing_fields", [])

            # Hard-correct leave_type: don't trust the LLM here — RAG keeps
            # retrieving emergency-leave chunks and the model anchors on them.
            # Determine type from the soldier's actual words, defaulting to
            # Ordinary unless they explicitly said otherwise.
            if target_form == "DA-31" and form_data:
                import re as _re
                q_lower = req.query.lower()
                # Strip the "emergency contact <name + phone>" phrase first —
                # otherwise the word 'emergency' there triggers a false positive.
                q_lower = _re.sub(r"emergency contact[^.,;]*", " ", q_lower)

                explicit = [
                    ("Emergency",    ("emergency leave", "family emergency",
                                      "death in", "funeral", "family member died",
                                      "next of kin")),
                    ("Convalescent", ("convalescent", "surgery", "recovery",
                                      "medical leave")),
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

            # DD-1351-2 deterministic per-diem math: don't trust the LLM with
            # arithmetic. The LLM extracts city/state/days from the request;
            # adjutant.per_diem looks up the GSA rate and computes the totals
            # exactly per JTR Chapter 5 (75% on travel days, full on intervening).
            if target_form == "DD-1351-2" and form_data:
                _wire_per_diem(form_data)

            # Always render a PDF if we have form_data — fill what we know,
            # leave missing fields blank for the chain of command to complete.
            if form_data:
                out_pdf = FILLED_DIR / f"{target_form}-{uuid.uuid4().hex[:8]}.pdf"
                fill_pdf(schema["pdf_path"], form_data, str(out_pdf), schema=schema)
                pdf_url = f"/filled/{out_pdf.name}"
                log.info(f"Filled {target_form} → {pdf_url} (missing: {missing or 'none'})")
        except KeyError as e:
            log.warning(f"Form not registered: {e}")

    # 4. Synthesize spoken summary
    audio_url = None
    try:
        audio_path = AUDIO_DIR / f"reply-{uuid.uuid4().hex[:8]}.wav"
        synthesize(answer["spoken_summary"], str(audio_path))
        audio_url = f"/audio/{audio_path.name}"
    except Exception as e:
        log.warning(f"TTS failed (non-fatal): {e}")

    return QueryResponse(
        spoken_summary=answer["spoken_summary"],
        citations=answer["citations"],
        form_data=form_data,
        missing_fields=missing,
        pdf_url=pdf_url,
        audio_url=audio_url,
    )


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


def _infer_form(query: str) -> str | None:
    """Intent classifier — fires on action OR direct first-person statements.

    A form-fill is triggered when the user is *acting* (not asking). We treat
    these signals as actionable:
      - explicit action verbs: file, submit, draft, fill, request, put in, need
      - direct first-person leave/TDY framing: "I need", "I want", "I'm going",
        "I'll be at", "starting <date>", "<N> days of"

    Pure Q&A ("How does leave accrue?", "What is per diem?") does NOT trigger.
    """
    import re
    q = query.lower()

    # Hard exclude pure questions
    starts_with_q = q.lstrip().startswith(("how ", "what ", "when ", "why ",
                                           "where ", "who ", "does ", "do "))
    if starts_with_q and "?" in q and not any(
        v in q for v in ("file", "submit", "draft", "request")
    ):
        return None

    actionable = bool(
        re.search(r"\bi\s+(need|want|am going|'m going|'ll|will|would like)\b", q)
        or re.search(r"\b(file|submit|draft|fill|request|put in)\b", q)
        or re.search(r"\b\d+\s+days?\s+of\b", q)        # "ten days of"
        or re.search(r"\bstarting\s+(june|july|aug|sep|oct|nov|dec|jan|feb|mar|apr|may|next)\b", q)
        or re.search(r"\bgoing to\s+(attend|the|fort|jrtc|ntc|atlanta|\w+)\b", q)
    )
    if not actionable:
        return None

    if any(k in q for k in ("leave", "vacation", "time off", "da-31", "da 31")):
        return "DA-31"
    if any(k in q for k in ("tdy", "travel", "voucher", "per diem", "trip",
                            "dd-1351", "dd 1351", "jrtc", "ntc")):
        return "DD-1351-2"
    if any(k in q for k in ("counsel", "counseling", "da-4856", "da 4856")):
        return "DA-4856"
    return None
