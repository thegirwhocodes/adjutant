"""Local Whisper STT via faster-whisper. No network calls."""

import logging
import os
import shutil
import subprocess
import tempfile

from dotenv import load_dotenv
from faster_whisper import WhisperModel

# Load .env before reading any env var (see note in llm.py).
load_dotenv()

log = logging.getLogger("adjutant.stt")

_MODEL: WhisperModel | None = None
_FFMPEG = shutil.which("ffmpeg")  # cached at import; None if not installed


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


def _transcode_to_wav(src_path: str, dest_path: str) -> bool:
    """Transcode any audio format Chrome's MediaRecorder might emit (WebM/Opus,
    MP4/AAC, etc.) into 16kHz mono WAV — what Whisper actually wants.

    This is the reliable path: PyAV (faster-whisper's default decoder) chokes
    on Chrome's WebM container fragments roughly 1-in-3 calls. ffmpeg handles
    every browser variant cleanly.

    Returns True on success.
    """
    if _FFMPEG is None:
        log.warning("ffmpeg not on PATH; skipping transcode (will try PyAV directly)")
        return False
    try:
        subprocess.run(
            [
                _FFMPEG, "-y",
                "-loglevel", "error",
                "-i", src_path,
                "-ac", "1",         # mono
                "-ar", "16000",     # 16 kHz (Whisper's native rate)
                "-vn",              # no video
                "-f", "wav",
                dest_path,
            ],
            check=True,
            capture_output=True,
            timeout=15,
        )
        return True
    except subprocess.CalledProcessError as e:
        log.warning(f"ffmpeg transcode failed: {e.stderr.decode(errors='ignore')[:200]}")
        return False
    except subprocess.TimeoutExpired:
        log.warning("ffmpeg transcode timed out (>15s)")
        return False


def transcribe(audio_bytes: bytes) -> str:
    """Transcribe an audio blob to text. Returns "" on silence or decode failure.

    Pipeline: write blob to disk → ffmpeg transcode to 16kHz mono WAV →
    Whisper transcribe → cleanup.

    Browser MediaRecorder emits WebM/Opus on Chrome and MP4/AAC on Safari.
    ffmpeg handles both. If ffmpeg is missing, falls back to letting Whisper's
    PyAV decoder try directly (less reliable on browser audio).
    """
    if not audio_bytes:
        log.info("STT: empty audio bytes")
        return ""

    model = _get_model()

    # Write the raw blob to a temp file. Use a generic .bin extension so
    # ffmpeg sniffs the format from the data, not the filename.
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as tmp_in:
        tmp_in.write(audio_bytes)
        in_path = tmp_in.name

    out_path = in_path + ".wav"

    try:
        # Try the reliable path first: transcode via ffmpeg.
        if _transcode_to_wav(in_path, out_path):
            audio_path = out_path
        else:
            # Fallback: hand the raw blob to Whisper and pray PyAV can sniff it.
            audio_path = in_path

        try:
            segments, info = model.transcribe(
                audio_path,
                language="en",
                initial_prompt=(
                    "US Army terminology: TDY, PCS, NCO, S1, AR, FM, JTR, DA-31, "
                    "DD-1351-2, DA-4856, BAH, BAS, leave, voucher, per diem, "
                    "Fort Bragg, Fort Polk, JRTC, battalion, company, platoon, squad."
                ),
                vad_filter=True,
            )
            text = " ".join(s.text.strip() for s in segments).strip()
            log.info(f"STT ({info.duration:.1f}s audio): {text[:80]!r}")
            return text
        except Exception as e:
            # Don't 500 the request — return empty string and let the caller
            # render a "didn't catch that" message in the UI.
            log.error(f"STT decode failed: {type(e).__name__}: {e}")
            return ""
    finally:
        for p in (in_path, out_path):
            try:
                os.unlink(p)
            except OSError:
                pass
