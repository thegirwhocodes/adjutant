#!/usr/bin/env python3
"""Interactive Adjutant profile setup. Use when CAC + ERB import isn't
available — soldier types in the static facts once.

Run:
    python scripts/profile_setup.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from adjutant.profile import load_profile, save_profile, set_sensitive  # noqa: E402


PROMPTS = [
    # (path, label, optional)
    (("soldier", "rank_title"), "Rank title (e.g. SGT, SSG, CPT)", False),
    (("soldier", "rank"),       "Pay grade (e.g. E-5, E-6, O-3)",  False),
    (("soldier", "name_last"),  "Last name",                       False),
    (("soldier", "name_first"), "First name",                      False),
    (("soldier", "name_middle"),"Middle initial",                  True),
    (("soldier", "dodid"),      "DoD ID / EDIPI (10 digits)",      False),
    (("soldier", "ssn_last4"),  "SSN last 4",                      False),
    (("soldier", "email"),      "Army email (firstname.m.lastname.mil@army.mil)", True),
    (("soldier", "phone_dsn"),  "DSN phone (XXX-XXXX)",            True),
    (("soldier", "phone_commercial"), "Commercial phone",          True),
    (("unit",    "name"),       "Unit (e.g. B Co, 1-504 PIR)",     False),
    (("unit",    "uic"),        "UIC (6 chars, e.g. WAUAA0)",      True),
    (("unit",    "duty_station"),"Duty station (e.g. Fort Bragg, NC)", False),
    (("service", "component"),  "Component (RA / USAR / ARNG)",    True),
    (("service", "mos"),        "PMOS / AOC (e.g. 11B)",           True),
]

SENSITIVE_PROMPTS = [
    ("ssn_full", "Full SSN (XXX-XX-XXXX) — stored in OS keychain only", True),
    ("emergency_contact_name",  "Emergency contact name — keychain", True),
    ("emergency_contact_phone", "Emergency contact phone — keychain", True),
]


def main() -> int:
    print("Adjutant profile setup")
    print("=" * 50)
    print("Adjutant runs entirely on this laptop. Your profile, voice, and")
    print("filled forms never leave the device. Sensitive fields (full SSN,")
    print("emergency contact) go to the OS keychain — not plain files.")
    print("Press <Enter> to skip optional fields.\n")

    profile = load_profile()
    for path, label, optional in PROMPTS:
        section, key = path
        existing = profile.get(section, {}).get(key, "")
        suffix = f" [{existing}]" if existing else ""
        opt_tag = " (optional)" if optional else ""
        v = input(f"  {label}{opt_tag}{suffix}: ").strip()
        if not v:
            continue
        profile.setdefault(section, {})[key] = v

    save_profile(profile)
    print("\nWrote ~/.adjutant/profile.json")

    print("\n--- Sensitive fields (keychain) ---")
    for key, label, optional in SENSITIVE_PROMPTS:
        v = input(f"  {label}: ").strip()
        if v:
            set_sensitive(key, v)

    print("\nDone. Adjutant will now auto-fill these fields on every form.")
    print("Voice prompt becomes: \"file ten days starting June 3\" — no name/rank/unit needed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
