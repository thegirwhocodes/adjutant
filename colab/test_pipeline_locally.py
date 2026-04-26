"""End-to-end pipeline test — small sample, runs on Mac (no Colab needed).

What it tests:
  Cell 5 (discovery)  — finds AD1 PDFs above threshold
  Cell 6 (download)   — fetches a small sample
  Cell 7 (extraction) — parses PDFs to chunks
  Cell 8 (embed)      — runs MiniLM on the chunks

If all four stages succeed here, Colab will work too.
Cap = 20 PDFs so this finishes in ~2 minutes.
"""

import asyncio
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

ROOT = Path("/tmp/adjutant_pipeline_test")
ROOT.mkdir(exist_ok=True, parents=True)
PDF_DIR = ROOT / "pdfs"
PDF_DIR.mkdir(exist_ok=True)

# Match the production config but with a tiny cap
MIN_ACCESSION = 1100000   # post-2018, known-working PDF range
MAX_DOCS = 20             # small for testing
MAX_CONCURRENT = 10
RATE_LIMIT_PER_SEC = 8.0

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/xml,*/*;q=0.8",
}
DTIC_SITEMAP = "https://apps.dtic.mil/sitemap.xml"
PDF_URL = lambda acc: f"https://apps.dtic.mil/sti/pdfs/{acc}.pdf"
ACC_RE = re.compile(r"/AD1(\d+)(?:/|$)", re.IGNORECASE)
NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def stage5_discover():
    """Walk the sitemaps, return matching PDF URLs."""
    print("=" * 70)
    print("STAGE 5 — discovery")
    print("=" * 70)
    started = time.monotonic()

    r = httpx.get(DTIC_SITEMAP, headers=HEADERS, timeout=30)
    r.raise_for_status()
    root = ET.fromstring(r.text)
    sitemap_urls = [loc.text for loc in root.findall(".//sm:loc", NS)]
    ad1_sitemaps = [u for u in sitemap_urls if "AD1" in u.rsplit("/", 1)[1].upper()]

    # Pre-filter sitemaps by name so we skip ones below threshold
    def sitemap_min_acc(url):
        m = re.search(r"AD1(\d+)x\.xml$", url, re.IGNORECASE)
        return int(m.group(1) + "0000") if m else 0

    relevant = sorted(
        [u for u in ad1_sitemaps if sitemap_min_acc(u) >= MIN_ACCESSION - 10000],
        key=sitemap_min_acc,
    )
    print(f"  total AD1 sitemaps: {len(ad1_sitemaps)}")
    print(f"  with range ≥ AD1{MIN_ACCESSION-10000}: {len(relevant)}")

    accessions = []
    for i, sm in enumerate(relevant, 1):
        try:
            r2 = httpx.get(sm, headers=HEADERS, timeout=20)
            sub = ET.fromstring(r2.text)
            for loc in sub.findall(".//sm:loc", NS):
                m = ACC_RE.search(loc.text)
                if not m:
                    continue
                suffix = m.group(1)
                num = 1_000_000 + int(suffix)  # AD1XXXXXX → 1XXXXXX scale
                if num < MIN_ACCESSION:
                    continue
                accessions.append(f"AD1{suffix}")
                if len(accessions) >= MAX_DOCS:
                    break
        except Exception as e:
            print(f"  ! sitemap {sm} failed: {e}")
            continue
        if len(accessions) >= MAX_DOCS:
            break

    pdf_urls = [PDF_URL(a) for a in accessions]
    elapsed = time.monotonic() - started
    print(f"  → {len(pdf_urls)} PDF URLs in {elapsed:.1f}s")
    if not pdf_urls:
        raise RuntimeError("Stage 5 FAILED — no URLs discovered")
    print(f"  sample URL: {pdf_urls[0]}")
    print()
    return pdf_urls


async def stage6_download(pdf_urls):
    """Download PDFs concurrently."""
    print("=" * 70)
    print("STAGE 6 — download")
    print("=" * 70)
    started = time.monotonic()
    sem = asyncio.Semaphore(MAX_CONCURRENT)

    async def fetch_one(url):
        accession = url.rsplit("/", 1)[1].replace(".pdf", "")
        dest = PDF_DIR / f"{accession}.pdf"
        if dest.exists() and dest.stat().st_size > 50_000:
            return ("cached", accession, dest.stat().st_size)
        async with sem:
            try:
                async with httpx.AsyncClient(headers=HEADERS) as client:
                    r = await client.get(url, timeout=60, follow_redirects=True)
                    if r.status_code != 200:
                        return (f"http_{r.status_code}", accession, 0)
                    if not r.content[:5].startswith(b"%PDF-"):
                        return ("not_pdf", accession, 0)
                    if len(r.content) < 50_000:
                        return ("too_small", accession, len(r.content))
                    dest.write_bytes(r.content)
                    return ("ok", accession, len(r.content))
            except Exception as e:
                return (f"err_{type(e).__name__}", accession, 0)

    results = await asyncio.gather(*[fetch_one(u) for u in pdf_urls])

    from collections import Counter
    statuses = Counter(r[0] for r in results)
    elapsed = time.monotonic() - started
    print(f"  results in {elapsed:.1f}s:")
    for status, count in statuses.most_common():
        print(f"    {status:20s}  {count}")

    ok = [r for r in results if r[0] in ("ok", "cached")]
    total_bytes = sum(r[2] for r in ok)
    print(f"  → {len(ok)} PDFs on disk, {total_bytes/1024/1024:.1f} MB")
    if not ok:
        raise RuntimeError("Stage 6 FAILED — no PDFs downloaded")
    print()


def stage7_extract():
    """Extract text + chunk."""
    print("=" * 70)
    print("STAGE 7 — extract + chunk")
    print("=" * 70)
    started = time.monotonic()

    from pypdf import PdfReader

    def chunk_text(text, size=800, overlap=150):
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

    all_chunks = []
    pdfs = sorted(PDF_DIR.glob("*.pdf"))
    skipped = 0
    for pdf in pdfs:
        accession = pdf.stem
        try:
            r = PdfReader(str(pdf))
            for i, page in enumerate(r.pages, start=1):
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
                        "section": f"page {i}",
                        "page": i,
                        "tier": "COLD",
                    })
        except Exception as e:
            skipped += 1
            print(f"  ! skipped {pdf.name}: {e}")

    elapsed = time.monotonic() - started
    print(f"  → {len(all_chunks):,} chunks from {len(pdfs)} PDFs ({skipped} skipped) in {elapsed:.1f}s")
    if not all_chunks:
        raise RuntimeError("Stage 7 FAILED — no chunks extracted")
    print(f"  sample chunk: {all_chunks[0]['text'][:120]}…")
    print()
    return all_chunks


def stage8_embed(all_chunks):
    """Embed chunks with MiniLM (CPU on Mac, but tests the path)."""
    print("=" * 70)
    print("STAGE 8 — embed")
    print("=" * 70)
    started = time.monotonic()

    from sentence_transformers import SentenceTransformer
    import numpy as np

    print(f"  loading model: sentence-transformers/all-MiniLM-L6-v2")
    model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")

    texts = [c["text"] for c in all_chunks]
    print(f"  embedding {len(texts):,} chunks (CPU)…")
    vecs = model.encode(
        texts,
        show_progress_bar=False,
        normalize_embeddings=True,
        batch_size=32,
        convert_to_numpy=True,
    ).astype("float32")
    elapsed = time.monotonic() - started
    print(f"  → shape={vecs.shape}, {elapsed:.1f}s ({len(texts)/elapsed:.0f} chunks/sec on CPU)")

    # Build index briefly to verify FAISS works
    import faiss
    index = faiss.IndexFlatIP(vecs.shape[1])
    index.add(vecs)
    print(f"  FAISS index: {index.ntotal:,} vectors")

    # Sanity-check retrieval works
    q_vec = model.encode(["army personnel administrative procedures"], normalize_embeddings=True).astype("float32")
    scores, idxs = index.search(q_vec, 3)
    print(f"  test query → top score {scores[0][0]:.3f}, top chunk:")
    print(f"    {all_chunks[idxs[0][0]]['text'][:150]}…")
    print()


async def main():
    pdf_urls = stage5_discover()
    await stage6_download(pdf_urls)
    chunks = stage7_extract()
    stage8_embed(chunks)
    print("=" * 70)
    print("✅ ALL STAGES PASSED — Colab pipeline will work")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
