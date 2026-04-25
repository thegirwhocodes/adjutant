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

            if form_data and not missing:
                out_pdf = FILLED_DIR / f"{target_form}-{uuid.uuid4().hex[:8]}.pdf"
                fill_pdf(schema["pdf_path"], form_data, str(out_pdf))
                pdf_url = f"/filled/{out_pdf.name}"
                log.info(f"Filled {target_form} → {pdf_url}")
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

def _infer_form(query: str) -> str | None:
    """Lightweight intent classifier — substring match on form keywords."""
    q = query.lower()
    if any(k in q for k in ("leave", "vacation", "time off", "da-31", "da 31")):
        return "DA-31"
    if any(k in q for k in ("tdy", "travel", "voucher", "per diem", "trip", "dd-1351", "dd 1351")):
        return "DD-1351-2"
    if any(k in q for k in ("counsel", "counseling", "da-4856", "da 4856")):
        return "DA-4856"
    return None
