"""Run DTIC + APD bulk crawls in parallel and rebuild tier indexes.

Why this exists
---------------
The two big corpora (DTIC's 230K AD1 docs and APD's ~1,500 in-force pubs)
hit different hosts with different rate limits and different infrastructure.
There's zero contention between them — one's saturated by HTTP latency to
apps.dtic.mil, the other by Playwright's headless-Chrome single-tab grind on
armypubs.army.mil. So we run them concurrently as separate subprocesses and
let each crawl saturate its own throughput envelope.

Topology
--------
This script spawns three child processes in parallel:

  1. fetch_dtic_async.py    → corpus/cold/DTIC_AD1*.pdf
  2. bulk_crawl_apd.py --apd-only → corpus/<root>/*.pdf  (gets re-tiered later)
  3. fetch_ecfr.py (TODO)   → corpus/cold/eCFR_title_*.txt

Then, after all three finish, it runs:

  4. build_tier_indexes.py  → partitions everything into HOT/WARM/COLD
                              and rebuilds the three FAISS indexes.

Wall-clock target on a residential connection:
- DTIC AD1 → 5-6 hours
- APD bulk → 1-2 hours
- eCFR titles → 10 minutes
- Build indexes (CPU-only on M1) → ~3.5 hours for ~5M chunks

The script is idempotent: re-running it picks up DTIC where it left off
(via dtic_crawl_state.jsonl) and skips APD docs already in corpus/.

Run
---
    python scripts/orchestrate_crawls.py                # full crawl + index
    python scripts/orchestrate_crawls.py --skip-dtic    # APD only
    python scripts/orchestrate_crawls.py --skip-apd     # DTIC only
    python scripts/orchestrate_crawls.py --crawl-only   # don't rebuild indexes
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("orchestrate")

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"


# ---------------------------------------------------------------------------
# Subprocess wrapper
# ---------------------------------------------------------------------------

class CrawlJob:
    def __init__(self, name: str, cmd: list[str], log_file: Path):
        self.name = name
        self.cmd = cmd
        self.log_file = log_file
        self.proc: asyncio.subprocess.Process | None = None
        self.started_at: float | None = None
        self.exit_code: int | None = None

    async def run(self) -> int:
        log.info(f"[{self.name}] starting: {' '.join(self.cmd)}")
        self.started_at = time.monotonic()
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        log_fh = open(self.log_file, "w")
        try:
            self.proc = await asyncio.create_subprocess_exec(
                *self.cmd,
                stdout=log_fh,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(ROOT),
            )
            self.exit_code = await self.proc.wait()
        finally:
            log_fh.close()
        elapsed = time.monotonic() - (self.started_at or 0)
        log.info(
            f"[{self.name}] exit={self.exit_code} "
            f"elapsed={elapsed/60:.1f}min log={self.log_file}"
        )
        return self.exit_code or 0

    async def cancel(self) -> None:
        if self.proc and self.proc.returncode is None:
            log.warning(f"[{self.name}] cancelling…")
            self.proc.send_signal(signal.SIGINT)
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=10)
            except asyncio.TimeoutError:
                self.proc.kill()


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

async def run_parallel_crawls(args: argparse.Namespace) -> dict[str, int]:
    """Spawn the crawl subprocesses in parallel. Returns {name: exit_code}."""
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")

    jobs: list[CrawlJob] = []

    if not args.skip_dtic:
        dtic_cmd = [sys.executable, str(SCRIPTS / "fetch_dtic_async.py")]
        if args.resume:
            dtic_cmd.append("--resume")
        if args.dtic_min_year:
            dtic_cmd.extend(["--min-year", str(args.dtic_min_year)])
        if args.dtic_max_docs:
            dtic_cmd.extend(["--max-docs", str(args.dtic_max_docs)])
        jobs.append(CrawlJob(
            "DTIC", dtic_cmd, log_dir / f"dtic_{ts}.log",
        ))

    if not args.skip_apd:
        apd_cmd = [
            sys.executable, str(SCRIPTS / "bulk_crawl_apd.py"),
            "--apd-only",
        ]
        if args.apd_limit:
            apd_cmd.extend(["--limit", str(args.apd_limit)])
        jobs.append(CrawlJob(
            "APD", apd_cmd, log_dir / f"apd_{ts}.log",
        ))

    # Optional: eCFR fetcher would go here when written.

    if not jobs:
        log.warning("Nothing to crawl (all crawls skipped).")
        return {}

    log.info(f"Spawning {len(jobs)} parallel crawl(s)")
    log.info("  Tail their logs in real time with:")
    for j in jobs:
        log.info(f"    tail -f {j.log_file}")

    try:
        results = await asyncio.gather(*[j.run() for j in jobs])
    except (asyncio.CancelledError, KeyboardInterrupt):
        log.warning("Interrupted; cancelling crawl jobs…")
        await asyncio.gather(*[j.cancel() for j in jobs], return_exceptions=True)
        raise

    return {j.name: j.exit_code or 0 for j in jobs}


def run_index_build() -> int:
    """After crawls finish, partition + build per-tier FAISS indexes."""
    log.info("Building per-tier FAISS indexes (HOT/WARM/COLD)")
    cmd = [sys.executable, str(SCRIPTS / "build_tier_indexes.py")]
    return subprocess.call(cmd, cwd=str(ROOT))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def total_corpus_disk_usage() -> str:
    """Return total disk usage of corpus/ (and tier subdirs) as a human string."""
    total = 0
    for p in (ROOT / "corpus").rglob("*.pdf"):
        try:
            total += p.stat().st_size
        except OSError:
            pass
    return f"{total / 1e9:.2f} GB"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run all corpus crawls in parallel.")
    parser.add_argument("--skip-dtic", action="store_true", help="Skip the DTIC crawler.")
    parser.add_argument("--skip-apd", action="store_true", help="Skip the APD crawler.")
    parser.add_argument("--crawl-only", action="store_true",
                        help="Don't rebuild FAISS indexes after crawling.")
    parser.add_argument("--resume", action="store_true",
                        help="Pass --resume to DTIC crawler.")
    parser.add_argument("--dtic-min-year", type=int, default=None)
    parser.add_argument("--dtic-max-docs", type=int, default=None)
    parser.add_argument("--apd-limit", type=int, default=None,
                        help="Cap APD discovery to this many detail pages.")
    args = parser.parse_args()

    started = time.monotonic()

    # Sanity check: needed CLI tools exist
    for required in ("playwright",):
        if not shutil.which(required) and not args.skip_apd:
            log.warning(
                f"`{required}` not on PATH. APD crawler may fail. "
                f"Install: pip install playwright && playwright install chromium"
            )

    try:
        results = asyncio.run(run_parallel_crawls(args))
    except KeyboardInterrupt:
        log.error("Aborted by user")
        return 130

    log.info("=" * 64)
    log.info("CRAWL SUMMARY")
    for name, code in results.items():
        status = "✓ ok" if code == 0 else f"✘ exit={code}"
        log.info(f"  {name:5s} {status}")
    log.info(f"  total disk now: {total_corpus_disk_usage()}")

    if any(code != 0 for code in results.values()):
        log.warning(
            "One or more crawlers exited non-zero. Logs are in ./logs/. "
            "You can usually `--resume` to pick up where they stopped."
        )

    if not args.crawl_only:
        log.info("=" * 64)
        rc = run_index_build()
        if rc != 0:
            log.error(f"Index build returned exit={rc}")
            return rc

    elapsed = time.monotonic() - started
    log.info("=" * 64)
    log.info(f"DONE in {elapsed/60:.1f} minutes ({elapsed/3600:.2f} hours)")
    log.info("Next: boot the tier servers + main server (see foundry/TIERS.md §8)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
