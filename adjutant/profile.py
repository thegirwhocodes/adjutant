"""Soldier profile loader for Adjutant.

The profile lets Adjutant fill DA-31 / DD-1351-2 / DA-4856 fields the soldier
already has on file (name, rank, unit, duty station, DODID, SSN-last4, etc.)
without making them dictate every form. The voice request only needs to
supply what's NEW for that form — dates, location, leave type, purpose.

Storage layout
--------------
~/.adjutant/profile.json    -- non-sensitive identity (name, rank, unit, ...)
OS keychain                  -- sensitive (full SSN, emergency contact info)
                                 service="adjutant", account=<field>

The profile carries a CUI marking per DoDI 5200.48 ("Controlled Unclassified
Information"). PII inside is in compliance with the marking scheme; nothing
in this file is transmitted off-device by Adjutant.

The keychain layer is OPTIONAL — if `keyring` isn't installed it falls back
to a sidecar `~/.adjutant/sensitive.json` (chmod 600). For a hackathon demo
that's acceptable; for a productized version, install `keyring` and the
sensitive fields move into the OS-native vault automatically.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger("adjutant.profile")

PROFILE_DIR  = Path(os.getenv("ADJUTANT_PROFILE_DIR", str(Path.home() / ".adjutant")))
PROFILE_PATH = PROFILE_DIR / "profile.json"
SENSITIVE_FALLBACK_PATH = PROFILE_DIR / "sensitive.json"

KEYRING_SERVICE = "adjutant"

# ---- Optional keyring backend ---------------------------------------------
try:
    import keyring  # type: ignore
    _HAS_KEYRING = True
except Exception:  # pragma: no cover
    keyring = None  # type: ignore
    _HAS_KEYRING = False


def _ensure_dir() -> None:
    PROFILE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Profile load / save
# ---------------------------------------------------------------------------

def load_profile() -> dict[str, Any]:
    """Return the stored profile dict, or {} if no profile exists."""
    if not PROFILE_PATH.exists():
        return {}
    try:
        return json.loads(PROFILE_PATH.read_text())
    except (OSError, json.JSONDecodeError) as e:
        log.warning(f"profile read failed: {e}")
        return {}


def save_profile(data: dict[str, Any]) -> None:
    """Atomically write the profile JSON to disk with restrictive perms."""
    _ensure_dir()
    data = dict(data)
    data.setdefault("schema_version", 1)
    data.setdefault("cui_marking", "CUI//PRIV")
    tmp = PROFILE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    os.chmod(tmp, 0o600)
    tmp.replace(PROFILE_PATH)


# ---------------------------------------------------------------------------
# Sensitive fields — keychain when available, encrypted-perm sidecar fallback
# ---------------------------------------------------------------------------

def get_sensitive(field: str) -> str | None:
    """Read a sensitive field (SSN, emergency contact, etc.) from the OS
    keychain. Falls back to a chmod-600 JSON sidecar if keyring isn't
    installed. Returns None if absent.
    """
    if _HAS_KEYRING:
        try:
            return keyring.get_password(KEYRING_SERVICE, field)
        except Exception as e:  # pragma: no cover
            log.warning(f"keyring read for {field!r} failed: {e}; falling through to sidecar")
    if SENSITIVE_FALLBACK_PATH.exists():
        try:
            return json.loads(SENSITIVE_FALLBACK_PATH.read_text()).get(field)
        except (OSError, json.JSONDecodeError):
            return None
    return None


def set_sensitive(field: str, value: str) -> None:
    """Persist a sensitive field. Prefers OS keychain; falls back to a
    chmod-600 sidecar JSON in the profile dir."""
    if _HAS_KEYRING:
        try:
            keyring.set_password(KEYRING_SERVICE, field, value)
            return
        except Exception as e:  # pragma: no cover
            log.warning(f"keyring write for {field!r} failed: {e}; falling through to sidecar")
    _ensure_dir()
    cur: dict[str, Any] = {}
    if SENSITIVE_FALLBACK_PATH.exists():
        try:
            cur = json.loads(SENSITIVE_FALLBACK_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            cur = {}
    cur[field] = value
    SENSITIVE_FALLBACK_PATH.write_text(json.dumps(cur, indent=2, sort_keys=True))
    os.chmod(SENSITIVE_FALLBACK_PATH, 0o600)


# ---------------------------------------------------------------------------
# View used by the LLM prompt
# ---------------------------------------------------------------------------

# These are the fields safe to inline into the form-extraction prompt.
# Full SSN + emergency-contact details stay in the keychain and are pulled
# only at form-write time by pdf_fill, never sent through the LLM.
_LLM_SAFE_KEYS = {
    "soldier": [
        "name_last", "name_first", "name_middle",
        "rank", "rank_title", "dodid", "ssn_last4",
        "email", "phone_dsn", "phone_commercial",
    ],
    "unit": [
        "name", "uic", "duty_station", "duty_station_address",
    ],
    "service": [
        "component", "mos",
    ],
}


def llm_profile_view() -> dict[str, Any]:
    """Return the subset of the profile safe to inline into LLM prompts."""
    p = load_profile()
    out: dict[str, Any] = {}
    for top, allowed in _LLM_SAFE_KEYS.items():
        section = p.get(top) or {}
        if not isinstance(section, dict):
            continue
        for k in allowed:
            v = section.get(k)
            if v not in (None, "", []):
                out[f"{top}.{k}"] = v
    return out


def llm_profile_json() -> str:
    """Return a JSON string of the LLM-safe profile fields, or '{}' if no
    profile exists. Suitable for direct interpolation into a prompt."""
    return json.dumps(llm_profile_view(), indent=2, sort_keys=True)
