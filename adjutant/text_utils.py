"""Display-only text utilities, shared by voice_loop + llm to avoid
circular imports. Keep purely text-shaping functions here — anything
that touches Whisper / Ollama / Kokoro / Silero belongs in its own
module."""

import re

# Pre-compiled patterns for citation trimming. The corpus was chunked at
# fixed char boundaries during FAISS ingestion, so most chunks start and
# end mid-sentence. The browser citation panel was displaying that raw
# garbage ("wing day is the first day of travel..."). We trim on the
# DISPLAY side: skip everything before the first capital letter after a
# sentence terminator, and end at the last terminator we can find. No
# re-ingestion required.
_SENT_START = re.compile(
    r"(?:(?<=[.!?]\s)|(?<=[.!?]\n)|(?<=\n\n))[A-Z]|(?<=^)[A-Z]"
)
_SENT_END = re.compile(r"[.!?](?=\s|$)")


def clean_citation_quote(raw: str, target_len: int = 320) -> str:
    """Trim a FAISS chunk to clean sentence boundaries for display.

    Strategy:
      1. Find the first capital letter that follows a sentence
         terminator (or the start). That's the first "real" sentence.
      2. From there, find the LAST sentence terminator within
         ~target_len chars. That's the trim end.
      3. If no clean start found, prepend "…" so the user knows
         it's mid-sentence. Same for the end.
    """
    if not raw:
        return ""
    text = raw.strip()
    m = _SENT_START.search(text)
    if m:
        start = m.start()
        prefix = "" if start == 0 else "…"
    else:
        start = 0
        prefix = "…"
    body = text[start:start + target_len + 60]
    matches = list(_SENT_END.finditer(body))
    if matches:
        body = body[:matches[-1].end()]
        suffix = ""
    else:
        body = body[:target_len].rstrip()
        suffix = "…"
    return (prefix + body + suffix).strip()
