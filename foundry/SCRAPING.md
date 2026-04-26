# Adjutant — Saturating Corpus Plan

**Goal:** ~500 current-revision documents indexed in FAISS, ~500K chunks, ~1.5 GB on disk. The "we ingested everything that matters" answer to any judge probe.

**Target wall-clock:** 8–12 hours including ingest. Most of that is parallel downloads + a one-time embedder pass.

**The catch:** at ~200K chunks the bare 384-dim MiniLM-L6 embedder starts losing precision — top-K returns near-duplicate noise instead of distinct relevant chunks. The fix is a **cross-encoder reranker** on top of the existing retriever. Adding it is ~30 lines of code and ~1 hour of work. Section 4 covers it.

---

## 1. Three sources, three fetch strategies

| Source | What it has | Fetch strategy | Auth | Tier |
|---|---|---|---|---|
| **Army Publishing Directorate** (`armypubs.army.mil`) | ~700 ARs + ~400 DA Pams + ~600 ADP/FM + ~250 TC + ~2,500 DA Forms = 4,000+ docs | Playwright (anti-bots non-browser clients with 1,226-byte HTML error page) | None | 1 |
| **DTIC** (`apps.dtic.mil`, `discover.dtic.mil`) | ~1.5M unclassified DoD technical reports, RAND/TRADOC/CSIS analyses | Sitemap-driven crawl + accession-number URL pattern | None for public/unlimited records | 2 |
| **eCFR** (`api.ecfr.gov`) | All federal regulations as structured XML; Title 32 = National Defense, Title 48 = FAR | REST/XML API, well-documented | None | 2 |

---

## 2. Army Publishing Directorate — the rulebook layer

This is where every AR, DA Pam, FM, ADP, and TC lives. **The single most important source for Adjutant** because every regulation Adjutant cites by section/paragraph traces back here.

### 2.1 Anti-bot obstacle

Direct `requests.get()` to a `armypubs.army.mil/epubs/DR_pubs/...` URL returns a **1,226-byte HTML error page** instead of a PDF. APD checks `User-Agent`, `Sec-Fetch-Dest`, `Sec-Fetch-Mode`, `Sec-Fetch-Site` headers, and a session cookie set on the parent page visit. A bare HTTP client doesn't satisfy all four.

**Solution:** Playwright (real headless Chromium). Already wired in [scripts/bulk_crawl_apd.py](/Users/naomiivie/adjutant/scripts/bulk_crawl_apd.py) `playwright_fetch()`. Falls back automatically when direct HTTP returns <50KB.

### 2.2 The Active index — the discovery surface

```
https://armypubs.army.mil/ProductMaps/PubForm/Active.aspx
```

This page lists **only currently in-force publications**. Crucial — APD also keeps superseded versions, and mixing them creates contradictory chunks (2014 AR 600-8-10 saying X, 2020 AR 600-8-10 saying Y, both ranking high in retrieval). The Active index is the only path that gives us a clean current-revision corpus.

ASP.NET WebForms with viewstate-based pagination — Playwright clicks the "Next page" link to advance. ~30 publications per page, ~50 pages → ~1,500 in-force pubs total (down from APD's ~4,000 because superseded versions and forms get filtered).

### 2.3 Direct PDF URL pattern (when known)

```
https://armypubs.army.mil/epubs/DR_pubs/DR_a/<doc-id>-<short-title>-000-WEB-1.pdf
```

Where `<doc-id>` is APD's internal asset id (e.g., `ARN30018` for AR 600-8-10). The Active.aspx detail page contains the canonical link; we resolve it once via Playwright then download all pages from the same session.

### 2.4 Per-base mirrors (the fast lane)

Many ARs are mirrored at `home.army.mil/<base>/...` — Fort Riley, Fort Bragg, Fort Hood, Fort Drum, Schofield, Hawaii, etc. Those mirrors **don't anti-bot**. When we already know the document we want, we can skip Playwright entirely and `requests.get()` directly. Downside: they sometimes serve older revisions. Trade-off: speed for currency.

This is how 11 of our 12 demo-corpus PDFs got pulled.

### 2.5 Highest-priority APD docs (Tier 1.5 — the ARs Adjutant must have)

In addition to the 12 already indexed:

**Personnel core (the bureaucratic-tail rulebook):**
- AR 350-1 — Army Training and Leader Development
- AR 614-100 — Officer Assignments, Details, and Transfers
- AR 614-200 — Enlisted Assignments and Utilization Management
- AR 600-8-105 — Military Orders
- AR 600-20 — Army Command Policy
- AR 600-100 — Army Profession and Leadership Policy
- AR 690-700 — Personnel Relations and Services (Civilian)
- AR 690-950 — Career Management (Civilian)
- AR 690-12 — Equal Employment Opportunity
- AR 600-43 — Conscientious Objection
- AR 135-178 — Enlisted Administrative Separations
- AR 135-91 — Service Obligations, Methods of Fulfillment, Participation Requirements
- AR 600-8-2 — Suspension of Favorable Personnel Actions (Flag)
- AR 600-8-19 — Enlisted Promotions and Reductions
- AR 600-8-24 — Officer Transfers and Discharges
- AR 600-8-105 — Military Orders

**Investigations / discipline / legal (the AR 27-10 companions):**
- AR 15-6 — Procedures for Administrative Investigations and Boards of Officers
- AR 27-3 — The Army Legal Assistance Program
- AR 27-26 — Rules of Professional Conduct for Lawyers
- AR 600-8-2 — Flags
- AR 638-2 — Army Mortuary Affairs Program

**Health / readiness:**
- AR 40-501 — Standards of Medical Fitness
- AR 40-66 — Medical Record Administration and Health Care Documentation
- AR 600-9 — Army Body Composition Program (already have)
- AR 600-85 — Army Substance Abuse Program (already have)

**Safety / security / records:**
- AR 380-5 — Army Information Security Program
- AR 380-67 — Personnel Security Program
- AR 385-10 — Army Safety Program
- AR 25-1 — Army Information Technology
- AR 25-22 — The Army Privacy and Civil Liberties Program
- AR 25-50 — Preparing and Managing Correspondence
- AR 25-400-2 — Army Records Information Management System

**Awards / chaplains / family / MWR:**
- AR 600-8-22 — Military Awards (already have)
- AR 165-1 — Army Chaplain Corps Activities
- AR 215-1 — Military Morale, Welfare, and Recreation Programs
- AR 608-99 — Family Support, Child Custody, and Paternity

**Plus the DA Pamphlets** that explain the ARs:
- DA Pam 600-3 — Officer Professional Development and Career Management
- DA Pam 600-25 — U.S. Army Noncommissioned Officer Professional Development Guide
- DA Pam 600-67 — Effective Writing for Army Leaders
- DA Pam 27-9 — Military Judges' Benchbook
- DA Pam 600-8-22 — Awards Procedures
- DA Pam 638-2 — Procedures for the Care and Disposition of Remains
- DA Pam 25-50 — Correspondence Preparation Aids

**Plus the doctrinal manuals** (FM/ADP) that frame the regulations:
- ADP 1 — The Army
- ADP 6-22 — Army Leadership and the Profession
- FM 7-0 — Training
- FM 7-22 — Holistic Health and Fitness
- FM 6-22 — Leader Development (already have)
- FM 3-0 — Operations (already downloaded, needs ingest)

**Plus DA Forms** as schemas (these are what we fill, not what we cite):
- DA-31 ✓, DA-4856 ✓, DD-1351-2 ✓ (already have)
- DA-2823 (Sworn Statement), DA-3349 (Physical Profile), DA-705 (ACFT), DA-5500 (Body Comp), DA-2062 (Hand Receipt — property), DA-1059 (Service School Academic Eval), DA-67-9 (OER)

---

## 3. DTIC — the analysis + lessons-learned layer

DTIC is the DoD's master research archive. ~1.5M docs. **What it gives Adjutant that APD doesn't:** the *why* behind regulations — RAND studies, TRADOC lessons-learned, CSIS analyses, GAO audits, DoD IG investigations. When a judge asks *"why is this AR worded this way?"*, DTIC has the answer.

### 3.1 URL patterns (verified)

```
# Citation page (HTML metadata)
https://apps.dtic.mil/sti/citations/AD<ACCESSION>

# Full-text PDF (when "Full Text Link" is present on the citation)
https://apps.dtic.mil/sti/pdfs/AD<ACCESSION>.pdf

# Same content, alternate route used by some legacy crawlers:
https://apps.dtic.mil/sti/tr/pdf/AD<ACCESSION>.pdf
```

Accession number prefixes:
- `AD0` / `ADA` — older reports (pre-2000)
- `AD1` — newer (2010-present)
- `ADB` — Distribution B (limited; not all public)
- `ADC` — Distribution C (limited)
- `ADP` — proceedings (often public)

For Adjutant, **only `AD0`/`ADA`/`AD1`/`ADP` records that show "Distribution Statement A: Approved for public release"** are downloadable without a CAC. That's still ~1M docs.

### 3.2 The bulk-discovery path

```
https://apps.dtic.mil/sitemap.xml
```

DTIC publishes a **public sitemap** specifically *"for indexing unclassified and unlimited reports"* (their words). The sitemap.xml is itself a sitemap-of-sitemaps — each entry points to a section sitemap with up to 50,000 URLs. Adjutant's bulk path:

1. Fetch `sitemap.xml` → list of section sitemap URLs
2. For each section sitemap → list of `apps.dtic.mil/sti/citations/AD*` URLs
3. For each citation → check it's Distribution A → derive `apps.dtic.mil/sti/pdfs/AD*.pdf` → download

We don't need an API key. We do need to be polite (~1 req/sec).

### 3.3 Most important DTIC documents for Adjutant

DTIC isn't where forms live, so we don't need exhaustive coverage. But these specific report categories *substantially strengthen* the regulation-Q&A side:

**RAND Arroyo Center** (Army's federally funded research center inside RAND) — owns the *"why is the Army the way it is"* literature. Most useful subjects:
- *"Reducing Administrative Burden for Army Company Leaders"* (multiple versions)
- *"Army Talent Management"* series
- *"Command Climate Survey"* analyses
- *"NCOER and OER"* validity research
- *"Personnel Tempo (PERSTEMPO)"* studies
- *"Army Career Tracker"* design rationales

**TRADOC** (Training and Doctrine Command) lessons-learned PDFs:
- CALL Handbooks (Center for Army Lessons Learned) — operationalizes doctrine
- AAR (After Action Review) consolidated reports

**GAO / DoD IG / CRS reports** on systems Adjutant interfaces with:
- "Defense Travel System" GAO reports — explains DTS rejection rates
- "IPPS-A" GAO and CRS reports — explains the multi-year-delayed rollout
- "GCSS-Army" property accountability audits

**Joint Publications** (already in Tier 1 — JCS portal serves these directly, faster than DTIC):
- JP 1-0 Personnel Support, JP 3-0 Operations, JP 5-0 Planning, JP 1 Doctrine

### 3.4 What we won't pull from DTIC

- Distribution B/C/D/E/F (not public)
- Anything older than ~1990 (regulations have been rewritten since)
- Wargame transcripts (not relevant to admin paperwork)
- Foreign-language reports
- Proceedings (ADP*) unless the topic is directly about admin or regulation reform

Filter heuristic in code: title regex match against `r"(?i)\b(personnel|admin|regulation|paperwork|leave|TDY|evaluation|NCOER|OER|UCMJ|substance abuse|property)\b"` plus `Distribution Statement A` requirement.

---

## 4. eCFR — the statutory binding layer

This is the **legal-binding** layer above Army regulation. Title 32 = National Defense (everything statutorily binding on DoD). Title 48 = Federal Acquisition Regulation (contracting officers).

Win line for the pitch: *"Adjutant grounds on actual federal law — CFR Title 32 — not just Army guidance documents. When AR 600-8-10 cites '10 U.S.C. § 701 governing leave entitlement,' Adjutant retrieves the underlying statute too, in addition to the Army's interpretation of it."*

### 4.1 API endpoints (verified)

Base: `https://www.ecfr.gov/api/`. No auth, no rate limits documented (be polite — ~5 req/s).

```
# Title metadata (lists all 50 titles + dates)
GET /versioner/v1/titles.json

# Full title as XML at a specific date
GET /versioner/v1/full/<YYYY-MM-DD>/title-<N>.xml
  Example: /versioner/v1/full/2026-04-01/title-32.xml
  Returns: ~50-100 MB XML for Title 32

# Title structure (table of contents only)
GET /versioner/v1/structure/<YYYY-MM-DD>/title-<N>.json

# Search across all titles
GET /search/v1/results
  ?query=<text>&title=<N>&date=<YYYY-MM-DD>

# Daily corrections / amendments
GET /admin/v1/corrections.json?title=<N>

# Agency metadata
GET /admin/v1/agencies.json
```

GitHub bulk mirror (full Title 32 history, daily snapshots): `github.com/AlextheYounga/ecfr` and `github.com/sam-berry/ecfr-analyzer` examples.

### 4.2 What to ingest

**Title 32 in full** (~50-100 MB XML). Convert XML to plaintext per CFR section, treat each section as a chunkable unit. Title 32 is ~1,000 sections → ~3,000-5,000 chunks at our 800-char target.

**Title 48 selectively** — only Subchapter J (Acquisition by DoD) and Subchapter F (Contract Management). Full Title 48 is a beast (~200 MB) and most of it isn't relevant to Adjutant's persona.

**Title 5 selectively** — § 6304 (Annual leave accumulation) and §§ 6321–6329 (Sick leave). These are the federal-employee leave statutes that AR 600-8-10 implements.

**Title 10** — the entire Armed Forces title is statutory (USC, not CFR), so we'd grab it from a different source (govinfo.gov/content/pkg/USCODE-2024-title10) — this is *the* statute Adjutant ultimately grounds on.

---

## 5. The reranker — what unblocks the saturating tier

At ~50K chunks (12 docs / current state), bare MiniLM bi-encoder retrieval works fine. **At ~500K chunks (full Tier 1 + DTIC + eCFR), it doesn't.** Top-K starts returning near-duplicate noise — the embedding space saturates.

The fix is a **two-stage retriever**:

```
Stage 1 (recall):  bi-encoder (MiniLM-L6) → FAISS top-50 candidates
Stage 2 (precision): cross-encoder (ms-marco-MiniLM-L6-v2) → re-score → top-5
```

Cross-encoders are fundamentally different: they take `(query, candidate)` *together* and run full attention over the concatenation. Vastly more accurate but O(N) at query time — that's why we only run it on the 50 candidates the bi-encoder surfaces, not the full 500K.

**Performance** ([sentence-transformers benchmarks](https://www.sbert.net/docs/pretrained-models/ce-msmarco.html)):
- `cross-encoder/ms-marco-MiniLM-L-6-v2` — ~12 ms for 1 candidate, 60 ms for 10, 740 ms for 100. Runs CPU-only on M1.
- 50 candidates per query → ~300 ms reranker latency. Adds to LLM latency we already have. Fine for 5-min demo.

**Code change** (~30 lines):

```python
# adjutant/rag.py — additions
from sentence_transformers import CrossEncoder

_reranker: CrossEncoder | None = None

def _load_reranker() -> CrossEncoder:
    global _reranker
    if _reranker is None:
        log.info("Loading cross-encoder reranker (ms-marco-MiniLM-L-6-v2)")
        _reranker = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
    return _reranker


def retrieve(query: str, top_k: int = 5, candidates: int = 50) -> list[dict]:
    """Two-stage: bi-encoder narrows to N candidates, cross-encoder ranks top_k."""
    _load()
    if _index is None or not _chunks:
        return []

    qv = _embedder.encode([query], normalize_embeddings=True).astype("float32")
    scores, idxs = _index.search(qv, candidates)

    pool = []
    for score, i in zip(scores[0], idxs[0]):
        if 0 <= i < len(_chunks):
            pool.append((float(score), dict(_chunks[i])))

    if len(pool) <= top_k:
        return [c for _, c in pool]

    reranker = _load_reranker()
    pairs = [(query, c["text"]) for _, c in pool]
    rerank_scores = reranker.predict(pairs)

    ranked = sorted(zip(rerank_scores, pool), key=lambda x: -float(x[0]))
    out = []
    threshold = float(os.getenv("RAG_RERANK_THRESHOLD", "0.0"))
    for rs, (_, c) in ranked[:top_k]:
        if float(rs) < threshold:
            continue
        c["score"] = float(rs)
        out.append(c)
    return out
```

The `RAG_SCORE_THRESHOLD` (currently 0.35 on bi-encoder cosine) becomes a *recall floor*; the reranker score becomes the precision gate.

---

## 6. The execution plan (ordered)

### Phase A — corpus expansion (parallel)

```bash
# Already in: 12 ARs/Pams/FMs (~76 MB, 16K chunks indexed)
# 3 PDFs downloaded but un-indexed: FM 3-0, DA Pam 600-25 CMF, DA Pam 623-3

# Add Tier 1 + Tier 1.5 (the ~50 ARs/Pams/FMs above)
python scripts/bulk_crawl_apd.py --tier 1   # ~30 min, ~30 docs

# Add eCFR Title 32 (build new ingest path)
python scripts/fetch_ecfr.py --title 32 --date 2026-04-01

# Add DTIC sitemap-driven crawl, filtered to admin/personnel/regulation
python scripts/fetch_dtic.py --filter "personnel|admin|leave|tdy|evaluation"
```

`scripts/fetch_ecfr.py` and `scripts/fetch_dtic.py` are new — **they don't exist yet**. They need to be written. Each is ~150 lines: requests-based, polite rate limit, dest = `corpus/`, output is plaintext `.txt` (not PDF) since both sources serve structured data.

### Phase B — ingest expansion

`scripts/ingest_corpus.py` currently handles only `.pdf` via `PdfReader`. Needs to be extended to handle:

- `.pdf` — existing path
- `.xml` — eCFR title XML, walk the `<DIV*>` structure, extract text per `<P>` and `<HD>` element, set `source = "32 CFR § X.Y.Z"`
- `.txt` — DTIC plain text, set `source = "DTIC AD<accession>"` and parse first line as title

After expansion: `python scripts/ingest_corpus.py` rebuilds FAISS over everything in `corpus/`. ~3-4 hours wall-clock for ~500K chunks on M1 CPU.

### Phase C — wire the reranker

- Add `cross-encoder/ms-marco-MiniLM-L-6-v2` install (auto-pulls from HF on first use)
- Patch [rag.py](/Users/naomiivie/adjutant/adjutant/rag.py) `retrieve()` per Section 4 above
- Add `RAG_RERANK_THRESHOLD` to `.env.example` (default 0.0 — accept all top_k candidates)
- Update startup warmup hook to pre-load the reranker

### Phase D — eval & ship

- Run the existing eval harness (or build it if not yet) — 25 questions × ground truth — measure precision delta vs no-reranker baseline. Should see precision @ top-5 climb from ~0.7 → ~0.9.
- Ship.

---

## 7. Time + storage budget

| Phase | Wall-clock | Disk | Network |
|---|---|---|---|
| Tier 1 APD (~30 docs) | 30 min | +250 MB | ~250 MB |
| eCFR Title 32 + 5 + 10 | 10 min | +200 MB | ~200 MB |
| DTIC filtered (~400 docs) | 6 hrs | +800 MB | ~800 MB |
| Re-ingest FAISS (~500K chunks) | 3.5 hrs | +500 MB FAISS index | 0 |
| Cross-encoder reranker model | 1 min | +90 MB | ~90 MB |
| **Total** | **~10 hours** | **~1.85 GB** | **~1.35 GB** |

Within laptop's free space. Within demo-day timeline (Phase A and B can run overnight).

---

## 8. Open risks

1. **APD pagination behind ASP.NET viewstate.** Playwright's `page.click()` works but is fragile. Fallback: scrape page 1 only (~30 docs) and supplement with the per-base mirror discovery in `home.army.mil/<base>/...`.
2. **DTIC sitemap takes hours to walk.** ~30 section sitemaps × up to 50K URLs each = lots of HTTP requests. Mitigate with an early-exit heuristic — only process citations where the title matches our admin/personnel/regulation filter.
3. **eCFR XML structure varies by title.** Title 32 uses some sections that don't match the standard CFR template. Mitigation: defensive parsing with logging when an element is unrecognized.
4. **Reranker introduces 300ms latency.** Cumulative round-trip becomes ~5-7 sec end-to-end (STT 1s + RAG 0.05s + reranker 0.3s + LLM 4s + TTS 0.5s). Still well under the 10-second demo-feels-slow threshold.
5. **Some DTIC PDFs are scanned images** with no extractable text. Mitigate with a `len(text) < 50_000` filter that drops them before embedding (already in our PDF chunker).
6. **Mixed-revision contradictions** — APD's Active.aspx index filters these out, but DTIC keeps superseded analyses. Mitigation: prefer documents dated 2020+ from DTIC; explicitly tag the ingest year in chunk metadata.

---

## 9. The pitch line this unlocks

> *"Adjutant retrieves over 500 current-revision Army regulations, joint publications, DoD technical reports, and Title 32 of the Code of Federal Regulations — every authoritative source the rank-and-file would ever cite. Half a million chunks, two-stage retrieval with a cross-encoder reranker, all running locally on this laptop. When you ask about leave, we cite AR 600-8-10 paragraph 4-3 *and* the underlying 10 U.S.C. § 701 statute, *and* the RAND analysis explaining why the regulation is worded that way. No internet. No cloud. No leakage."*

That's the saturating-tier answer. Each comma is a defensible technical claim and each one comes from a different source class.

---

## Sources

- [eCFR API Documentation](https://www.ecfr.gov/developers/documentation/api/v1)
- [eCFR Reader Aids — Developer Resources](https://www.ecfr.gov/reader-aids/ecfr-developer-resources)
- [GPO Bulk Data — eCFR XML User Guide](https://github.com/usgpo/bulk-data/blob/main/ECFR-XML-User-Guide.md)
- [DTIC Public Products and Services](https://discover.dtic.mil/products-services/)
- [DTIC Technical Reports](https://discover.dtic.mil/technical-reports/)
- [DTIC TR Redirect / accession URL pattern](https://discover.dtic.mil/tr_redirect/)
- [DTIC sitemap (bulk discovery)](https://apps.dtic.mil/sitemap.xml)
- [Army Publishing Directorate — Active publications index](https://armypubs.army.mil/ProductMaps/PubForm/Active.aspx)
- [cross-encoder/ms-marco-MiniLM-L-6-v2](https://huggingface.co/cross-encoder/ms-marco-MiniLM-L6-v2)
- [Sentence-Transformers MS MARCO Cross-Encoders](https://www.sbert.net/docs/pretrained-models/ce-msmarco.html)
- [Advanced RAG Retrieval — Cross-Encoders & Reranking](https://towardsdatascience.com/advanced-rag-retrieval-cross-encoders-reranking/)
- [Critical Role of Rerankers in RAG (Medium)](https://medium.com/@akanshak/the-critical-role-of-rerankers-in-rag-98309f52abe5)
