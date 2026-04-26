"""Local LLM via Ollama. Retrieval-grounded; refuses on out-of-corpus questions."""

import json
import logging
import os
import re

import ollama
from dotenv import load_dotenv

from adjutant.prompts import (
    FORM_EXTRACTION_PROMPT,
    REFUSAL_OUT_OF_CORPUS,
    SYSTEM_PROMPT_STATIC,
)
from adjutant.text_utils import clean_citation_quote

# Load .env BEFORE reading any env var — otherwise constants captured at
# import time use the defaults below instead of the values in .env.
load_dotenv()

log = logging.getLogger("adjutant.llm")

MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")

_client = ollama.Client(host=HOST)


def _format_context(chunks: list[dict]) -> str:
    """Format retrieved chunks for inclusion in the prompt with source labels."""
    if not chunks:
        return "(no relevant regulation chunks retrieved)"
    lines = []
    for i, c in enumerate(chunks, 1):
        src = c.get("source", "unknown")
        sec = c.get("section", "")
        head = f"[{i}] {src}{' — ' + sec if sec else ''}"
        lines.append(f"{head}\n{c['text']}\n")
    return "\n".join(lines)


def answer_query(query: str, chunks: list[dict]) -> dict:
    """Generate a grounded, citation-bearing answer.

    Returns:
        {"spoken_summary": str, "citations": [{"source", "section", "quote"}]}
    """
    if not chunks:
        return {
            "spoken_summary": REFUSAL_OUT_OF_CORPUS,
            "citations": [],
        }

    # See answer_query_stream for the message-split rationale.
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT_STATIC},
        {"role": "user",
         "content": f"RETRIEVED CONTEXT:\n{_format_context(chunks)}\n\nUSER REQUEST:\n{query}"},
    ]

    resp = _client.chat(
        model=MODEL,
        messages=messages,
        options={"temperature": 0.2, "num_predict": 400},
    )
    text = resp["message"]["content"].strip()

    # Strip any <form_data> tags from the spoken summary (handled separately).
    spoken = re.sub(r"<form_data>.*?</form_data>", "", text, flags=re.DOTALL).strip()

    citations = [
        {
            "source": c.get("source", "unknown"),
            "section": c.get("section", ""),
            "quote": clean_citation_quote(c["text"]),
        }
        for c in chunks
    ]

    return {"spoken_summary": spoken, "citations": citations}


# Phrase-end pattern (was sentence-end). Split on . ! ? , ; : — followed by
# REQUIRED whitespace. ElevenLabs Flash + Cartesia Sonic both fire TTS per
# phrase rather than per full sentence, which drops first-audio latency by
# ~900ms because we don't wait for the LLM to emit a full ~25-token sentence
# before starting Kokoro on the first ~5-token phrase.
#
# Negative lookbehinds avoid splitting on:
#   - decimals (2.5)             — digit before .
#   - acronyms / initials (U.S.) — single uppercase before .
#   - common abbrev (e.g. i.e.)  — explicit Eg/Ie patterns
#   - rank ranges (E-5.)         — handled by the digit lookbehind
# Whitespace is REQUIRED so we never flush a partial phrase whose terminator
# might be a decimal point still being streamed (mid-token "2." waiting for "5").
#
# We also enforce a minimum phrase length (MIN_PHRASE_CHARS) so we don't fire
# a Kokoro synth on "Per AR," before the rest of the citation arrives —
# Kokoro prosody is awful on 2-word fragments.
MIN_PHRASE_CHARS = 24

_PHRASE_RE = re.compile(
    r"(?<![A-Z])"          # not preceded by uppercase (handles U.S., E-5.)
    r"(?<!\d)"             # not preceded by a digit (handles 2.5)
    r"(?<!\be\.g)"
    r"(?<!\bi\.e)"
    r"(.+?[.!?,;:])"
    r"(\s+)"
)
# Hard sentence-end fallback used when nothing has flushed in a while —
# guarantees we don't sit on a giant unbroken paragraph forever.
_SENTENCE_RE = re.compile(
    r"(?<![A-Z])(?<!\d)(?<!\be\.g)(?<!\bi\.e)(.+?[.!?])(\s+)"
)


def answer_query_stream(query: str, chunks: list[dict]):
    """Synchronous generator yielding sentences as the LLM produces them.

    Yields tuples of (sentence: str, is_final: bool). is_final=True means the
    sentence is fully formed (terminator hit) or it is the trailing partial
    flushed at end of stream.

    Caller is responsible for running this in a thread (asyncio.to_thread)
    since ollama-python's stream is blocking.

    Refusal contract preserved: if chunks is empty, the whole REFUSAL is
    yielded as one sentence — no LLM call.
    """
    if not chunks:
        yield (REFUSAL_OUT_OF_CORPUS, True)
        return

    # KV-cache-friendly message split:
    #   - system message is byte-identical every turn → fully cached
    #   - context block (often identical for follow-up Qs on same topic) → cached when same
    #   - query is the only thing that always varies
    # Ollama caches matching prefixes between calls automatically.
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT_STATIC},
        {"role": "user",
         "content": f"RETRIEVED CONTEXT:\n{_format_context(chunks)}\n\nUSER REQUEST:\n{query}"},
    ]

    buf = ""
    stream = _client.chat(
        model=MODEL,
        messages=messages,
        options={"temperature": 0.2, "num_predict": 400},
        stream=True,
    )

    for chunk in stream:
        delta = chunk["message"]["content"]
        if not delta:
            continue
        buf += delta
        # Strip <form_data> tags AND any leaking XML-ish markup from the
        # streamed text — we never want to speak structured-data tags.
        buf = re.sub(r"<form_data>.*?</form_data>", "", buf, flags=re.DOTALL)
        buf = re.sub(r"</?form_data>|</?command>", "", buf)

        # Flush every complete phrase as soon as it forms (and is long
        # enough to give Kokoro decent prosody).
        while True:
            m = _PHRASE_RE.search(buf)
            if not m:
                break
            phrase = m.group(1).strip()
            if len(phrase) < MIN_PHRASE_CHARS:
                # Too short — let it accumulate. Break out so we re-enter
                # on next token delta, by which time the buffer will be
                # longer and a later boundary will satisfy the min.
                break
            if phrase:
                yield (phrase, True)
            buf = buf[m.end():]

    # Flush any trailing partial (no terminal punctuation).
    tail = buf.strip()
    tail = re.sub(r"<form_data>.*?</form_data>", "", tail, flags=re.DOTALL).strip()
    tail = re.sub(r"</?form_data>|</?command>", "", tail).strip()
    if tail:
        yield (tail, True)


def extract_form_data(query: str, chunks: list[dict], schema: dict) -> dict:
    """Ask the LLM to populate the form schema from the user's request + context.

    Returns:
        {"data": dict, "missing_fields": list[str]}
    """
    schema_for_prompt = json.dumps(
        {k: v["desc"] for k, v in schema["fields"].items()},
        indent=2,
    )

    from adjutant.prompts import _today
    from adjutant.profile import llm_profile_json

    prompt = FORM_EXTRACTION_PROMPT.format(
        form_id=schema["form_id"],
        schema=schema_for_prompt,
        query=query,
        context=_format_context(chunks),
        today=_today(),
        profile_json=llm_profile_json(),
    )

    resp = _client.chat(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        options={"temperature": 0.1, "num_predict": 600},
        format="json",
    )
    raw = resp["message"]["content"]

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        log.warning(f"LLM returned invalid JSON: {raw[:200]!r}")
        return {"data": {}, "missing_fields": list(schema["fields"])}

    # Normalize: tolerate either flat output or {data, missing_fields} shape
    if "data" in parsed and isinstance(parsed["data"], dict):
        data = _clean_nulls(parsed["data"])
        return {
            "data": data,
            "missing_fields": parsed.get("missing_fields", []),
        }

    data = _clean_nulls(parsed)
    missing = [
        name for name, spec in schema["fields"].items()
        if spec["required"] and not data.get(name)
    ]
    return {"data": data, "missing_fields": missing}


def _clean_nulls(d: dict) -> dict:
    """LLMs occasionally emit the literal string 'null' for unknown fields
    instead of JSON null. Normalize both to empty so downstream form-fill
    doesn't write the word 'null' into a PDF field.
    """
    cleaned = {}
    for k, v in d.items():
        if v is None:
            cleaned[k] = ""
        elif isinstance(v, str) and v.strip().lower() in ("null", "none", "n/a", "tbd"):
            cleaned[k] = ""
        else:
            cleaned[k] = v
    return cleaned