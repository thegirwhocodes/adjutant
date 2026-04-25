"""Local Whisper STT via faster-whisper. No network calls."""

import io
import logging
import os
import tempfile

from dotenv import load_dotenv
from faster_whisper import WhisperModel

# Load .env before reading any env var (see note in llm.py).
load_dotenv()

log = logging.getLogger("adjutant.stt")

_MODEL: WhisperModel | None = None


def _get_model() -> WhisperModel:
    """Lazy-load the Whisper model so import is fast."""
    global _MODEL
    if _MODEL is None:
        size = os.getenv("WHISPER_MODEL", "small.en")
        device = os.getenv("WHISPER_DEVICE", "cpu")
        compute = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
        log.info(f"Loading Whisper {size} on {device} ({compute})")
        _MODEL = WhisperModel(size, device=device, compute_type=compute)
    return _MODEL


def transcribe(audio_bytes: bytes) -> str:
    """Transcribe a webm/wav/mp3 audio blob to text. Returns empty string on silence."""
    model = _get_model()

    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        segments, info = model.transcribe(
            tmp_path,
            language="en",
            initial_prompt=(
                "US Army terminology: TDY, PCS, NCO, S1, AR, FM, JTR, DA-31, DD-1351-2, "
                "DA-4856, BAH, BAS, leave, voucher, per diem, Fort Bragg, Fort Polk, JRTC, "
                "battalion, company, platoon, squad."
            ),
            vad_filter=True,
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        log.info(f"STT ({info.duration:.1f}s audio): {text[:80]!r}")
        return text
    finally:
        os.unlink(tmp_path)
