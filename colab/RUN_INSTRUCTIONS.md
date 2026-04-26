# How to run the DTIC pipeline on Google Colab

**Time required:** ~5-10 minutes of clicking, then ~4-5 hours of unattended run.
**Cost:** $0.
**Risk:** Zero to your local machine — everything runs on Colab + saves to your Drive.

---

## Step 1 — Open Colab and create a new notebook

1. Go to **[colab.research.google.com](https://colab.research.google.com)**
2. Sign in with your Google account
3. **File → New notebook**

---

## Step 2 — Switch to T4 GPU (free)

1. **Runtime → Change runtime type**
2. Hardware accelerator: **T4 GPU**
3. Save
4. The kernel restarts automatically

---

## Step 3 — Paste the cells

Open [`/Users/naomiivie/adjutant/colab/dtic_pipeline.py`](/Users/naomiivie/adjutant/colab/dtic_pipeline.py) on your Mac.

The file has **9 cells** separated by `# %%` markers. For each cell:

1. In the file, copy everything between two `# %%` markers (or between the first `# %%` and the next one)
2. In Colab, click **+ Code** to add a new cell
3. Paste
4. Repeat for all 9 cells

The cells are:
1. **Install dependencies** (~1 min) — `pip install httpx faiss-cpu sentence-transformers pypdf`
2. **Mount Google Drive** — pops a one-click consent dialog
3. **Verify GPU** — should print `Tesla T4`
4. **Configuration** — disk paths + filter knobs
5. **Discover PDFs** — walks DTIC sitemap (~5 min)
6. **Download PDFs** — async at 50 concurrent (~3-4 hours, longest step)
7. **Extract + chunk** — pull text from each PDF (~30 min)
8. **Embed + build FAISS** — T4 GPU does the heavy lifting (~30 min)
9. **Save to Drive** — `~/MyDrive/adjutant_dtic/.faiss_index_cold/`

**Faster path: paste the whole file.** In Colab: **File → Upload notebook**? — that needs `.ipynb` format. Easier to just paste cells manually.

---

## Step 4 — Run all cells

1. Click on the first cell
2. **Runtime → Run all** (Cmd+F9)
3. Approve the Drive mount when prompted (one-time consent)
4. Walk away

You can close the browser tab — Colab keeps running in the background. Come back in ~4-5 hours.

**One thing to watch:** Colab's free tier disconnects idle sessions after ~12 hours. To prevent this, leave the tab open OR open it once per hour just to ping. Or run this Cell 0 *before* anything else to keep the session alive:

```python
# Cell 0 — keep-alive (paste at top, run before everything)
import IPython
display(IPython.display.Javascript('''
  function ConnectButton(){
    console.log("Auto-connect: clicking…");
    document.querySelector("colab-connect-button").shadowRoot.querySelector("#connect").click();
  }
  setInterval(ConnectButton, 60000);
'''))
```

---

## Step 5 — Pull the index back to your Mac

When Cell 9 prints **"Done."**:

1. On your Mac, **OneDrive sync** isn't Drive — for Drive use **Google Drive desktop app** (free, in App Store)
2. Once Drive is synced locally, the files are at:
   ```
   ~/Library/CloudStorage/GoogleDrive-<your_email>/My Drive/adjutant_dtic/.faiss_index_cold/
   ```
3. Copy them into Adjutant:
   ```bash
   cp ~/Library/CloudStorage/GoogleDrive-*/My\ Drive/adjutant_dtic/.faiss_index_cold/* \
      ~/adjutant/.faiss_index_cold/
   ```
4. Restart your COLD tier server:
   ```bash
   cd ~/adjutant && source .venv/bin/activate
   python scripts/run_corpus_server.py --tier cold --port 8002
   ```

The COLD tier now serves your DTIC corpus.

---

## Tuning the filter (Cell 4)

The variable `MIN_ACCESSION` controls how many docs you crawl:

| Value | Cutoff date (approx) | Doc count | Disk on Colab | Total time |
|---|---|---|---|---|
| `1080000` | 2015+ | ~570K | won't fit | n/a |
| `1150000` | 2018+ | ~290K | won't fit | n/a |
| `1200000` | 2020+ | ~150K | needs Colab Pro | ~10 hr |
| **`1230000`** (default) | **2022+** | **~100K → cap 30K** | **~30 GB on free Colab** | **~4-5 hr** |
| `1250000` | 2024+ | ~50K → cap 30K | ~30 GB | ~3 hr |

`MAX_DOCS = 30000` is the safety cap — even if the filter would yield more, we stop there. Tune up if you have Colab Pro disk; tune down for a fast test.

---

## Troubleshooting

**Cell 6 download is slow** → DTIC rate-limits aggressively. Don't lower `RATE_LIMIT_PER_SEC` below 8.0 or you risk IP-bans.

**"NoneType" errors in Cell 7** → Some PDFs are image-only scans. They get skipped automatically — not an error, just no extractable text.

**Cell 8 OOM (out of memory)** → Lower `EMBED_BATCH` from 256 to 128 or 64. T4 has 16 GB VRAM, batch=256 is comfortable but tight.

**Drive saves nothing** → check Cell 2 succeeded. The mount sometimes fails silently. Re-run Cell 2 and check `!ls /content/drive` shows your Drive contents.

**Session disconnects mid-run** → That's the Colab idle timeout. Re-open the notebook, **Runtime → Reconnect**, then re-run from Cell 5 (Cell 4's config is preserved if disk wasn't wiped). Cell 6's download is resumable — already-downloaded PDFs are skipped.

---

## After demo: what to do with this corpus

1. The COLD tier now has ~30K-100K DTIC docs
2. Adjutant's `tiers.py` already routes COLD queries through the cross-encoder reranker, which is essential at this scale
3. Pitch upgrade: *"Adjutant indexes 933 Army regulations + 30,000+ DoD analytical documents — RAND, GAO, CRS, TRADOC lessons-learned — locally, offline."*

The COLD tier becomes the *"why is this AR worded this way"* layer.
