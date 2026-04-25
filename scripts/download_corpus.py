"""Download the corpus + blank forms. Run once at install.

Pulls only public, unclassified PDFs from US Government sources.
"""

import json
import logging
import sys
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("download_corpus")

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "corpus"
FORMS = ROOT / "forms"
CORPUS.mkdir(exist_ok=True)
FORMS.mkdir(exist_ok=True)

# Regulation PDFs to ingest into the FAISS index.
CORPUS_DOCS = [
    {
        "filename": "AR_600-8-10_Leaves_and_Passes.pdf",
        "url": "https://armypubs.army.mil/epubs/DR_pubs/DR_a/ARN30018-AR_600-8-10-000-WEB-1.pdf",
        "label": "AR 600-8-10",
    },
    {
        "filename": "JTR_2025-06.pdf",
        "url": "https://api.army.mil/e2/c/downloads/2025/06/10/0da05172/jtr-june-2025.pdf",
        "label": "Joint Travel Regulations (June 2025)",
    },
    # Add more as time permits during the hack:
    # {"filename": "AR_623-3.pdf", "url": "https://armypubs.army.mil/...", "label": "AR 623-3"},
    # {"filename": "FM_6-22.pdf",  "url": "https://armypubs.army.mil/...", "label": "FM 6-22"},
]

# Blank forms to populate.
FORMS_DOCS = [
    {
        "filename": "da_31_blank.pdf",
        "url": "https://home.army.mil/riley/9415/4456/0604/DA31_Leave_Form_PDF_Fillable.pdf",
    },
    {
        "filename": "dd_1351_2_blank.pdf",
        "url": "https://www.esd.whs.mil/Portals/54/Documents/DD/forms/dd/dd1351-2.pdf",
    },
    {
        "filename": "da_4856_blank.pdf",
        "url": "https://armypubs.army.mil/pub/eforms/DR_a/ARN20422_DA_FORM_4856_FINAL.pdf",
    },
]


def fetch(url: str, dest: Path) -> bool:
    """Download a single file. Skip if already present."""
    if dest.exists() and dest.stat().st_size > 1024:
        log.info(f"✔ {dest.name} (cached)")
        return True
    log.info(f"↓ {url}")
    try:
        resp = requests.get(url, timeout=60, headers={"User-Agent": "Adjutant/0.1"})
        resp.raise_for_status()
        dest.write_bytes(resp.content)
        log.info(f"✔ {dest.name} ({len(resp.content):,} bytes)")
        return True
    except Exception as e:
        log.error(f"✘ {dest.name}: {e}")
        return False


def write_per_diem_stub() -> None:
    """Drop a tiny GSA per-diem cache so the demo works without API access.

    During the hack, expand this with the GSA bulk download or live API calls.
    """
    sample = {
        "atlanta, GA":     {"lodging": 175, "mie": 79},
        "leesville, LA":   {"lodging": 110, "mie": 68},  # near Fort Polk
        "fayetteville, NC": {"lodging": 119, "mie": 68}, # near Fort Bragg
        "boston, MA":      {"lodging": 296, "mie": 92},
        "washington, DC":  {"lodging": 257, "mie": 92},
        "_state_default_GA": {"lodging": 110, "mie": 68},
        "_state_default_LA": {"lodging": 110, "mie": 68},
        "_state_default_NC": {"lodging": 110, "mie": 68},
    }
    out = CORPUS / "per_diem.json"
    out.write_text(json.dumps(sample, indent=2))
    log.info(f"✔ {out.name} (seed cache; expand from GSA API during hack)")


def main() -> int:
    failures = 0
    for doc in CORPUS_DOCS:
        if not fetch(doc["url"], CORPUS / doc["filename"]):
            failures += 1
    for doc in FORMS_DOCS:
        if not fetch(doc["url"], FORMS / doc["filename"]):
            failures += 1
    write_per_diem_stub()

    if failures:
        log.warning(f"{failures} download(s) failed — manually drop PDFs into corpus/ or forms/")
        return 1
    log.info("Corpus ready. Next: python scripts/ingest_corpus.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
