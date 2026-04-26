#!/usr/bin/env python3
"""Parse a Soldier Talent Profile (STP) or Enlisted Record Brief (ERB)
PDF exported from IPPS-A and merge the fields into Adjutant's profile.

IPPS-A (PeopleSoft-backed) generates the STP/ERB with consistent section
headers across the whole Army, so the layout is parseable by regex against
text extracted by pdfplumber. Soldier downloads from `my.ippsa.army.mil`
> Documents tile, then drops the PDF on this script.

Run:
    python scripts/import_erb.py /path/to/STP.pdf            # interactive confirm
    python scripts/import_erb.py /path/to/STP.pdf --yes      # auto-merge
    python scripts/import_erb.py /path/to/STP.pdf --dry-run  # parse only
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from adjutant.profile import load_profile, save_profile  # noqa: E402

try:
    import pdfplumber  # type: ignore
except Exception as e:
    print(f"ERR  need pdfplumber: pip install pdfplumber  ({e})", file=sys.stderr)
    sys.exit(2)


# Field patterns (loose — STP and ERB phrasing differ between IPPS-A versions
# and unit S-1 templates, so we accept several variants per field).
PATTERNS = {
    # Identity
    "name":       [r"NAME\s*[:\-]\s*([A-Z'\-]+),\s*([A-Z'\-]+)\s*([A-Z]?)\b",
                   r"\b([A-Z][A-Z'\-]+),\s*([A-Z][A-Z'\-]+)\s+([A-Z])\s+(?:E-\d|O-\d)"],
    "dodid":      [r"DOD\s*ID\s*(?:#|NUMBER)?\s*[:\-]?\s*(\d{10})",
                   r"\bEDIPI\s*[:\-]?\s*(\d{10})"],
    "ssn_last4":  [r"SSN\s*[:\-]?\s*(?:XXX-XX-|\*+)?(\d{4})\b"],
    "rank":       [r"\b(?:RANK|GRADE|PAY\s*GRADE)\s*[:\-]?\s*(E-\d|O-\d|W-\d)\b",
                   r"\b(E-\d|O-\d|W-\d)\b"],
    # Service
    "component":  [r"\b(?:COMPONENT|COMP)\s*[:\-]?\s*(RA|USAR|ARNG|AGR)\b",
                   r"\bCOMPO\s*[:\-]?\s*(\d)\b"],
    "mos":        [r"\b(?:PMOS|MOS|AOC)\s*[:\-]?\s*(\d{2}[A-Z]\d?)\b"],
    "basd":       [r"\bBASD\s*[:\-]?\s*(\d{4}[\-/]\d{2}[\-/]\d{2}|\d{2}\s+[A-Z]+\s+\d{4})"],
    "pebd":       [r"\bPEBD\s*[:\-]?\s*(\d{4}[\-/]\d{2}[\-/]\d{2}|\d{2}\s+[A-Z]+\s+\d{4})"],
    "ets":        [r"\bETS\s*[:\-]?\s*(\d{4}[\-/]\d{2}[\-/]\d{2}|\d{2}\s+[A-Z]+\s+\d{4})"],
    "dor":        [r"\b(?:DOR|DATE\s+OF\s+RANK)\s*[:\-]?\s*(\d{4}[\-/]\d{2}[\-/]\d{2}|\d{2}\s+[A-Z]+\s+\d{4})"],
    # Assignment
    "uic":        [r"\bUIC\s*[:\-]?\s*([A-Z0-9]{6})\b"],
    "duty_station":[r"\b(?:DUTY\s*STATION|STATION|INSTALLATION)\s*[:\-]?\s*([A-Z][A-Z\s]+,\s*[A-Z]{2}|FORT\s+[A-Z]+(?:\s*,\s*[A-Z]{2})?)"],
    "unit_name":  [r"\b(?:UNIT|ASSIGNMENT|ORGANIZATION)\s*[:\-]?\s*([A-Z0-9][A-Z0-9\s,\-/&\.]{4,80})"],
    # Contact
    "email":      [r"\b([\w.\-]+@(?:army|mail|us\.af|navy)\.mil)\b"],
    "phone_dsn":  [r"\bDSN\s*[:\-]?\s*(\d{3}[\-\.\s]?\d{4})"],
    "phone_commercial": [r"\b(?:COMM(?:ERCIAL)?|PHONE|PH)\s*[:\-]?\s*(\(?\d{3}\)?[\-\.\s]?\d{3}[\-\.\s]?\d{4})"],
}


def extract_text(pdf_path: Path) -> str:
    text_chunks: list[str] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            t = page.extract_text() or ""
            text_chunks.append(t)
    text = "\n".join(text_chunks)
    text = re.sub(r"[ \t]+", " ", text)
    return text


def parse(text: str) -> dict:
    out: dict = {"soldier": {}, "unit": {}, "service": {}}
    upper = text.upper()

    # Name (compound match — last/first/middle)
    for pat in PATTERNS["name"]:
        m = re.search(pat, upper)
        if m:
            out["soldier"]["name_last"]   = m.group(1).title()
            out["soldier"]["name_first"]  = m.group(2).title()
            if m.lastindex and m.lastindex >= 3 and m.group(3):
                out["soldier"]["name_middle"] = m.group(3).upper()
            break

    soldier_keys = {"dodid": "dodid", "ssn_last4": "ssn_last4", "rank": "rank",
                    "email": "email", "phone_dsn": "phone_dsn",
                    "phone_commercial": "phone_commercial"}
    for src, dst in soldier_keys.items():
        for pat in PATTERNS[src]:
            m = re.search(pat, upper if src not in ("email",) else text)
            if m:
                out["soldier"][dst] = m.group(1).strip().lower() if src == "email" else m.group(1).strip()
                break

    unit_keys = {"uic": "uic", "duty_station": "duty_station", "unit_name": "name"}
    for src, dst in unit_keys.items():
        for pat in PATTERNS[src]:
            m = re.search(pat, upper)
            if m:
                out["unit"][dst] = m.group(1).strip().title()
                break

    service_keys = {"component": "component", "mos": "mos",
                    "basd": "basd", "pebd": "pebd", "ets": "ets", "dor": "dor"}
    for src, dst in service_keys.items():
        for pat in PATTERNS[src]:
            m = re.search(pat, upper)
            if m:
                out["service"][dst] = m.group(1).strip()
                break

    # Drop empty top-level sections
    return {k: v for k, v in out.items() if v}


def merge_into_profile(parsed: dict) -> dict:
    profile = load_profile()
    for top, fields in parsed.items():
        cur = profile.setdefault(top, {})
        for k, v in fields.items():
            cur[k] = v
    save_profile(profile)
    return profile


def main() -> int:
    ap = argparse.ArgumentParser(description="Import STP/ERB into Adjutant profile.")
    ap.add_argument("pdf", help="Path to STP or ERB PDF (downloaded from IPPS-A).")
    ap.add_argument("--yes", action="store_true", help="Skip confirmation prompt.")
    ap.add_argument("--dry-run", action="store_true", help="Parse and print only.")
    args = ap.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"ERR  PDF not found: {pdf_path}", file=sys.stderr)
        return 2

    text = extract_text(pdf_path)
    if len(text) < 200:
        print("WARN extracted very little text — is this a scanned PDF? "
              "May need OCR (tesseract).", file=sys.stderr)

    parsed = parse(text)
    if not parsed:
        print("ERR  no fields matched. Save the IPPS-A STP as text-PDF and retry.",
              file=sys.stderr)
        return 3

    print("Parsed fields:")
    for top, fields in parsed.items():
        print(f"  [{top}]")
        for k, v in fields.items():
            print(f"    {k:18} = {v}")

    if args.dry_run:
        return 0

    if not args.yes:
        confirm = input("\nMerge into ~/.adjutant/profile.json? [y/N] ").strip().lower()
        if confirm != "y":
            print("Aborted.")
            return 0

    merge_into_profile(parsed)
    print("OK  profile updated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
