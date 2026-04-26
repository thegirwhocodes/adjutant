"""Text-to-speech for Adjutant.

Primary: Kokoro-82M ONNX (Apache 2.0, ~300ms warm on M2, fully offline).
Fallback: pyttsx3 system TTS, then Apple `say` command.

Sabi-pattern additions:
- Pre-generated cue audio cache (loaded at server startup) for instant playback
  during LLM prefill. The user hears "Checking the regs..." within ~50ms of
  finishing speaking, while the 6-second context prefill runs in parallel.
- Async wrapper around the synchronous Kokoro generate() so coroutines never
  block the event loop.
- Returns raw PCM16 WAV bytes for the streaming WebSocket path; also writes
  to disk for the legacy /query HTTP path.
"""

import asyncio
import io
import logging
import os
import subprocess
import threading
from pathlib import Path

import numpy as np
import soundfile as sf

log = logging.getLogger("adjutant.tts")

KOKORO_MODEL = os.getenv("KOKORO_MODEL", "models/kokoro/kokoro-v1.0.onnx")
KOKORO_VOICES = os.getenv("KOKORO_VOICES", "models/kokoro/voices-v1.0.bin")
KOKORO_VOICE = os.getenv("KOKORO_VOICE", "am_michael")
CUE_DIR = Path(os.getenv("CUE_DIR", "audio_cache/cues"))

_kokoro = None
_kokoro_lock = threading.Lock()
_cue_cache: dict[str, bytes] = {}  # name -> WAV bytes


def _get_kokoro():
    """Lazy-load Kokoro. Call _warmup() at server startup to avoid first-request lag."""
    global _kokoro
    with _kokoro_lock:
        if _kokoro is None:
            from kokoro_onnx import Kokoro
            log.info(f"Loading Kokoro ONNX from {KOKORO_MODEL}")
            _kokoro = Kokoro(KOKORO_MODEL, KOKORO_VOICES)
            log.info("Kokoro ready.")
        return _kokoro


def warmup() -> None:
    """Server-startup warmup. Loads Kokoro and pre-warms the inference graph
    by synthesizing a one-word phrase. Also loads pre-generated cue audio
    files into RAM for zero-latency playback.

    Called from server.py @app.on_event("startup").
    """
    k = _get_kokoro()
    samples, sr = k.create("ready", voice=KOKORO_VOICE, speed=1.0, lang="en-us")
    log.info(f"Kokoro warmup ok ({len(samples)/sr:.2f}s test audio).")

    if not CUE_DIR.exists():
        log.warning(f"Cue dir {CUE_DIR} missing — thinking cues will not play. "
                    f"Run scripts/generate_cues.py to populate.")
        return

    n = 0
    for wav_path in sorted(CUE_DIR.glob("*.wav")):
        with open(wav_path, "rb") as f:
            _cue_cache[wav_path.stem] = f.read()
        n += 1
    log.info(f"Loaded {n} cue audio files into RAM.")


def get_cue(name: str) -> bytes | None:
    """Return cached cue WAV bytes, or None if missing.

    Names: thinking_0..thinking_4, retry_low_conf, ack_da31, ack_dd13512, ack_da4856.
    """
    return _cue_cache.get(name)


def list_cues() -> list[str]:
    return sorted(_cue_cache)


def synthesize_pcm(text: str, voice: str | None = None) -> tuple[np.ndarray, int]:
    """Synchronous synthesis. Returns (float32_samples, sample_rate)."""
    k = _get_kokoro()
    samples, sr = k.create(text, voice=voice or KOKORO_VOICE, speed=1.0, lang="en-us")
    return samples, sr


def synthesize_wav_bytes(text: str, voice: str | None = None) -> bytes:
    """Synchronous synthesis returning a self-contained WAV blob.

    Used both by the streaming WS path (each sentence → one WAV chunk that
    the browser decodes with audioContext.decodeAudioData) and by the legacy
    /query path (writes to disk).
    """
    samples, sr = synthesize_pcm(text, voice=voice)
    return _to_wav_bytes(samples, sr)


async def synthesize_wav_bytes_async(text: str, voice: str | None = None) -> bytes:
    """Async wrapper — Sabi pattern. Runs Kokoro in a thread so the event
    loop stays free to interleave WS sends, VAD frames, and other coroutines.
    """
    return await asyncio.to_thread(synthesize_wav_bytes, text, voice)


def _to_wav_bytes(samples: np.ndarray, sr: int) -> bytes:
    """Float32 numpy array → WAV bytes (PCM16 mono).

    Writes a standard 44-byte RIFF header so the bytes are self-decodable in
    a browser via AudioContext.decodeAudioData() — no fragile MSE / streaming
    container plumbing required.
    """
    buf = io.BytesIO()
    sf.write(buf, samples, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Legacy file-based API used by the existing /query HTTP route in server.py.
# Keep the same signature so that route doesn't need touching during V0–V5.
# ---------------------------------------------------------------------------

def synthesize(text: str, output_path: str) -> str:
    """Render text to a WAV file at output_path. Tries Kokoro first; falls
    back to pyttsx3, then macOS `say`. Returns output_path on success.
    """
    try:
        wav_bytes = synthesize_wav_bytes(text)
        with open(output_path, "wb") as f:
            f.write(wav_bytes)
        return output_path
    except Exception as e:
        log.warning(f"Kokoro synth failed ({e!r}), falling back to pyttsx3")

    try:
        _pyttsx3_synth(text, output_path)
        return output_path
    except Exception as e:
        log.warning(f"pyttsx3 failed ({e!r}), falling back to macOS `say`")

    _say_synth(text, output_path)
    return output_path


def _pyttsx3_synth(text: str, output_path: str) -> None:
    import pyttsx3
    engine = pyttsx3.init()
    engine.setProperty("rate", 180)
    engine.save_to_file(text, output_path)
    engine.runAndWait()


def _say_synth(text: str, output_path: str) -> None:
    """Last-resort fallback using macOS built-in `say`. Always works."""
    aiff_tmp = output_path + ".aiff"
    subprocess.run(["say", "-v", "Samantha", "-o", aiff_tmp, text],
                   check=True, capture_output=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                    "-i", aiff_tmp, "-ar", "24000", "-ac", "1", output_path],
                   check=True, capture_output=True)
    try:
        os.unlink(aiff_tmp)
    except OSError:
        pass