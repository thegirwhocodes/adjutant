"""Text-to-speech. Uses Chatterbox if available; falls back to system TTS via pyttsx3."""

import logging
import os

import httpx

log = logging.getLogger("adjutant.tts")

CHATTERBOX_URL = os.getenv("CHATTERBOX_URL", "")  # empty = skip and fall back


def synthesize(text: str, output_path: str) -> str:
    """Render text to a WAV/MP3 file at output_path. Returns the path on success."""
    if CHATTERBOX_URL and _try_chatterbox(text, output_path):
        return output_path
    _system_tts(text, output_path)
    return output_path


def _try_chatterbox(text: str, output_path: str) -> bool:
    """POST to a local Chatterbox TTS server (Sabi pattern). Optional."""
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                f"{CHATTERBOX_URL}/tts",
                json={"text": text[:2000], "format": "wav", "exaggeration": 0.5},
            )
            resp.raise_for_status()
            with open(output_path, "wb") as f:
                f.write(resp.content)
        log.info(f"Chatterbox TTS: {len(text)} chars → {output_path}")
        return True
    except Exception as e:
        log.warning(f"Chatterbox unavailable, falling back: {e}")
        return False


def _system_tts(text: str, output_path: str) -> None:
    """pyttsx3 — uses macOS NSSpeechSynthesizer / Linux espeak / Windows SAPI5.

    100% offline. No network. Voice quality is mediocre but it ships.
    """
    import pyttsx3
    engine = pyttsx3.init()
    engine.setProperty("rate", 175)
    engine.save_to_file(text, output_path)
    engine.runAndWait()
    log.info(f"System TTS → {output_path}")
