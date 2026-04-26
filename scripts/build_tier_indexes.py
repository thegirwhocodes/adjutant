"""Partition the corpus into HOT/WARM/COLD and build a FAISS index per tier.

Production scale targets
------------------------
- HOT  : ~30 docs   / ~80K chunks   / ~150 MB on disk
        Most-touched regs (form-target ARs + JTR + everyday personnel/admin)
- WARM : ~500 docs  / ~500K chunks  / ~1.5 GB on disk
        Broader Army admin (discipline, property, awards, evals, body comp,
        substance abuse, in/out processing, leadership, training)
- COLD : ~5,000 docs / ~5M chunks   / ~15 GB on disk
        Full APD + DTIC technical reports + eCFR Title 32 + Joint Pubs

Each tier is its own subdirectory (`corpus/hot/`, `corpus/warm/`, `corpus/cold/`)
with its own FAISS index (`.faiss_index_hot/`, `.faiss_index_warm/`, etc.).

Tiering rules
-------------
A document's tier is determined by `_classify(filename, source_label)`:
  - HOT    : Source label matches HOT_SOURCES (an explicit allowlist of the
             ~30 regs that govern the demo forms + most-touched regs).
  - WARM   : Source label matches WARM_PATTERNS (broader Army admin, discipline,
             property, awards, body comp, substance abuse, etc.).
  - COLD   : Anything else — defaults to COLD (doctrine, RAND/DTIC reports,
             eCFR titles, joint pubs).

The classification is intentionally conservative: when in doubt, push to COLD,
because pushing too much to HOT bloats the local install, and pushing to WARM
when WARM is unreachable means the soldier loses access. HOT must be small.

Run
---
    python scripts/build_tier_indexes.py             # build all 3 from corpus/
    python scripts/build_tier_indexes.py --tier hot  # rebuild just one
    python scripts/build_tier_indexes.py --dry-run   # show partition only

Idempotent — safe to re-run after the bulk crawler adds new docs.
"""

from __future__ import annotations

import argparse
import logging
import pickle
import re
import shutil
import sys
from pathlib import Path

import faiss
from sentence_transformers import SentenceTransformer

# Re-use the existing chunker + section detector
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ingest_corpus import (  # type: ignore[import-not-found]
    EMBED_MODEL,
    chunk,
    detect_section,
    extract_pages,
    SOURCE_LABELS,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("build_tiers")

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "corpus"

# Per-tier subdirs and index dirs (created if missing).
TIER_DIRS = {
    "hot":  CORPUS / "hot",
    "warm": CORPUS / "warm",
    "cold": CORPUS / "cold",
}
TIER_INDEX_DIRS = {
    "hot":  ROOT / ".faiss_index_hot",
    "warm": ROOT / ".faiss_index_warm",
    "cold": ROOT / ".faiss_index_cold",
}


# ---------------------------------------------------------------------------
# Tier classification rules
# ---------------------------------------------------------------------------

# HOT — explicit allowlist of the ~30 regs that should always be local. These
# are the ones that govern the 3 demo forms + the most-touched personnel regs.
# A soldier with a closed laptop in a motor pool needs every one of these.
HOT_SOURCES: set[str] = {
    # The forms' governing regs (must-have for the 3-form demo)
    "AR 600-8-10",     # Leaves and Passes — DA-31
    "Joint Travel Regulations",  # JTR — DD-1351-2
    "AR 623-3",        # Evaluation Reporting — DA-4856
    # Most-touched personnel regs
    "AR 670-1",        # Wear and Appearance
    "AR 600-9",        # Body Composition (used daily by every leader)
    "AR 600-8-22",     # Awards (very common)
    "AR 600-8-101",    # Personnel Processing (in/out)
    "AR 600-8-2",      # Suspension of Favorable Personnel Actions (Flag)
    "AR 600-8-19",     # Enlisted Promotions and Reductions
    "AR 600-8-24",     # Officer Transfers and Discharges
    "AR 600-8-105",    # Military Orders
    "AR 600-20",       # Army Command Policy
    "AR 600-100",      # Army Profession and Leadership Policy
    # Everyday companion guides
    "DA Pam 600-25",   # NCO Guide
    "DA Pam 600-3",    # Officer Professional Development
    "DA Pam 600-67",   # Effective Writing for Army Leaders
    "DA Pam 600-8-22", # Awards procedures
    "DA Pam 25-50",    # Correspondence preparation
    # Foundational doctrine that gets cited often
    "FM 6-22",         # Leader Development
    "ADP 6-22",        # Army Leadership and the Profession
    "ADP 1",           # The Army
    "FM 7-0",          # Training
    "FM 7-22",         # Holistic Health and Fitness
    # Common admin
    "AR 25-50",        # Preparing and Managing Correspondence
    "AR 25-1",         # Army Information Technology
    "AR 25-22",        # Privacy and Civil Liberties
    "AR 350-1",        # Army Training and Leader Development
    "AR 25-400-2",     # Records management (ARIMS)
}

# WARM — pattern-based; broader admin / discipline / property / health.
# These are the regs a soldier might cite weekly but not daily.
WARM_PATTERNS = [
    r"^AR 27-",        # Legal/justice
    r"^AR 15-",        # Investigations
    r"^AR 638-",       # Mortuary affairs / casualty
    r"^AR 165-",       # Chaplain
    r"^AR 215-",       # MWR
    r"^AR 380-",       # Information security
    r"^AR 385-",       # Safety
    r"^AR 600-43",     # Conscientious objection
    r"^AR 600-85",     # Substance abuse
    r"^AR 614-",       # Officer/enlisted assignments
    r"^AR 615-",       # Officer assignment defaults
    r"^AR 690-",       # Civilian personnel
    r"^AR 608-",       # Family support
    r"^AR 40-",        # Medical fitness / records
    r"^AR 135-",       # Reserve component admin
    r"^AR 735-",       # Property accountability
    r"^DA Pam 27-",    # Military judges' benchbook
    r"^DA Pam 638-",   # Casualty procedures
    r"^DA Pam 25-",    # Correspondence
    r"^DA Pam 638-",   # Mortuary procedures
    r"^DA Pam 623-",   # Evaluation procedures
    r"^FM 3-",         # Operations
    r"^MCM",           # Manual for Courts-Martial
]
WARM_REGEX = re.compile("|".join(WARM_PATTERNS))

# COLD = everything else (DTIC, eCFR, joint pubs, deep doctrine, etc.)


def _normalize_label(label: str) -> str:
    """Canonicalize labels so the bulk-crawler's underscore-slugged filenames
    match HOT_SOURCES / WARM_PATTERNS that use space-separated forms.

    Examples:
        'AR_600-9'                          → 'AR 600-9'
        'AR_600-8-10_Leaves_and_Passes'     → 'AR 600-8-10'
        'DA_Pam_600-25_NCO_Guide'           → 'DA Pam 600-25'
        'FM_3-0_Operations'                 → 'FM 3-0'
        'AR 600-9'                          → 'AR 600-9' (unchanged)
    """
    s = label.replace("_", " ").strip()
    # Pattern: <KIND> <NUMBER>  with optional descriptive suffix afterwards.
    # Capture only KIND + NUMBER, drop the rest. Handles AR/FM/ADP/ADRP/ATP/
    # ATTP/TC/STP/TM/JP/MCM/ALARACT/Army Dir/HQDA Policy Notice/PPM/DA Memo/
    # DA Pam variants.
    m = re.match(
        r"^(?P<kind>AR|DA\s+Pam|PAM|FM|ADP|ADRP|ATP|ATTP|TC|STP|TM|TB|JP|MCM|"
        r"ALARACT|ARMY\s+DIR|HQDA(?:\s+POLICY)?(?:\s+NOTICE)?|PPM|DA\s+MEMO|"
        r"GO|AGO|SD|MISC\s*PUB)\s+(?P<num>[\dA-Za-z\-/.]+)",
        s,
        re.IGNORECASE,
    )
    if not m:
        return s
    kind_raw = m.group("kind").upper().strip()
    num = m.group("num")
    # Canonicalize KIND to match HOT_SOURCES / WARM_PATTERNS spelling.
    kind_map = {
        "AR": "AR",
        "DA PAM": "DA Pam",
        "PAM": "DA Pam",
        "FM": "FM",
        "ADP": "ADP",
        "ADRP": "ADRP",
        "ATP": "ATP",
        "ATTP": "ATTP",
        "TC": "TC",
        "STP": "STP",
        "TM": "TM",
        "TB": "TB",
        "JP": "JP",
        "MCM": "MCM",
        "ALARACT": "ALARACT",
        "ARMY DIR": "Army Dir",
        "HQDA": "HQDA Policy Notice",
        "HQDA POLICY": "HQDA Policy Notice",
        "HQDA POLICY NOTICE": "HQDA Policy Notice",
        "PPM": "PPM",
        "DA MEMO": "DA Memo",
        "GO": "GO",
        "AGO": "GO",
        "SD": "SD",
        "MISC PUB": "MCM",
    }
    return f"{kind_map.get(kind_raw, kind_raw.title())} {num}"


def _classify(source_label: str, *, filename: str | None = None) -> str:
    """Return 'hot', 'warm', or 'cold' for a given source label / filename.

    Routing rules:
      1. If filename is `DTIC_AD*.pdf` (sitemap-walked DTIC AD1 docs), it goes
         to COLD regardless — DTIC analysis reports are always tier-3 evidence.
      2. If filename is `eCFR_title_*.{xml,txt}`, also COLD — federal statute
         is reference material that complements but doesn't replace ARs.
      3. If normalized source_label is in the explicit HOT_SOURCES allowlist, HOT.
      4. If normalized source_label matches a WARM_PATTERNS regex, WARM.
      5. Default: COLD.
    """
    if filename:
        f = Path(filename).name if isinstance(filename, str) else filename.name
        if f.startswith("DTIC_AD"):
            return "cold"
        if f.startswith("eCFR_title_"):
            return "cold"
    normalized = _normalize_label(source_label)
    if normalized in HOT_SOURCES:
        return "hot"
    if WARM_REGEX.match(normalized):
        return "warm"
    return "cold"


# ---------------------------------------------------------------------------
# Partition + ingest
# ---------------------------------------------------------------------------

def partition_corpus(*, dry_run: bool = False) -> dict[str, list[Path]]:
    """Walk corpus/ root + tier subdirs, classify each PDF, return tier→[pdfs].

    Already-tiered files (those under corpus/hot/ etc.) are kept in place.
    Files in the corpus/ root are classified by source label and *moved* into
    their tier subdir on a real run, *recorded* on a dry run.
    """
    moves: dict[str, list[Path]] = {"hot": [], "warm": [], "cold": []}
    if not dry_run:
        for d in TIER_DIRS.values():
            d.mkdir(parents=True, exist_ok=True)

    # First pass: docs already in a tier subdir stay there.
    for tier, tier_dir in TIER_DIRS.items():
        for pdf in tier_dir.glob("*.pdf"):
            moves[tier].append(pdf)

    # Second pass: docs in the corpus root get classified + moved.
    for pdf in CORPUS.glob("*.pdf"):
        # Skip if it's actually inside a tier dir (glob only returns root).
        label = SOURCE_LABELS.get(pdf.name, pdf.stem)
        tier = _classify(label, filename=pdf.name)
        target = TIER_DIRS[tier] / pdf.name
        log.info(f"  {pdf.name:60s} → {tier.upper()} (label: {label!r})")
        if not dry_run:
            if not target.exists():
                shutil.move(str(pdf), str(target))
            else:
                # Already in the right place, drop the duplicate
                pdf.unlink()
            moves[tier].append(target)
        else:
            moves[tier].append(pdf)
    return moves


def build_tier_index(tier: str, pdfs: list[Path], embedder: SentenceTransformer) -> int:
    """Build a FAISS index for one tier from its list of PDFs. Returns chunk count."""
    index_dir = TIER_INDEX_DIRS[tier]
    index_dir.mkdir(parents=True, exist_ok=True)

    if not pdfs:
        log.warning(f"[{tier.upper()}] no PDFs — skipping index build")
        return 0

    log.info(f"[{tier.upper()}] ingesting {len(pdfs)} PDFs")
    all_chunks: list[dict] = []
    for pdf in pdfs:
        label = SOURCE_LABELS.get(pdf.name, pdf.stem)
        for page_num, page_text in extract_pages(pdf):
            for piece in chunk(page_text):
                all_chunks.append({
                    "text": piece,
                    "source": label,
                    "section": detect_section(piece, label),
                    "page": page_num,
                    "tier": tier.upper(),
                })

    if not all_chunks:
        log.warning(f"[{tier.upper()}] no extractable text — skipping")
        return 0

    log.info(f"[{tier.upper()}] embedding {len(all_chunks):,} chunks…")
    vecs = embedder.encode(
        [c["text"] for c in all_chunks],
        show_progress_bar=True,
        normalize_embeddings=True,
        batch_size=32,
    ).astype("float32")

    index = faiss.IndexFlatIP(vecs.shape[1])
    index.add(vecs)

    faiss.write_index(index, str(index_dir / "faiss.bin"))
    with open(index_dir / "chunks.pkl", "wb") as f:
        pickle.dump(all_chunks, f)

    log.info(
        f"[{tier.upper()}] wrote {index.ntotal:,} vectors to {index_dir} "
        f"({(index_dir / 'faiss.bin').stat().st_size / 1e6:.1f} MB)"
    )
    return len(all_chunks)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build per-tier FAISS indexes.")
    parser.add_argument(
        "--tier", choices=["hot", "warm", "cold", "all"], default="all",
        help="Which tier to (re)build. Default: all.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show the tier classification without moving files or building.",
    )
    args = parser.parse_args()

    log.info("Partitioning corpus by tier classification…")
    tiered = partition_corpus(dry_run=args.dry_run)
    for tier in ("hot", "warm", "cold"):
        log.info(f"  {tier.upper():4s}: {len(tiered[tier])} PDFs")

    if args.dry_run:
        log.info("Dry run — no indexes built. Re-run without --dry-run to build.")
        return 0

    log.info(f"Loading embedder: {EMBED_MODEL}")
    embedder = SentenceTransformer(EMBED_MODEL)

    targets = ("hot", "warm", "cold") if args.tier == "all" else (args.tier,)
    total = 0
    for tier in targets:
        total += build_tier_index(tier, tiered[tier], embedder)

    log.info("=" * 64)
    log.info(f"DONE  total chunks indexed: {total:,}")
    log.info("Run the per-tier corpus servers:")
    log.info("  python scripts/run_corpus_server.py --tier warm --port 8001")
    log.info("  python scripts/run_corpus_server.py --tier cold --port 8002")
    log.info("Then start the main server (HOT runs in-process):")
    log.info("  uvicorn adjutant.server:app --port 8000")
    return 0


if __name__ == "__main__":
    sys.exit(main())
