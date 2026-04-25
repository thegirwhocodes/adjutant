"""GSA per-diem lookup. Uses a local cache so we work offline.

The cache is built by scripts/download_corpus.py from open.gsa.gov/api/perdiem-api.
At runtime we just read the JSON.
"""

import json
import logging
from datetime import date
from pathlib import Path

log = logging.getLogger("adjutant.per_diem")

CACHE_PATH = Path("corpus/per_diem.json")

# FY 2026 default rates (when destination not in GSA file). Per DTMO.
DEFAULT_LODGING = 110
DEFAULT_MIE = 68


def lookup(city: str, state: str, travel_date: date | None = None) -> dict:
    """Return {"lodging": int, "mie": int, "source": "GSA city / GSA default"}.

    travel_date currently unused but kept in the signature so we can layer in
    seasonal rates later without breaking callers.
    """
    if not CACHE_PATH.exists():
        log.warning(f"Per-diem cache missing: {CACHE_PATH}. Using FY26 defaults.")
        return _default()

    try:
        with open(CACHE_PATH) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"Per-diem cache unreadable ({e}). Using defaults.")
        return _default()

    key = f"{city.strip().lower()}, {state.strip().upper()}"
    if key in data:
        rate = data[key]
        return {
            "lodging": rate["lodging"],
            "mie": rate["mie"],
            "source": f"GSA — {city}, {state}",
        }

    state_default = data.get(f"_state_default_{state.strip().upper()}")
    if state_default:
        return {
            "lodging": state_default["lodging"],
            "mie": state_default["mie"],
            "source": f"GSA — {state} state default",
        }

    return _default()


def _default() -> dict:
    return {
        "lodging": DEFAULT_LODGING,
        "mie": DEFAULT_MIE,
        "source": "GSA FY26 CONUS default",
    }


def calculate_tdy_total(
    city: str, state: str, days: int, travel_date: date | None = None
) -> dict:
    """Total estimate for a TDY trip. Returns full breakdown for the form."""
    rate = lookup(city, state, travel_date)
    # M&IE: 75% on first + last day (travel days), full rate on intervening.
    if days <= 1:
        mie_total = rate["mie"] * 0.75
    else:
        mie_total = rate["mie"] * 0.75 * 2 + rate["mie"] * (days - 2)
    lodging_total = rate["lodging"] * (days - 1)  # last night usually not lodged

    return {
        "lodging_per_day": rate["lodging"],
        "mie_per_day": rate["mie"],
        "total_days": days,
        "lodging_total": round(lodging_total, 2),
        "mie_total": round(mie_total, 2),
        "estimated_total": round(lodging_total + mie_total, 2),
        "source": rate["source"],
    }
