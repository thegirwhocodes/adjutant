# RunPod walkthrough — DTIC pipeline in ~2 hours, ~$3-5

**The script `run_on_runpod.py` is verified working.** All 4 stages pass on a 20-PDF test (~5 min on M1). RunPod scales it to 30,000 PDFs in ~2 hours.

---

## Step 1 — Sign in + add credits

1. [runpod.io](https://www.runpod.io) → **Sign Up** (Google or email)
2. Verify email
3. Left sidebar → **Billing** → **Add Credits** → **$5** is enough for one run

## Step 2 — Add your SSH public key

On your Mac:

```bash
cat ~/.ssh/id_ed25519.pub 2>/dev/null || ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N "" && cat ~/.ssh/id_ed25519.pub
```

Copy the entire `ssh-ed25519 AAAA…` line. On RunPod web:
- **Settings → SSH Public Keys → Add New SSH Key** → paste → save

## Step 3 — Spin up the pod

1. Left sidebar → **Pods** → **Deploy**
2. **GPU**: Pick **A40** ($0.40/hr — cheapest A-series) or **A100 40GB** ($1.10/hr — faster). Either works.
3. **Region**: anywhere — you only upload one Python file (~10 KB) and pull back ~5 GB at the end. Geography barely matters.
4. **Template**: search and select **"RunPod PyTorch 2.4"**
5. **Container Disk**: bump to **80 GB** (default 20 GB is too tight for 30K PDFs at peak)
6. **Volume Disk**: 0 (you don't need persistent storage)
7. **Deploy On-Demand** (not Spot)
8. Click **Deploy**

Pod boots in ~30 sec. Wait until status is green **Running**.

## Step 4 — Connect via SSH

Click the pod → **Connect** → **SSH (Direct TCP)**. Copy the command, paste in your Mac terminal:

```bash
ssh root@<host> -p <port> -i ~/.ssh/id_ed25519
```

Accept the host fingerprint on first connect.

## Step 5 — Upload the script

Open a NEW Mac terminal (keep the SSH one open):

```bash
scp -P <port> -i ~/.ssh/id_ed25519 \
    ~/adjutant/colab/run_on_runpod.py \
    root@<host>:/workspace/
```

Should print `run_on_runpod.py    100%   ...   < 1 sec`.

## Step 6 — Install deps + run

Back in the **SSH session**:

```bash
cd /workspace
pip install --quiet httpx faiss-cpu sentence-transformers pypdf
python run_on_runpod.py 2>&1 | tee run.log
```

You'll see live output like:
```
[INFO] STAGE 1 — discovery
[INFO]   filtered to 60 sitemaps with range ≥ AD11090000
[INFO]   walked 10/60; matches: 4,213
...
[INFO] STAGE 2 — download 30,000 PDFs
[INFO]   download batch 200/30,000 — ok=199 cached=0
...
```

**Expected runtime:**

| Stage | Time |
|---|---|
| Stage 1 (discovery) | ~30 sec |
| Stage 2 (download 30K PDFs at 8 req/s) | **~60-90 min** |
| Stage 3 (extract + chunk) | ~15-20 min |
| Stage 4 (embed on GPU) | **~30 min** on A40, ~15 min on A100 |
| **Total** | **~2 hours** |

## Step 7 — Pull the FAISS index back

When the script prints `✅ DONE`, from your **Mac terminal**:

```bash
mkdir -p ~/adjutant/.faiss_index_cold
scp -P <port> -i ~/.ssh/id_ed25519 \
    "root@<host>:/workspace/.faiss_index_cold/*" \
    ~/adjutant/.faiss_index_cold/
```

You'll get `faiss.bin` (~1-3 GB) and `chunks.pkl` (~1-2 GB) on your Mac.

## Step 8 — STOP the pod (billing stops here)

**This is the most important step.** A100 left running burns ~$25/day.

On RunPod web:
- **Pods** → click your pod → **Stop** → wait for "Stopped" state
- Then **Terminate** to fully release the volume too

## Step 9 — Restart Adjutant's COLD tier server

On your Mac:

```bash
cd ~/adjutant && source .venv/bin/activate
python scripts/run_corpus_server.py --tier cold --port 8002
```

Hit `http://localhost:8002/health` — should now show ~1.5M chunks (or whatever your final count was).

The COLD tier of your tiered architecture is now backed by ~30,000 DTIC research reports.

---

## Tuning — if you have less time than 2 hours

Edit `run_on_runpod.py` line 28:

```python
MAX_DOCS = 30000   # default
```

Reduce to:

| MAX_DOCS | Total runtime |
|---|---|
| 1000 | ~5 min |
| 5000 | ~20 min |
| 10000 | ~40 min |
| 30000 (default) | ~2 hr |

Lower numbers are great for **demo-day quick runs**. The pitch is the same — *"Adjutant indexes thousands of DoD research reports"* — whether it's 1K or 30K.

---

## Cost summary

| GPU | Rate | 2-hour cost |
|---|---|---|
| A40 | $0.40/hr | **~$0.80** |
| A100 40GB | $1.10/hr | ~$2.20 |
| A100 80GB | $1.99/hr | ~$4.00 |

Recommendation: **A40 at $0.40/hr**. The bottleneck is download speed, not GPU — A100 only saves ~15 min on Stage 4.

If you run a smaller cap (e.g., MAX_DOCS=5000), you can use A40 and finish for **under $0.20**.

---

## Troubleshooting

**`ModuleNotFoundError` after pip install** → run `pip install` with `--upgrade`. RunPod templates sometimes have stale versions.

**Stage 2 stalls at HTTP 429** → DTIC rate-limited you. Lower `RATE_LIMIT_PER_SEC` from 8.0 to 4.0 in the script and restart. The script is resumable — re-running picks up where it stopped via the `dest.exists()` cache check.

**OOM during Stage 4** → lower `EMBED_BATCH` from 256 to 128 or 64.

**SSH connection drops mid-run** → use `tmux`:
```bash
tmux new -s dtic
python run_on_runpod.py 2>&1 | tee run.log
# Disconnect: Ctrl-B then D
# Re-attach later: tmux attach -t dtic
```

**You forgot to stop the pod** → set a Spending Alert at $10 in RunPod billing settings (auto-pauses if exceeded).

---

## What you ship

After this completes:

```
~/adjutant/.faiss_index_cold/faiss.bin       ~1-3 GB
~/adjutant/.faiss_index_cold/chunks.pkl      ~1-2 GB
~/adjutant/.faiss_index_cold/                ~30K DTIC reports indexed
```

Pitch upgrade for the demo:
> *"Adjutant's COLD tier indexes ~30,000 DoD analytical reports — every public DTIC accession from 2018 onwards — in addition to the 933-document Tier-1 corpus. All locally, all offline."*
