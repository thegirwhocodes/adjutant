"""Populate a blank DA / DD form PDF with extracted field values.

Two strategies, tried in order:

  1. AcroForm field-fill via pikepdf  — for true-fillable PDFs (most DD
     forms, some Army forms).
  2. Reportlab overlay merge          — for XFA forms and flat scans
     (the DA-31 from armypubs.army.mil ships as XFA, no AcroForm fields).

Strategy 2 uses a hand-tuned coordinate map per form. Coordinates are
in PDF points (1pt = 1/72 inch), origin bottom-left.
"""

import io
import logging
from pathlib import Path

import pikepdf
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

log = logging.getLogger("adjutant.pdf_fill")


# ---------------------------------------------------------------------------
# Coordinate maps for the overlay strategy. Tuned by eyeballing the official
# DA-31 / DD-1351-2 / DA-4856 layouts. Refine during demo rehearsal.
# Page indices are 0-based. Coordinates: (x, y) in PDF points from
# bottom-left corner. Each form is US letter (612 x 792 pt).
# ---------------------------------------------------------------------------

# DA-31 — Request and Authority for Leave (June 2020 revision)
# Block layout reference:
#   - Box 1: TYPE (block 1) at top, name + grade left
#   - Box 4: NAME (LAST, FIRST, MI)
#   - Box 5: SSN
#   - Box 6: GRADE / RANK
#   - Box 7a: dates from
#   - Box 7b: dates to
#   - Box 8: number of days
#   - Box 11: ORG/STATION
#   - Box 12: leave address
#   - Box 13: telephone
DA_31_OVERLAY = {
    0: {
        "name":              {"x":  90, "y": 668, "size": 10},
        "ssn":               {"x": 320, "y": 668, "size": 10},
        "rank":              {"x": 470, "y": 668, "size": 10},
        "leave_type":        {"x":  60, "y": 700, "size":  9, "prefix": "X "},
        "start_date":        {"x":  90, "y": 632, "size": 10},
        "end_date":          {"x": 230, "y": 632, "size": 10},
        "days_requested":    {"x": 360, "y": 632, "size": 10},
        "unit":              {"x":  90, "y": 596, "size": 10},
        "leave_address":     {"x":  90, "y": 558, "size": 10},
        "leave_phone":       {"x":  90, "y": 522, "size": 10},
        "emergency_contact": {"x": 320, "y": 522, "size": 10},
    }
}

# DD-1351-2 — Travel Voucher (3-page form)
DD_1351_2_OVERLAY = {
    0: {
        "name":            {"x":  90, "y": 700, "size": 10},
        "ssn":             {"x": 380, "y": 700, "size": 10},
        "rank":            {"x":  90, "y": 670, "size": 10},
        "duty_station":    {"x":  90, "y": 640, "size": 10},
        "purpose":         {"x":  90, "y": 580, "size":  9},
        "tdy_location":    {"x":  90, "y": 540, "size": 10},
        "depart_date":     {"x": 380, "y": 540, "size": 10},
        "return_date":     {"x": 480, "y": 540, "size": 10},
        "total_days":      {"x":  90, "y": 500, "size": 10},
        "lodging_per_day": {"x": 220, "y": 500, "size": 10},
        "mie_per_day":     {"x": 320, "y": 500, "size": 10},
        "estimated_total": {"x": 460, "y": 500, "size": 10, "prefix": "$"},
    }
}

# DA-4856 — Developmental Counseling Form
DA_4856_OVERLAY = {
    0: {
        "name":             {"x":  90, "y": 720, "size": 10},
        "rank":             {"x": 380, "y": 720, "size": 10},
        "date":             {"x": 480, "y": 720, "size": 10},
        "counselor_name":   {"x":  90, "y": 690, "size": 10},
        "counselor_rank":   {"x": 380, "y": 690, "size": 10},
        "counseling_type":  {"x":  90, "y": 660, "size":  9},
        "purpose":          {"x":  90, "y": 600, "size":  9},
        "key_points":       {"x":  90, "y": 480, "size":  9},
        "plan_of_action":   {"x":  90, "y": 320, "size":  9},
    }
}

OVERLAY_MAPS = {
    "da_31_blank.pdf":     DA_31_OVERLAY,
    "dd_1351_2_blank.pdf": DD_1351_2_OVERLAY,
    "da_4856_blank.pdf":   DA_4856_OVERLAY,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fill_pdf(template_path: str, data: dict, output_path: str, schema: dict | None = None) -> str:
    """Fill a DA/DD form PDF with the given field values.

    Args:
        template_path: blank PDF
        data: {semantic_field_name: value} from the LLM
        output_path: where to write the filled PDF
        schema: optional registry entry for the form. If provided AND its
            fields contain "pdf_field" entries, we translate semantic names
            into the form's actual AcroForm field names before writing.

    Tries AcroForm fill first; falls back to reportlab overlay merge if the
    PDF has no fillable fields (XFA-only or flat scan).
    """
    template = Path(template_path)
    if not template.exists():
        raise FileNotFoundError(
            f"Blank PDF not found: {template_path}. "
            f"Run: python scripts/download_corpus.py"
        )

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    # Translate semantic names -> actual PDF field names if the schema tells us how.
    if schema and "fields" in schema:
        translated = {}
        for sem_name, value in data.items():
            spec = schema["fields"].get(sem_name, {})
            pdf_name = spec.get("pdf_field") if isinstance(spec, dict) else None
            if pdf_name:
                translated[pdf_name] = value
            else:
                # Keep the semantic name as fallback (works for forms whose
                # AcroForm field names already match our schema keys).
                translated[sem_name] = value
        data = translated

    str_data = {k: ("" if v is None else str(v)) for k, v in data.items()}

    # Strategy 1: AcroForm fill.
    n_filled = _try_acroform_fill(template, str_data, out)
    if n_filled > 0:
        log.info(f"Filled {n_filled} AcroForm fields in {template.name} → {out.name}")
        return str(out)

    # Strategy 2: overlay (XFA / flat-scan fallback).
    log.info(f"{template.name} has no AcroForm fields — using reportlab overlay")
    n_overlaid = _overlay_fill(template, str_data, out)
    log.info(f"Overlaid {n_overlaid} values onto {template.name} → {out.name}")
    return str(out)


# ---------------------------------------------------------------------------
# Strategy 1: AcroForm field-fill via pikepdf
# ---------------------------------------------------------------------------

def _try_acroform_fill(template: Path, data: dict, out: Path) -> int:
    """Attempt to fill AcroForm fields. Returns count filled (0 if no AcroForm)."""
    n_filled = 0
    fields_seen: list[str] = []

    with pikepdf.open(str(template)) as pdf:
        if "/AcroForm" not in pdf.Root or "/Fields" not in pdf.Root.AcroForm:
            return 0

        def walk(field):
            nonlocal n_filled
            if "/Kids" in field:
                for kid in field.Kids:
                    walk(kid)
                return
            name = str(field.T) if "/T" in field else None
            if name is None:
                return
            fields_seen.append(name)
            if name in data:
                value = data[name]
                ft = field.get("/FT")
                if ft == "/Btn":
                    field.V = pikepdf.Name("/" + value) if value else pikepdf.Name("/Off")
                else:
                    field.V = pikepdf.String(value)
                n_filled += 1

        if len(pdf.Root.AcroForm.Fields) == 0:
            return 0
        for top_field in pdf.Root.AcroForm.Fields:
            walk(top_field)

        if n_filled == 0:
            return 0

        pdf.Root.AcroForm.NeedAppearances = True
        pdf.save(str(out))

    return n_filled


# ---------------------------------------------------------------------------
# Strategy 2: reportlab overlay
# ---------------------------------------------------------------------------

def _build_overlay(field_map: dict, data: dict) -> bytes:
    """Build a single-page (or multi-page) overlay PDF with text drawn at the
    coordinates in `field_map`. Returns raw PDF bytes.

    field_map: {page_idx: {field_name: {"x": pt, "y": pt, "size": pt, "prefix": str?}}}
    """
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFillColorRGB(0.05, 0.05, 0.05)  # near-black

    n_pages = max(field_map.keys()) + 1 if field_map else 1

    for page_idx in range(n_pages):
        page_fields = field_map.get(page_idx, {})
        for fname, spec in page_fields.items():
            value = data.get(fname, "")
            if not value:
                continue
            prefix = spec.get("prefix", "")
            text = f"{prefix}{value}"
            c.setFont("Helvetica", spec.get("size", 10))
            # Wrap long strings (>60 chars) onto multi-line
            if len(text) > 60:
                _draw_wrapped(c, text, spec["x"], spec["y"], spec.get("size", 10))
            else:
                c.drawString(spec["x"], spec["y"], text)
        c.showPage()

    c.save()
    return buf.getvalue()


def _draw_wrapped(c, text: str, x: int, y: int, font_size: int) -> None:
    """Crude word-wrap for long strings (e.g. counseling key_points)."""
    width_chars = 70
    line_height = font_size + 2
    words = text.split()
    line, lines = "", []
    for w in words:
        candidate = (line + " " + w).strip()
        if len(candidate) > width_chars:
            lines.append(line)
            line = w
        else:
            line = candidate
    if line:
        lines.append(line)
    for i, ln in enumerate(lines):
        c.drawString(x, y - i * line_height, ln)


def _overlay_fill(template: Path, data: dict, out: Path) -> int:
    """Merge a reportlab overlay onto each page of the template."""
    field_map = OVERLAY_MAPS.get(template.name, {})
    if not field_map:
        log.warning(f"No overlay map for {template.name}; copying template unchanged")
        with pikepdf.open(str(template)) as pdf:
            pdf.save(str(out))
        return 0

    overlay_bytes = _build_overlay(field_map, data)
    n_overlaid = sum(1 for page in field_map.values() for f, _ in page.items() if data.get(f))

    with pikepdf.open(str(template)) as base, pikepdf.open(io.BytesIO(overlay_bytes)) as ov:
        for page_idx, base_page in enumerate(base.pages):
            if page_idx >= len(ov.pages):
                break
            base_page.add_overlay(ov.pages[page_idx])
        base.save(str(out))

    return n_overlaid
