"""Build the FAISS index from corpus/*.pdf. Run once after downloading the corpus."""

import logging
import pickle
import re
import sys
from pathlib import Path

import faiss
import numpy as np
from pypdf import PdfReader
from sentence_transformers import SentenceTransformer

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ingest")

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "corpus"
INDEX = ROOT / ".faiss_index"
INDEX.mkdir(exist_ok=True)

# Tunables — fine for a 30-hour hack.
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
CHUNK_SIZE = 800       # characters
CHUNK_OVERLAP = 150

# Map PDF filename → human-readable source label for citations.
SOURCE_LABELS = {
    "AR_600-8-10_Leaves_and_Passes.pdf": "AR 600-8-10",
    "JTR_2025-06.pdf": "Joint Travel Regulations",
    "AR_623-3.pdf": "AR 623-3",
    "FM_6-22.pdf": "FM 6-22",
}


def extract_pages(pdf_path: Path) -> list[tuple[int, str]]:
    """Return [(page_number, text)] for each page. Drops pages with <50 chars."""
    out = []
    reader = PdfReader(str(pdf_path))
    for i, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as e:
            log.warning(f"{pdf_path.name} page {i}: {e}")
            continue
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) < 50:
            continue
        out.append((i, text))
    return out


def chunk(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Sliding window over a string. Splits on sentence boundaries when possible."""
    if len(text) <= size:
        return [text]
    chunks, i = [], 0
    while i < len(text):
        end = min(i + size, len(text))
        if end < len(text):
            cut = text.rfind(". ", i, end)
            if cut != -1 and cut > i + size // 2:
                end = cut + 1
        chunks.append(text[i:end].strip())
        if end >= len(text):
            break
        i = max(end - overlap, i + 1)
    return [c for c in chunks if len(c) >= 50]


def detect_section(text: str, source: str) -> str:
    """Cheap section labeler — looks for 'Paragraph N-N' / 'Chapter N' / '§ N'."""
    patterns = [
        r"(Paragraph\s+\d+[\-\.]\d+)",
        r"(Chapter\s+\d+)",
        r"(§\s*\d[\d\-\.]*)",
        r"(Section\s+\d+[\-\.]\d+)",
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            return m.group(1)
    return ""


def main() -> int:
    pdfs = sorted(CORPUS.glob("*.pdf"))
    if not pdfs:
        log.error(f"No PDFs in {CORPUS}. Run: python scripts/download_corpus.py")
        return 1

    log.info(f"Loading embedder: {EMBED_MODEL}")
    embedder = SentenceTransformer(EMBED_MODEL)

    all_chunks: list[dict] = []
    for pdf in pdfs:
        label = SOURCE_LABELS.get(pdf.name, pdf.stem)
        log.info(f"Reading {pdf.name} ({label})")
        for page_num, page_text in extract_pages(pdf):
            for piece in chunk(page_text):
                all_chunks.append({
                    "text": piece,
                    "source": label,
                    "section": detect_section(piece, label),
                    "page": page_num,
                })

    if not all_chunks:
        log.error("No extractable text. Check the PDFs aren't image-only scans.")
        return 1

    log.info(f"Embedding {len(all_chunks)} chunks…")
    vecs = embedder.encode(
        [c["text"] for c in all_chunks],
        show_progress_bar=True,
        normalize_embeddings=True,
        batch_size=32,
    ).astype("float32")

    # Cosine similarity via inner product on L2-normalized vectors.
    index = faiss.IndexFlatIP(vecs.shape[1])
    index.add(vecs)

    faiss.write_index(index, str(INDEX / "faiss.bin"))
    with open(INDEX / "chunks.pkl", "wb") as f:
        pickle.dump(all_chunks, f)

    log.info(f"✔ {INDEX}/faiss.bin ({index.ntotal} vectors)")
    log.info(f"✔ {INDEX}/chunks.pkl ({len(all_chunks)} chunks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
