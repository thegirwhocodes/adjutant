"""Inspect blank PDFs in forms/ and print their AcroForm field names.

Use the output to update adjutant/forms.py with the actual field names.
PDFs from Army Publishing Directorate use cryptic names like 'topmostSubform[0].Page1[0].FormalName[0]'.
"""

import json
import sys
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent.parent
FORMS = ROOT / "forms"


def main() -> int:
    if not FORMS.exists():
        print(f"No forms dir: {FORMS}", file=sys.stderr)
        return 1

    pdfs = sorted(FORMS.glob("*.pdf"))
    if not pdfs:
        print(f"No PDFs in {FORMS}. Run: python scripts/download_corpus.py", file=sys.stderr)
        return 1

    out = {}
    for pdf in pdfs:
        try:
            reader = PdfReader(str(pdf))
            fields = reader.get_fields() or {}
        except Exception as e:
            print(f"{pdf.name}: ERROR {e}", file=sys.stderr)
            continue

        if not fields:
            print(f"{pdf.name}: no AcroForm fields (likely a flat scan)")
            out[pdf.name] = {"fields": [], "note": "flat scan — no fillable fields"}
            continue

        names = sorted(fields.keys())
        out[pdf.name] = {"fields": names, "field_count": len(names)}
        print(f"\n=== {pdf.name} ({len(names)} fields) ===")
        for n in names:
            ft = fields[n].get("/FT", "")
            print(f"  {n}  [{ft}]")

    schema_file = FORMS / "extracted_schemas.json"
    schema_file.write_text(json.dumps(out, indent=2))
    print(f"\nWrote {schema_file}")
    print("Update adjutant/forms.py with the actual field names from this output.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
