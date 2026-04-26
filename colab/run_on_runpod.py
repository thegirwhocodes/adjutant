"""DTIC pipeline — single-shot script for RunPod (no Colab cells).

Designed to run unattended on a RunPod A100/A40 box. After:
    pip install httpx faiss-cpu sentence-transformers pypdf
    python run_on_runpod.py

It produces /workspace/.faiss_index_cold/{faiss.bin, chunks.pkl} which you
scp back to your Mac.

Config: tune MIN_ACCESSION + MAX_DOCS at the top.

Verified working end-to-end on Apr 26 2026 — pipeline pulled real ARL Technical
Reports, chunked + embedded successfully.
"""

from __future__ import annotations

import asyncio
import logging
import pickle
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("dtic")

# ---------------------------------------------------------------------------
# Config — tune these
# ---------------------------------------------------------------------------

# Working PDF range — verified Apr 2026 by probe. AD1230000+ migrated to
# non-public storage and return 404 on /sti/pdfs/.
MIN_ACCESSION = 1100000   # post-2018
MAX_DOCS = 30000          # cap; lower for faster demo, higher for fuller corpus

# Storage on the pod
WORK_DIR = Path("/workspace/dtic_work")
PDF_DIR = WORK_DIR / "pdfs"
INDEX_OUT = Path("/workspace/.faiss_index_cold")

# Network
MAX_CONCURRENT = 50
RATE_LIMIT_PER_SEC = 8.0
TIMEOUT_S = 60.0
MIN_VALID_PDF_BYTES = 50_000

# Embedding
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
CHUNK_SIZE = 800
CHUNK_OVERLAP = 150
EMBED_BATCH = 256  # T4/A40/A100 fits this comfortably

# ---------------------------------------------------------------------------
# DTIC URL constants
# ---------------------------------------------------------------------------

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
DTIC_SITEMAP = "https://apps.dtic.mil/sitemap.xml"
PDF_URL = lambda acc: f"https://apps.dtic.mil/sti/pdfs/{acc}.pdf"
ACC_RE = re.compile(r"/AD1(\d+)(?:/|$)", re.IGNORECASE)
NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


# ---------------------------------------------------------------------------
# Stage 1 — discover
# ---------------------------------------------------------------------------

def discover() -> list[str]:
    log.info(f"Fetching master sitemap: {DTIC_SITEMAP}")
    r = httpx.get(DTIC_SITEMAP, headers=HEADERS, timeout=30)
    r.raise_for_status()
    root = ET.fromstring(r.text)
    sitemap_urls = [loc.text for loc in root.findall(".//sm:loc", NS)]

    def sitemap_min_acc(url):
        m = re.search(r"AD1(\d+)x\.xml$", url, re.IGNORECASE)
        return int(m.group(1) + "0000") if m else 0

    ad1_sitemaps = sorted(
        [u for u in sitemap_urls if "AD1" in u.rsplit("/", 1)[1].upper()
         and sitemap_min_acc(u) >= MIN_ACCESSION - 10000],
        key=sitemap_min_acc,
    )
    log.info(f"  filtered to {len(ad1_sitemaps)} sitemaps with range ≥ AD1{MIN_ACCESSION - 10000}")

    accessions: list[str] = []
    for i, sm in enumerate(ad1_sitemaps, 1):
        try:
            r2 = httpx.get(sm, headers=HEADERS, timeout=30)
            sub = ET.fromstring(r2.text)
            for loc in sub.findall(".//sm:loc", NS):
                m = ACC_RE.search(loc.text or "")
                if not m:
                    continue
                suffix = m.group(1)
                num = 1_000_000 + int(suffix)
                if num < MIN_ACCESSION:
                    continue
                accessions.append(f"AD1{suffix}")
                if len(accessions) >= MAX_DOCS:
                    break
        except Exception as e:
            log.warning(f"  sitemap {sm} failed: {e}")
            continue
        if i % 10 == 0:
            log.info(f"  walked {i}/{len(ad1_sitemaps)}; matches: {len(accessions):,}")
        if len(accessions) >= MAX_DOCS:
            log.info(f"  hit MAX_DOCS={MAX_DOCS}, stopping")
            break

    log.info(f"→ {len(accessions):,} candidate accessions")
    return [PDF_URL(acc) for acc in accessions]


# ---------------------------------------------------------------------------
# Stage 2 — async download with rate limit
# ---------------------------------------------------------------------------

class RateLimiter:
    def __init__(self, rate: float, capacity: int = 16):
        self.rate, self.capacity = rate, capacity
        self.tokens = capacity
        self.updated = time.monotonic()
        self.lock = asyncio.Lock()

    async def acquire(self):
        async with self.lock:
            now = time.monotonic()
            self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
            self.updated = now
            if self.tokens >= 1:
                self.tokens -= 1
                return
            wait = (1 - self.tokens) / self.rate
        await asyncio.sleep(wait)
        async with self.lock:
            self.tokens = max(0, self.tokens - 1)


async def download_one(client, url, sem, rl):
    accession = url.rsplit("/", 1)[1].replace(".pdf", "")
    dest = PDF_DIR / f"{accession}.pdf"
    if dest.exists() and dest.stat().st_size > MIN_VALID_PDF_BYTES:
        return ("cached", accession)
    await rl.acquire()
    async with sem:
        try:
            r = await client.get(url, timeout=TIMEOUT_S, follow_redirects=True)
            if r.status_code != 200:
                return (f"http_{r.status_code}", accession)
            if not r.content[:5].startswith(b"%PDF-"):
                return ("not_pdf", accession)
            if len(r.content) < MIN_VALID_PDF_BYTES:
                return ("too_small", accession)
            dest.write_bytes(r.content)
            return ("ok", accession)
        except Exception as e:
            return (f"err_{type(e).__name__}", accession)


async def download_all(urls):
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(MAX_CONCURRENT)
    rl = RateLimiter(RATE_LIMIT_PER_SEC)

    async with httpx.AsyncClient(headers=HEADERS) as client:
        results = {}
        batch_size = 200
        for batch_start in range(0, len(urls), batch_size):
            batch = urls[batch_start: batch_start + batch_size]
            tasks = [download_one(client, u, sem, rl) for u in batch]
            batch_results = await asyncio.gather(*tasks)
            for status, acc in batch_results:
                results[acc] = status
            ok = sum(1 for s in results.values() if s == "ok")
            cached = sum(1 for s in results.values() if s == "cached")
            log.info(f"  download batch {batch_start + len(batch):,}/{len(urls):,} — ok={ok:,} cached={cached:,}")
        return results


# ---------------------------------------------------------------------------
# Stage 3 — extract + chunk
# ---------------------------------------------------------------------------

def chunk_text(text, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
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


def extract_all() -> list[dict]:
    from pypdf import PdfReader

    pdfs = sorted(PDF_DIR.glob("*.pdf"))
    log.info(f"Chunking {len(pdfs):,} PDFs…")
    all_chunks = []
    skipped = 0
    for idx, pdf in enumerate(pdfs, 1):
        accession = pdf.stem
        try:
            r = PdfReader(str(pdf))
            for page_num, page in enumerate(r.pages, start=1):
                try:
                    t = page.extract_text() or ""
                except Exception:
                    continue
                t = re.sub(r"\s+", " ", t).strip()
                if len(t) < 50:
                    continue
                for piece in chunk_text(t):
                    all_chunks.append({
                        "text": piece,
                        "source": f"DTIC {accession}",
                        "section": f"page {page_num}",
                        "page": page_num,
                        "tier": "COLD",
                    })
        except Exception as e:
            skipped += 1
            log.debug(f"  skipped {pdf.name}: {e}")
        if idx % 1000 == 0:
            log.info(f"  chunked {idx:,}/{len(pdfs):,} — chunks so far: {len(all_chunks):,}")

    log.info(f"→ {len(all_chunks):,} chunks from {len(pdfs)} PDFs ({skipped} skipped)")
    return all_chunks


# ---------------------------------------------------------------------------
# Stage 4 — embed + build FAISS
# ---------------------------------------------------------------------------

def embed_and_index(all_chunks: list[dict]) -> None:
    import torch
    from sentence_transformers import SentenceTransformer
    import faiss

    device = "cuda" if torch.cuda.is_available() else "cpu"
    log.info(f"Device: {device} ({torch.cuda.get_device_name(0) if device == 'cuda' else 'CPU only'})")
    log.info(f"Loading model: {EMBED_MODEL}")
    model = SentenceTransformer(EMBED_MODEL, device=device)

    texts = [c["text"] for c in all_chunks]
    log.info(f"Embedding {len(texts):,} chunks…")
    started = time.monotonic()
    vecs = model.encode(
        texts,
        show_progress_bar=True,
        normalize_embeddings=True,
        batch_size=EMBED_BATCH,
        convert_to_numpy=True,
    ).astype("float32")
    elapsed = time.monotonic() - started
    log.info(f"  shape={vecs.shape}, {elapsed:.1f}s ({len(texts) / elapsed:.0f} chunks/sec)")

    index = faiss.IndexFlatIP(vecs.shape[1])
    index.add(vecs)
    log.info(f"FAISS index: {index.ntotal:,} vectors, dim {vecs.shape[1]}")

    INDEX_OUT.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(INDEX_OUT / "faiss.bin"))
    with open(INDEX_OUT / "chunks.pkl", "wb") as f:
        pickle.dump(all_chunks, f)

    faiss_mb = (INDEX_OUT / "faiss.bin").stat().st_size / (1024 * 1024)
    chunks_mb = (INDEX_OUT / "chunks.pkl").stat().st_size / (1024 * 1024)
    log.info(f"=== Saved to {INDEX_OUT} ===")
    log.info(f"  faiss.bin   {faiss_mb:,.1f} MB")
    log.info(f"  chunks.pkl  {chunks_mb:,.1f} MB")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main_async():
    started = time.monotonic()

    log.info("=" * 70)
    log.info("STAGE 1 — discovery")
    log.info("=" * 70)
    pdf_urls = discover()
    if not pdf_urls:
        log.error("No URLs discovered — bailing")
        return 1

    log.info("=" * 70)
    log.info(f"STAGE 2 — download {len(pdf_urls):,} PDFs")
    log.info("=" * 70)
    results = await download_all(pdf_urls)
    statuses = Counter(results.values())
    log.info("Download summary:")
    for status, count in statuses.most_common():
        log.info(f"  {status:25s}  {count:,}")
    n_pdfs = len(list(PDF_DIR.glob("*.pdf")))
    log.info(f"  → {n_pdfs:,} PDFs on disk")

    log.info("=" * 70)
    log.info("STAGE 3 — extract + chunk")
    log.info("=" * 70)
    chunks = extract_all()
    if not chunks:
        log.error("No chunks extracted — bailing")
        return 1

    log.info("=" * 70)
    log.info("STAGE 4 — embed + build FAISS")
    log.info("=" * 70)
    embed_and_index(chunks)

    elapsed = time.monotonic() - started
    log.info(f"\n✅ DONE in {elapsed / 60:.1f} min")
    log.info("Now scp the FAISS files back to your Mac:")
    log.info(f"  scp -P <port> -i <key> -r root@<ip>:{INDEX_OUT} ~/adjutant/")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
