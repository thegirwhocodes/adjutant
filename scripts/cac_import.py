#!/usr/bin/env python3
"""Read identity from the CAC card and seed Adjutant's profile.

The CAC's PIV-Auth certificate carries the soldier's name + 10-digit EDIPI in
the Subject CN and SAN-UPN extensions:

    Subject: CN=LAST.FIRST.MIDDLE.EDIPI, OU=USA, OU=PKI, OU=DoD, ...
    X509v3 Subject Alternative Name:
        othername:UPN: <EDIPI>@mil
        email:firstname.m.lastname.mil@army.mil

Reading paths:
  macOS / Linux  : OpenSC `pkcs15-tool` + `openssl x509 -text`
  Windows        : PowerShell `Get-ChildItem Cert:\\CurrentUser\\My`

Run:
    python scripts/cac_import.py            # interactive — prompts to confirm
    python scripts/cac_import.py --dry-run  # print parsed values, do not save

Sets only the fields the cert authoritatively carries: name_last, name_first,
name_middle, soldier.dodid, soldier.email. Soldier still needs to fill in
rank / unit / duty_station / phone via the Profile tab — those aren't on the
cert.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Allow running from repo root without installing
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from adjutant.profile import load_profile, save_profile, set_sensitive  # noqa: E402


# Subject CN: CN=LAST.FIRST.MIDDLE.EDIPI  (middle initial is optional)
CN_RE = re.compile(
    r"CN\s*=\s*([A-Z][A-Z'\-]+)\.([A-Z][A-Z'\-]+)\.([A-Z]?)\.?(\d{10})",
)
# UPN: <EDIPI>@mil
UPN_RE   = re.compile(r"\b(\d{10})@mil\b")
EMAIL_RE = re.compile(r"\b([\w.\-]+@(?:army|mail|us\.af|navy)\.mil)\b", re.I)


# ---------------------------------------------------------------------------
# Backend: macOS / Linux via OpenSC
# ---------------------------------------------------------------------------

def _opensc_dump() -> str | None:
    """Read every certificate on the inserted card via pkcs15-tool and pipe
    each through `openssl x509 -text`. Returns the concatenated decoded
    output, or None if OpenSC is unavailable / no card inserted."""
    if not shutil.which("pkcs15-tool"):
        return None
    if not shutil.which("openssl"):
        return None
    try:
        listed = subprocess.run(
            ["pkcs15-tool", "--list-certificates"],
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if listed.returncode != 0:
        return None

    # Each "X.509 Certificate ... ID <id>" line gives us a slot to read.
    ids = re.findall(r"^\s*ID\s*:\s*([0-9a-fA-F]+)", listed.stdout, re.M)
    if not ids:
        return None

    chunks: list[str] = []
    for cid in ids:
        try:
            der = subprocess.run(
                ["pkcs15-tool", "--read-certificate", cid],
                capture_output=True, timeout=15,
            )
            if der.returncode != 0 or not der.stdout:
                continue
            decoded = subprocess.run(
                ["openssl", "x509", "-text", "-noout"],
                input=der.stdout, capture_output=True, text=True, timeout=10,
            )
            if decoded.returncode == 0:
                chunks.append(decoded.stdout)
        except subprocess.TimeoutExpired:
            continue
    return "\n".join(chunks) if chunks else None


# ---------------------------------------------------------------------------
# Backend: Windows via PowerShell
# ---------------------------------------------------------------------------

def _powershell_dump() -> str | None:
    """Enumerate the user's certificate store and emit each DoD cert in
    the same `openssl -text`-ish form CN_RE / UPN_RE / EMAIL_RE expect."""
    if platform.system() != "Windows":
        return None
    ps = shutil.which("powershell") or shutil.which("pwsh")
    if not ps:
        return None
    script = (
        "$certs = Get-ChildItem Cert:\\CurrentUser\\My | "
        "Where-Object { $_.Subject -like '*OU=DoD*' };"
        "$certs | ForEach-Object {"
        "  Write-Output ('Subject: ' + $_.Subject);"
        "  $san = $_.Extensions | Where-Object { $_.Oid.Value -eq '2.5.29.17' };"
        "  if ($san) { Write-Output $san.Format($true) }"
        "}"
    )
    try:
        out = subprocess.run(
            [ps, "-NoProfile", "-Command", script],
            capture_output=True, text=True, timeout=15,
        )
    except subprocess.TimeoutExpired:
        return None
    return out.stdout if out.returncode == 0 and out.stdout else None


# ---------------------------------------------------------------------------
# Parse + merge
# ---------------------------------------------------------------------------

def parse(text: str) -> dict[str, str]:
    """Pull name parts + EDIPI + email out of the CAC cert dump."""
    out: dict[str, str] = {}
    m = CN_RE.search(text)
    if m:
        out["name_last"]   = m.group(1).title()
        out["name_first"]  = m.group(2).title()
        if m.group(3):
            out["name_middle"] = m.group(3)
        out["dodid"] = m.group(4)
    if "dodid" not in out:
        m2 = UPN_RE.search(text)
        if m2:
            out["dodid"] = m2.group(1)
    m3 = EMAIL_RE.search(text)
    if m3:
        out["email"] = m3.group(1).lower()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Seed Adjutant profile from CAC card.")
    ap.add_argument("--dry-run", action="store_true", help="Print, don't save.")
    ap.add_argument("--input-file", help="Read OpenSSL-formatted cert text from a file (skip card read).")
    args = ap.parse_args()

    if args.input_file:
        text = Path(args.input_file).read_text()
    else:
        text = _opensc_dump() or _powershell_dump()

    if not text:
        print("ERR  no CAC reader found and no --input-file given.", file=sys.stderr)
        print("     macOS/Linux: brew install opensc  (or apt install opensc)", file=sys.stderr)
        print("     Windows: ActivClient must be installed (DoD-issued)", file=sys.stderr)
        return 2

    fields = parse(text)
    if not fields:
        print("ERR  no DoD identity found in cert dump.", file=sys.stderr)
        return 3

    print("Found CAC identity:")
    for k, v in fields.items():
        print(f"  {k:14} = {v}")

    if args.dry_run:
        return 0

    confirm = input("\nSave to ~/.adjutant/profile.json? [y/N] ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        return 0

    profile = load_profile()
    profile.setdefault("soldier", {}).update(fields)
    save_profile(profile)

    # Stash the full DODID in keychain too so pdf_fill can pull it without
    # round-tripping the JSON file each time.
    if "dodid" in fields:
        set_sensitive("dodid", fields["dodid"])

    print(f"OK  wrote {len(fields)} fields to ~/.adjutant/profile.json")
    print("     Run `python scripts/import_erb.py <stp.pdf>` next to fill in")
    print("     rank / unit / duty station / awards.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
