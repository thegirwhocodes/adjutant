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
    SYSTEM_PROMPT,
)

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

    prompt = SYSTEM_PROMPT.format(
        context=_format_context(chunks),
        query=query,
    )

    resp = _client.chat(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        options={"temperature": 0.2, "num_predict": 400},
    )
    text = resp["message"]["content"].strip()

    # Strip any <form_data> tags from the spoken summary (handled separately).
    spoken = re.sub(r"<form_data>.*?</form_data>", "", text, flags=re.DOTALL).strip()

    citations = [
        {
            "source": c.get("source", "unknown"),
            "section": c.get("section", ""),
            "quote": c["text"][:300],
        }
        for c in chunks
    ]

    return {"spoken_summary": spoken, "citations": citations}


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

    prompt = FORM_EXTRACTION_PROMPT.format(
        form_id=schema["form_id"],
        schema=schema_for_prompt,
        query=query,
        context=_format_context(chunks),
        today=_today(),
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