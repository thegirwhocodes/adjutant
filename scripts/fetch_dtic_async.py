"""DTIC async sitemap walker — AD1 prefix, post-2015, Distribution A only.

Goal
----
Pull the modern half of DTIC's public archive offline:
- Prefix `AD1*` (2010-present; ~230K accessions, of which ~150K are post-2015)
- After keyword filter (admin/personnel/regulation/leadership/training/...)
  expect ~30–50K relevant docs
- Embedded into the COLD tier of Adjutant's tiered architecture

What this script does
---------------------
1. Fetch `https://apps.dtic.mil/sitemap.xml` — the sitemap-of-sitemaps DTIC
   maintains explicitly for crawlers indexing its public collection.
2. For each section sitemap, extract every citation URL of the form
   `https://apps.dtic.mil/sti/citations/AD1*` (skip AD0/ADA = pre-2010 vintage,
   skip ADB/ADC = limited distribution & gated).
3. For each citation, fetch the citation HTML page in an async pool, parse
   metadata (publication date, distribution statement, title), filter:
     - publication year >= MIN_YEAR (default 2015)
     - distribution statement contains "Statement A" (Distribution A)
     - title regex matches admin/personnel/regulation keywords
4. For each surviving citation, fetch the corresponding PDF from
   `https://apps.dtic.mil/sti/pdfs/AD1*.pdf`.
5. Save to `corpus/cold/` (the COLD tier directory).

Concurrency / politeness
------------------------
- 50 concurrent in-flight requests via `asyncio.Semaphore`
- Token-bucket rate limit of 8 req/s shared across all connections
  (~28K req/hour — well below DTIC's tolerance)
- Exponential backoff with jitter on 429/503
- Resumable: every URL processed gets logged to `dtic_crawl_state.jsonl`
  with status. Re-running the script skips already-processed URLs.

Wall-clock estimates
--------------------
- Sitemap walk: ~10 min (assumes ~30 section sitemaps × 50K URLs each)
- Citation page fetch + filter: ~3 hours for 230K AD1 URLs at 8 req/s
- PDF fetch for surviving ~30-50K matches: ~2 hours at 8 req/s
- TOTAL: ~5-6 hours wall-clock from cold start
- Resumable means a flaky network or a cancelled run picks up where it left off

Run
---
    pip install httpx beautifulsoup4 lxml
    python scripts/fetch_dtic_async.py                    # full crawl
    python scripts/fetch_dtic_async.py --min-year 2018    # tighter date filter
    python scripts/fetch_dtic_async.py --max-docs 5000    # cap for testing
    python scripts/fetch_dtic_async.py --resume           # continue from state file
    python scripts/fetch_dtic_async.py --dry-run          # discover URLs only
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("dtic")

ROOT = Path(__file__).resolve().parent.parent
COLD_CORPUS = ROOT / "corpus" / "cold"
COLD_CORPUS.mkdir(parents=True, exist_ok=True)
STATE_FILE = ROOT / "dtic_crawl_state.jsonl"

DTIC_SITEMAP = "https://apps.dtic.mil/sitemap.xml"
# Citation/HTML metadata page (verified Apr 2026 by direct probe):
#   https://apps.dtic.mil/sti/html/tr/AD1XXXXXX/index.html  (200 OK, ~270 KB)
# Direct PDF (verified Apr 2026):
#   https://apps.dtic.mil/sti/pdfs/AD1XXXXXX.pdf            (200 OK, application/pdf)
CITATION_BASE = "https://apps.dtic.mil/sti/html/tr/"
PDF_BASE = "https://apps.dtic.mil/sti/pdfs/"

MIN_VALID_PDF_BYTES = 50_000

# Network politeness
MAX_CONCURRENT = 50
RATE_LIMIT_PER_SEC = 8.0
RETRY_MAX = 4
TIMEOUT_S = 60.0

# DTIC's anti-bot is mild; real Chrome User-Agent is enough.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml,application/pdf;q=0.9",
    "Accept-Language": "en-US,en;q=0.9",
}

# AD1 = post-2010; AD0/ADA = pre-2010 vintage; ADB/ADC = distribution-restricted.
# DTIC sitemap URLs use the format `/sti/html/tr/AD1XXXXXX/index.html` —
# verified Apr 2026 by direct probe of sitemap-AD1000x.xml. The accession
# number is what we capture; downloads then go through PDF_BASE.
AD1_RE = re.compile(r"/sti/html/tr/(AD1\d+)/", re.IGNORECASE)

# Title-level keyword filter for Adjutant's admin/personnel/regulation focus.
RELEVANT_KEYWORDS = re.compile(
    r"(?i)\b("
    r"personnel|admin(?:istrative)?|regulation|paperwork|leave|TDY|"
    r"evaluation|NCOER|OER|UCMJ|substance abuse|property|housing|"
    r"medical readiness|deployment|safety|equal opportunity|SHARP|"
    r"training|doctrine|leadership|command|company|battalion|"
    r"brigade|division|enlisted|officer|NCO|sergeant|warrant|"
    r"DTS|IPPS|HRC|S1|G1|CDAO|GenAI|DoD|DOW|Army|Marine|Navy|Air Force|"
    r"manpower|workforce|talent|recruiting|retention|career|"
    r"retirement|separation|discharge|promotion|assignment|PCS|"
    r"counseling|recognition|award|discipline|investigation|"
    r"profession|ethic|values|conduct|complaint|grievance"
    r")\b"
)


@dataclass
class CitationMeta:
    accession: str
    title: str = ""
    publication_year: int | None = None
    distribution: str = ""
    pdf_url: str = ""
    eligible: bool = False
    reject_reason: str = ""


@dataclass
class CrawlState:
    sitemap_done: bool = False
    discovered: list[str] = field(default_factory=list)
    citation_processed: set[str] = field(default_factory=set)
    pdf_downloaded: set[str] = field(default_factory=set)
    skipped: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Rate-limited HTTP client
# ---------------------------------------------------------------------------

class RateLimiter:
    """Token-bucket rate limiter. Refills at RATE_LIMIT_PER_SEC tokens/second."""
    def __init__(self, rate: float, capacity: int = 16):
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.updated = time.monotonic()
        self.lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self.lock:
            now = time.monotonic()
            elapsed = now - self.updated
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            self.updated = now
            if self.tokens >= 1:
                self.tokens -= 1
                return
            # Sleep until next token is available
            wait = (1 - self.tokens) / self.rate
        await asyncio.sleep(wait)
        async with self.lock:
            self.tokens = max(0, self.tokens - 1)


_rate_limiter = RateLimiter(RATE_LIMIT_PER_SEC)
_sem = asyncio.Semaphore(MAX_CONCURRENT)


async def fetch(client: httpx.AsyncClient, url: str, *, expect_pdf: bool = False) -> bytes | None:
    """Polite GET with exponential backoff. Returns content bytes or None."""
    for attempt in range(RETRY_MAX):
        await _rate_limiter.acquire()
        async with _sem:
            try:
                resp = await client.get(url, follow_redirects=True, timeout=TIMEOUT_S)
                if resp.status_code == 200:
                    if expect_pdf and not resp.content[:5].startswith(b"%PDF-"):
                        log.debug(f"   not a PDF: {url}")
                        return None
                    return resp.content
                if resp.status_code in (429, 503):
                    backoff = (2 ** attempt) + random.random()
                    log.warning(f"   {resp.status_code} on {url[:80]}; backoff {backoff:.1f}s")
                    await asyncio.sleep(backoff)
                    continue
                if resp.status_code in (403, 404):
                    return None
                log.warning(f"   HTTP {resp.status_code} on {url[:80]}")
                return None
            except (httpx.TimeoutException, httpx.RequestError) as e:
                backoff = (2 ** attempt) + random.random()
                log.warning(f"   {type(e).__name__} on {url[:80]}; backoff {backoff:.1f}s")
                await asyncio.sleep(backoff)
    return None


# ---------------------------------------------------------------------------
# Sitemap walk → AD1 citation URLs
# ---------------------------------------------------------------------------

async def discover_ad1_urls(client: httpx.AsyncClient) -> list[str]:
    """Walk DTIC sitemap-of-sitemaps and harvest AD1* citation URLs."""
    log.info(f"Fetching sitemap: {DTIC_SITEMAP}")
    body = await fetch(client, DTIC_SITEMAP)
    if not body:
        log.error("Failed to fetch sitemap.xml")
        return []

    soup = BeautifulSoup(body, "xml")
    section_sitemap_urls = [loc.text.strip() for loc in soup.find_all("loc")]
    log.info(f"  found {len(section_sitemap_urls)} section sitemaps")

    all_ad1: set[str] = set()
    for i, sm_url in enumerate(section_sitemap_urls, 1):
        log.info(f"  [{i}/{len(section_sitemap_urls)}] {sm_url}")
        sm_body = await fetch(client, sm_url)
        if not sm_body:
            continue
        sm_soup = BeautifulSoup(sm_body, "xml")
        for loc in sm_soup.find_all("loc"):
            text = loc.text.strip()
            if AD1_RE.search(text):
                all_ad1.add(text)
        log.info(f"     running total AD1 URLs: {len(all_ad1):,}")

    return sorted(all_ad1)


# ---------------------------------------------------------------------------
# Citation page parsing
# ---------------------------------------------------------------------------

YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def parse_citation(html: bytes, accession: str) -> CitationMeta:
    """Extract title, year, distribution from a DTIC citation HTML page."""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    # Title — typically in <h1> or first big heading
    title = ""
    h1 = soup.find(["h1", "h2"])
    if h1:
        title = h1.get_text(strip=True)
    if not title:
        # Fallback: look for title-like span
        title_tag = soup.find(attrs={"class": re.compile(r"title", re.I)})
        if title_tag:
            title = title_tag.get_text(strip=True)

    # Distribution statement
    distribution = ""
    dist_match = re.search(
        r"Distribution\s+Statement\s+([A-F])[^\n]{0,200}",
        text,
        re.IGNORECASE,
    )
    if dist_match:
        distribution = f"Distribution Statement {dist_match.group(1).upper()}"
    elif "approved for public release" in text.lower():
        distribution = "Distribution Statement A"

    # Publication year — look for "Publication Date:" or "Date of Publication"
    pub_year: int | None = None
    pub_match = re.search(
        r"(?:Publication\s+Date|Date\s+of\s+Publication|Date)\s*[:\-]?\s*"
        r"(?:\w+\s+)?(\d{4})",
        text,
        re.IGNORECASE,
    )
    if pub_match:
        try:
            pub_year = int(pub_match.group(1))
        except ValueError:
            pass

    # Fallback: pull max plausible year out of the page
    if pub_year is None:
        years = [int(y) for y in YEAR_RE.findall(text) if 1980 <= int(y) <= 2030]
        if years:
            pub_year = max(years)

    pdf_url = f"{PDF_BASE}{accession}.pdf"

    return CitationMeta(
        accession=accession,
        title=title[:300],
        publication_year=pub_year,
        distribution=distribution,
        pdf_url=pdf_url,
    )


def is_eligible(meta: CitationMeta, *, min_year: int) -> tuple[bool, str]:
    """Return (eligible, reject_reason)."""
    if meta.publication_year is None:
        return False, "no_year"
    if meta.publication_year < min_year:
        return False, f"too_old:{meta.publication_year}"
    if "Statement A" not in meta.distribution:
        return False, f"distribution:{meta.distribution or 'unknown'}"
    if not RELEVANT_KEYWORDS.search(meta.title):
        return False, "off_topic"
    return True, ""


# ---------------------------------------------------------------------------
# State persistence (resumable crawls)
# ---------------------------------------------------------------------------

def save_event(event: dict) -> None:
    with open(STATE_FILE, "a") as f:
        f.write(json.dumps(event) + "\n")


def load_state() -> CrawlState:
    state = CrawlState()
    if not STATE_FILE.exists():
        return state
    log.info(f"Resuming from {STATE_FILE}")
    with open(STATE_FILE) as f:
        for line in f:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = ev.get("kind")
            if kind == "discovered":
                state.discovered.append(ev["url"])
            elif kind == "citation_done":
                state.citation_processed.add(ev["accession"])
            elif kind == "pdf_done":
                state.pdf_downloaded.add(ev["accession"])
            elif kind == "skip":
                reason = ev.get("reason", "unknown")
                state.skipped[reason] = state.skipped.get(reason, 0) + 1
    log.info(
        f"   resumed: {len(state.discovered):,} discovered · "
        f"{len(state.citation_processed):,} processed · "
        f"{len(state.pdf_downloaded):,} downloaded · "
        f"skipped: {sum(state.skipped.values()):,}"
    )
    return state


# ---------------------------------------------------------------------------
# Pipeline stages
# ---------------------------------------------------------------------------

async def stage_process_citation(
    client: httpx.AsyncClient, citation_url: str, *, min_year: int
) -> CitationMeta | None:
    """Fetch citation page, parse, filter. Returns meta if eligible."""
    accession_match = AD1_RE.search(citation_url)
    if not accession_match:
        return None
    accession = accession_match.group(1)

    html = await fetch(client, citation_url)
    if not html:
        save_event({"kind": "skip", "accession": accession, "reason": "fetch_failed"})
        return None

    meta = parse_citation(html, accession)
    eligible, reason = is_eligible(meta, min_year=min_year)
    meta.eligible = eligible
    meta.reject_reason = reason
    save_event({
        "kind": "citation_done",
        "accession": accession,
        "title": meta.title[:120],
        "year": meta.publication_year,
        "distribution": meta.distribution,
        "eligible": eligible,
        "reject_reason": reason,
    })
    if not eligible:
        save_event({"kind": "skip", "accession": accession, "reason": reason})
        return None
    return meta


async def stage_download_pdf(
    client: httpx.AsyncClient, meta: CitationMeta
) -> bool:
    """Download the PDF for an eligible citation."""
    dest = COLD_CORPUS / f"DTIC_{meta.accession}.pdf"
    if dest.exists() and dest.stat().st_size > MIN_VALID_PDF_BYTES:
        save_event({"kind": "pdf_done", "accession": meta.accession, "cached": True})
        return True

    body = await fetch(client, meta.pdf_url, expect_pdf=True)
    if not body or len(body) < MIN_VALID_PDF_BYTES:
        save_event({"kind": "skip", "accession": meta.accession, "reason": "pdf_missing_or_small"})
        return False

    dest.write_bytes(body)
    save_event({"kind": "pdf_done", "accession": meta.accession, "bytes": len(body)})
    return True


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

async def run(args: argparse.Namespace) -> int:
    state = load_state() if args.resume else CrawlState()

    async with httpx.AsyncClient(headers=HEADERS) as client:
        # Stage 1: discovery
        if args.resume and state.discovered:
            urls = state.discovered
            log.info(f"Resume: skipping sitemap walk (have {len(urls):,} URLs)")
        else:
            urls = await discover_ad1_urls(client)
            for u in urls:
                save_event({"kind": "discovered", "url": u})

        if args.dry_run:
            log.info(f"DRY RUN — discovered {len(urls):,} AD1 URLs; not fetching citation pages.")
            return 0

        if args.max_docs:
            urls = urls[: args.max_docs]
            log.info(f"--max-docs cap: limiting to {len(urls):,}")

        # Filter URLs we already processed
        urls = [u for u in urls if AD1_RE.search(u).group(1) not in state.citation_processed]
        log.info(f"After resume filter: {len(urls):,} citations to process")

        # Stage 2: process citation pages — accumulate eligible metas in batches
        eligible: list[CitationMeta] = []
        batch_size = 200
        for batch_start in range(0, len(urls), batch_size):
            batch = urls[batch_start : batch_start + batch_size]
            tasks = [
                stage_process_citation(client, u, min_year=args.min_year)
                for u in batch
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, CitationMeta) and r.eligible:
                    eligible.append(r)
            log.info(
                f"  citation batch {batch_start + len(batch):,}/{len(urls):,} "
                f"— eligible so far: {len(eligible):,}"
            )

        log.info(f"Stage 2 complete: {len(eligible):,} eligible citations")

        # Stage 3: download eligible PDFs (skip already-downloaded)
        eligible = [m for m in eligible if m.accession not in state.pdf_downloaded]
        log.info(f"Stage 3: downloading {len(eligible):,} new PDFs to {COLD_CORPUS}")
        downloaded = 0
        for batch_start in range(0, len(eligible), batch_size):
            batch = eligible[batch_start : batch_start + batch_size]
            tasks = [stage_download_pdf(client, m) for m in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            downloaded += sum(1 for r in results if r is True)
            log.info(
                f"  pdf batch {batch_start + len(batch):,}/{len(eligible):,} "
                f"— downloaded so far: {downloaded:,}"
            )

        # Final summary
        total_size = sum(p.stat().st_size for p in COLD_CORPUS.glob("DTIC_*.pdf"))
        n_pdfs = len(list(COLD_CORPUS.glob("DTIC_*.pdf")))
        log.info("=" * 64)
        log.info(f"DONE  AD1 corpus has {n_pdfs:,} PDFs in {COLD_CORPUS}")
        log.info(f"      total size: {total_size / 1e9:.2f} GB")
        log.info(f"      this run downloaded: {downloaded:,}")
        log.info(f"Next: python scripts/build_tier_indexes.py --tier cold")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="DTIC async sitemap crawler — AD1 prefix, post-2015, Distribution A."
    )
    parser.add_argument(
        "--min-year", type=int, default=2015,
        help="Drop docs published before this year (default: 2015).",
    )
    parser.add_argument(
        "--max-docs", type=int, default=None,
        help="Cap total citation URLs processed (for testing).",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Continue from dtic_crawl_state.jsonl. Skip already-done URLs.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Discover URLs only; don't fetch citations or PDFs.",
    )
    args = parser.parse_args()

    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
