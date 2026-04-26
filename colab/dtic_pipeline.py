# %% [markdown]
# # Adjutant DTIC Corpus Builder (Colab T4)
#
# **What this does:** Crawls DTIC's `apps.dtic.mil` for every post-2020 AD1 PDF,
# downloads them to Colab disk, chunks + embeds with `sentence-transformers/all-MiniLM-L6-v2`,
# builds a FAISS index, saves it to your Google Drive.
#
# **Time:** 4-5 hours wall clock (mostly download). Your laptop is not involved.
#
# **Cost:** $0. Free T4 GPU, free Colab disk, your existing Drive.
#
# **Output:** `~/MyDrive/adjutant_dtic/.faiss_index_cold/{faiss.bin, chunks.pkl}`
# which you'll copy back to `~/adjutant/.faiss_index_cold/` on your Mac.

# %% [markdown]
# ## Cell 1 — Install dependencies (~1 min)

# %%
!pip install -q httpx faiss-cpu sentence-transformers pypdf

# %% [markdown]
# ## Cell 2 — Mount Google Drive
# A one-click consent dialog will appear. Approve, then this cell finishes.

# %%
from google.colab import drive
drive.mount('/content/drive')

# %% [markdown]
# ## Cell 3 — Verify GPU is allocated
# If "Tesla T4" doesn't appear, go: Runtime → Change runtime type → T4 GPU → Save → Re-run cells.

# %%
!nvidia-smi --query-gpu=name,memory.total --format=csv

# %% [markdown]
# ## Cell 4 — Configuration
#
# `MIN_ACCESSION` controls the cutoff. AD1200000 ≈ 2020+ records.
# Tune lower (more docs, more disk) or higher (fewer, faster).

# %%
import os
from pathlib import Path

# Storage
WORK_DIR = Path("/content/dtic_work")
WORK_DIR.mkdir(exist_ok=True, parents=True)
PDF_DIR = WORK_DIR / "pdfs"
PDF_DIR.mkdir(exist_ok=True)

DRIVE_OUT = Path("/content/drive/MyDrive/adjutant_dtic")
DRIVE_OUT.mkdir(exist_ok=True, parents=True)
INDEX_OUT = DRIVE_OUT / ".faiss_index_cold"
INDEX_OUT.mkdir(exist_ok=True)

# Filter: which AD1 accession range to crawl. Higher = fewer docs.
# IMPORTANT: Newer accessions (AD1230000+) have migrated to a non-public PDF
# storage path and return 404 on /sti/pdfs/ — verified by probe Apr 2026.
# The reliably-PDF-fetchable range is AD1000000 — AD1180000 (~2010-2024).
# Default tuned to that working range:
#   AD1100000 ≈ 2018+   (~70K records, all PDF-fetchable) ← recommended
#   AD1150000 ≈ 2022+   (~30K records, all PDF-fetchable)
#   AD1170000 ≈ 2024+   (~10K records, fetchable)
MIN_ACCESSION = 1100000   # post-2018, known-working PDF range
MAX_DOCS = 30000          # safety cap; reduce for testing

# Concurrency
MAX_CONCURRENT = 50
RATE_LIMIT_PER_SEC = 8.0  # be polite — DTIC will throttle past this

# Embedding
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
CHUNK_SIZE = 800
CHUNK_OVERLAP = 150
EMBED_BATCH = 256  # T4 fits this comfortably

print(f"PDFs land in:    {PDF_DIR}")
print(f"FAISS saves to:  {INDEX_OUT}")
print(f"Filter:          AD1{MIN_ACCESSION}+ (cap {MAX_DOCS} docs)")

# %% [markdown]
# ## Cell 5 — Discover candidate AD1 PDFs from sitemap
# Walks DTIC's master sitemap, picks AD1 sitemaps, harvests URLs above the threshold.

# %%
import asyncio
import re
import xml.etree.ElementTree as ET
import httpx
import time

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/xml,*/*;q=0.8",
}

DTIC_SITEMAP = "https://apps.dtic.mil/sitemap.xml"
PDF_URL = lambda acc: f"https://apps.dtic.mil/sti/pdfs/{acc}.pdf"
# DTIC has migrated URL formats twice. Sitemap entries use one of three:
#   /sti/html/tr/AD1234567/index.html       (oldest, ~2015-2018)
#   /sti/html/trecms/AD1234567/index.html   (middle, ~2018-2024)
#   /sti/dtictr/citation/AD1234567          (newest, 2024-2025)
# Permissive pattern catches all three by anchoring on /AD1XXXXX.
ACC_RE = re.compile(r"/AD1(\d+)(?:/|$)", re.IGNORECASE)
NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def discover_ad1_pdfs(min_accession: int, max_docs: int) -> list[str]:
    """Returns a list of PDF URLs to download (already filtered by accession #)."""
    print(f"Fetching master sitemap: {DTIC_SITEMAP}")
    r = httpx.get(DTIC_SITEMAP, headers=HEADERS, timeout=30)
    r.raise_for_status()
    root = ET.fromstring(r.text)

    # Get sitemap URLs whose name starts with AD1
    sitemap_urls = [loc.text for loc in root.findall(".//sm:loc", NS)]
    ad1_sitemaps = [u for u in sitemap_urls if "AD1" in u.rsplit("/", 1)[1].upper()]
    print(f"  found {len(ad1_sitemaps)} AD1 sitemaps")

    accessions: list[str] = []
    for i, sm in enumerate(ad1_sitemaps, 1):
        try:
            r2 = httpx.get(sm, headers=HEADERS, timeout=30)
            sub = ET.fromstring(r2.text)
            for loc in sub.findall(".//sm:loc", NS):
                m = ACC_RE.search(loc.text)
                if not m:
                    continue
                # m.group(1) is just the 6-digit suffix after "AD1" (e.g. "300005").
                # Add the 1_000_000 prefix that AD1 implicitly represents so the
                # comparison against MIN_ACCESSION (a 7-digit integer like
                # 1_300_000) is on the same scale.
                suffix = m.group(1)
                num = 1_000_000 + int(suffix)
                if num < min_accession:
                    continue
                accessions.append(f"AD1{suffix}")
                if len(accessions) >= max_docs:
                    break
        except Exception as e:
            print(f"  ! sitemap {sm} failed: {e}")
            continue
        if i % 25 == 0:
            print(f"  walked {i}/{len(ad1_sitemaps)} sitemaps; matches so far: {len(accessions):,}")
        if len(accessions) >= max_docs:
            print(f"  hit MAX_DOCS={max_docs}, stopping")
            break

    pdf_urls = [PDF_URL(acc) for acc in accessions]
    print(f"\n→ {len(pdf_urls):,} candidate PDF URLs")
    return pdf_urls


pdf_urls = discover_ad1_pdfs(MIN_ACCESSION, MAX_DOCS)

# %% [markdown]
# ## Cell 6 — Async PDF download with rate limit
# 50 concurrent connections, 8 req/s polite cap. Resumable: skips files already on disk.

# %%
import asyncio
import httpx
from pathlib import Path

class RateLimiter:
    def __init__(self, rate: float, capacity: int = 16):
        self.rate = rate; self.capacity = capacity
        self.tokens = capacity; self.updated = time.monotonic()
        self.lock = asyncio.Lock()
    async def acquire(self):
        async with self.lock:
            now = time.monotonic()
            self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
            self.updated = now
            if self.tokens >= 1:
                self.tokens -= 1; return
            wait = (1 - self.tokens) / self.rate
        await asyncio.sleep(wait)
        async with self.lock: self.tokens = max(0, self.tokens - 1)


async def download_one(client, url, sem, rl, dest_dir):
    accession = url.rsplit("/", 1)[1].replace(".pdf", "")
    dest = dest_dir / f"{accession}.pdf"
    if dest.exists() and dest.stat().st_size > 50_000:
        return ("cached", accession)
    await rl.acquire()
    async with sem:
        try:
            r = await client.get(url, timeout=60, follow_redirects=True)
            if r.status_code != 200:
                return (f"http_{r.status_code}", accession)
            if not r.content[:5].startswith(b"%PDF-"):
                return ("not_pdf", accession)
            if len(r.content) < 50_000:
                return ("too_small", accession)
            dest.write_bytes(r.content)
            return ("ok", accession)
        except Exception as e:
            return (f"err_{type(e).__name__}", accession)


async def download_all(urls, dest_dir):
    sem = asyncio.Semaphore(MAX_CONCURRENT)
    rl = RateLimiter(RATE_LIMIT_PER_SEC)
    async with httpx.AsyncClient(headers=HEADERS) as client:
        results = {}
        # Process in batches of 200 so we get progress updates
        batch_size = 200
        total = len(urls)
        for batch_start in range(0, total, batch_size):
            batch = urls[batch_start:batch_start + batch_size]
            tasks = [download_one(client, u, sem, rl, dest_dir) for u in batch]
            batch_results = await asyncio.gather(*tasks)
            for status, acc in batch_results:
                results[acc] = status
            ok = sum(1 for s in results.values() if s == "ok")
            cached = sum(1 for s in results.values() if s == "cached")
            print(f"  batch {batch_start + len(batch):,}/{total:,} — ok={ok:,} cached={cached:,}")
        return results


print(f"Downloading {len(pdf_urls):,} PDFs to {PDF_DIR}…")
print("(this is the long step — go get coffee)")
results = await download_all(pdf_urls, PDF_DIR)

# Summary
from collections import Counter
status_counts = Counter(results.values())
print(f"\nDownload summary:")
for status, count in status_counts.most_common():
    print(f"  {status:25s}  {count:,}")

n_pdfs = len(list(PDF_DIR.glob("*.pdf")))
total_mb = sum(p.stat().st_size for p in PDF_DIR.glob("*.pdf")) / (1024 * 1024)
print(f"\nDisk: {n_pdfs:,} PDFs, {total_mb:,.0f} MB on Colab")

# %% [markdown]
# ## Cell 7 — Extract text + chunk
# Walks every PDF, splits into 800-char chunks with 150-char overlap.
# Skips PDFs that are scanned images (no extractable text).

# %%
import re
import pickle
from pypdf import PdfReader
from tqdm import tqdm

def chunk_text(text, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    """Sliding window with sentence-boundary preference."""
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
        if end >= len(text): break
        i = max(end - overlap, i + 1)
    return [c for c in chunks if len(c) >= 50]


def extract_pages(pdf_path):
    """Returns list of (page_num, text) for pages with ≥50 chars."""
    out = []
    try:
        r = PdfReader(str(pdf_path))
        for i, page in enumerate(r.pages, start=1):
            try:
                t = page.extract_text() or ""
            except Exception:
                continue
            t = re.sub(r"\s+", " ", t).strip()
            if len(t) < 50: continue
            out.append((i, t))
    except Exception as e:
        return []
    return out


all_chunks = []
pdfs = sorted(PDF_DIR.glob("*.pdf"))
print(f"Chunking {len(pdfs):,} PDFs…")
for pdf in tqdm(pdfs):
    accession = pdf.stem  # e.g. AD1234567
    pages = extract_pages(pdf)
    for page_num, page_text in pages:
        for piece in chunk_text(page_text):
            all_chunks.append({
                "text": piece,
                "source": f"DTIC {accession}",
                "section": f"page {page_num}",
                "page": page_num,
                "tier": "COLD",
            })

print(f"\nTotal chunks: {len(all_chunks):,}")

# %% [markdown]
# ## Cell 8 — Embed on T4 GPU + build FAISS index
# This is where the GPU pays off — ~3,000 chunks/sec on T4 vs ~30/sec on M1 CPU.

# %%
import torch
from sentence_transformers import SentenceTransformer
import numpy as np
import faiss

print(f"CUDA available: {torch.cuda.is_available()}")
print(f"Device: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU'}")

model = SentenceTransformer(EMBED_MODEL, device="cuda" if torch.cuda.is_available() else "cpu")

texts = [c["text"] for c in all_chunks]
print(f"\nEmbedding {len(texts):,} chunks…")
vecs = model.encode(
    texts,
    show_progress_bar=True,
    normalize_embeddings=True,
    batch_size=EMBED_BATCH,
    convert_to_numpy=True,
).astype("float32")

print(f"\nEmbedding shape: {vecs.shape}")

# Build FAISS index (cosine sim via inner product on L2-normalized vectors)
index = faiss.IndexFlatIP(vecs.shape[1])
index.add(vecs)
print(f"FAISS index: {index.ntotal:,} vectors, dim {vecs.shape[1]}")

# %% [markdown]
# ## Cell 9 — Save to Google Drive
# `faiss.bin` and `chunks.pkl` go to your Drive. After this completes, mount your Drive on Mac and copy them.

# %%
faiss_path = INDEX_OUT / "faiss.bin"
chunks_path = INDEX_OUT / "chunks.pkl"

faiss.write_index(index, str(faiss_path))
with open(chunks_path, "wb") as f:
    pickle.dump(all_chunks, f)

faiss_mb = faiss_path.stat().st_size / (1024 * 1024)
chunks_mb = chunks_path.stat().st_size / (1024 * 1024)

print(f"\n=== Saved to Drive ===")
print(f"  {faiss_path}    {faiss_mb:,.1f} MB")
print(f"  {chunks_path}    {chunks_mb:,.1f} MB")
print()
print("Done. To use locally:")
print("  1. On your Mac, the files are at ~/Library/CloudStorage/")
print("     <YourDriveName>/MyDrive/adjutant_dtic/.faiss_index_cold/")
print("  2. Copy them to ~/adjutant/.faiss_index_cold/")
print("  3. Restart your COLD tier server")
