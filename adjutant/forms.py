"""DA / DD form schemas. Field names match the actual blank PDFs from Army Publishing Directorate.

These schemas drive both LLM extraction and pypdf field-fill. Each has:
  - form_id: short label
  - title: human description
  - regulation: governing AR
  - fields: dict of field_name → {description, type, required}
  - pdf_path: path to the blank PDF in forms/

Field names are extracted from blank PDFs by scripts/extract_form_schemas.py.
The actual field names below are placeholders to confirm against extracted JSON.
"""

DA_31 = {
    "form_id": "DA-31",
    "title": "Request and Authority for Leave",
    "regulation": "AR 600-8-10",
    "pdf_path": "forms/da_31_blank.pdf",
    "fields": {
        "name": {"desc": "Soldier full name (Last, First MI)", "type": "string", "required": True},
        "ssn": {"desc": "DoD ID / SSN last 4", "type": "string", "required": True},
        "rank": {"desc": "Pay grade (e.g., E-5)", "type": "string", "required": True},
        "unit": {"desc": "Unit and station", "type": "string", "required": True},
        "leave_type": {"desc": "Ordinary | Emergency | Convalescent | PTDY | Permissive | Terminal", "type": "string", "required": True},
        "start_date": {"desc": "First day of leave (YYYY-MM-DD)", "type": "date", "required": True},
        "end_date": {"desc": "Last day of leave (YYYY-MM-DD)", "type": "date", "required": True},
        "days_requested": {"desc": "Total leave days", "type": "integer", "required": True},
        "leave_address": {"desc": "Address while on leave", "type": "string", "required": True},
        "leave_phone": {"desc": "Phone number while on leave", "type": "string", "required": True},
        "emergency_contact": {"desc": "POC during leave (name + relation)", "type": "string", "required": False},
    },
}

DD_1351_2 = {
    "form_id": "DD-1351-2",
    "title": "Travel Voucher (Sub-Voucher)",
    "regulation": "Joint Travel Regulations",
    "pdf_path": "forms/dd_1351_2_blank.pdf",
    # Schema keys are the LLM-facing semantic names (what we ask the LLM to populate).
    # The "pdf_field" entries are the actual AcroForm field names in the blank PDF —
    # consumed by pdf_fill.py to write into the right blocks.
    # PDF block mapping (DD-1351-2 NOV 2025 revision):
    #   Block 2  = Name              (two[0])
    #   Block 3  = Grade             (three[0])
    #   Block 4  = SSN / DoD ID      (four_specify[0])
    #   Block 6a-e = Home address    (sixA-E[0]) — we use sixA for home base
    #   Block 11 = Organization/Station — i.e. soldier's HOME unit (eleven[0])
    #   Block 15 = Itinerary table — DEP/ARR rows × city/reason/lodging columns
    "fields": {
        "name":            {"desc": "Traveler last name only",                         "type": "string", "required": True,  "pdf_field": "two[0]"},
        "rank":            {"desc": "Pay grade like E-5, O-3",                          "type": "string", "required": True,  "pdf_field": "three[0]"},
        "ssn":             {"desc": "Last 4 of SSN or DoD ID",                          "type": "string", "required": False, "pdf_field": "four_specify[0]"},
        "duty_station":    {"desc": "Home base / duty station street address",          "type": "string", "required": True,  "pdf_field": "sixA[0]"},
        "unit":            {"desc": "Home unit name (Block 11 — Organization & Station)","type": "string", "required": True,  "pdf_field": "eleven[0]"},
        "purpose":         {"desc": "Purpose of TDY (Block 15 line 2 reason for stop)", "type": "string", "required": True,  "pdf_field": "fifteen_reason_line2[0]"},
        "tdy_location":    {"desc": "TDY destination city, state",                       "type": "string", "required": True,  "pdf_field": "fifteen_place_line1[0]"},
        "depart_date":     {"desc": "Date of departure (YYYYMMDD)",                      "type": "date",   "required": True,  "pdf_field": "fifteen_dep_date_line1[0]"},
        "return_date":     {"desc": "Date of return (YYYYMMDD)",                         "type": "date",   "required": True,  "pdf_field": "fifteen_arr_date_line2[0]"},
        "total_days":      {"desc": "Total TDY days",                                    "type": "integer","required": True,  "pdf_field": None},
        "lodging_per_day": {"desc": "GSA lodging rate ($/day)",                          "type": "number", "required": True,  "pdf_field": "fifteen_lodging_line2[0]"},
        "mie_per_day":     {"desc": "GSA M&IE rate ($/day)",                             "type": "number", "required": True,  "pdf_field": None},
        "estimated_total": {"desc": "Estimated total reimbursement (USD)",               "type": "number", "required": True,  "pdf_field": None},
    },
}

DA_4856 = {
    "form_id": "DA-4856",
    "title": "Developmental Counseling Form",
    "regulation": "AR 623-3",
    "pdf_path": "forms/da_4856_blank.pdf",
    "fields": {
        "name": {"desc": "Counselee full name", "type": "string", "required": True},
        "rank": {"desc": "Pay grade", "type": "string", "required": True},
        "date": {"desc": "Counseling date", "type": "date", "required": True},
        "counselor_name": {"desc": "Counselor full name", "type": "string", "required": True},
        "counselor_rank": {"desc": "Counselor pay grade", "type": "string", "required": True},
        "counseling_type": {"desc": "Event-Oriented | Performance/Professional Growth", "type": "string", "required": True},
        "purpose": {"desc": "Purpose of counseling (one sentence)", "type": "string", "required": True},
        "key_points": {"desc": "Bullet list of discussion points", "type": "string", "required": True},
        "plan_of_action": {"desc": "What soldier will do next; deadlines", "type": "string", "required": True},
    },
}

REGISTRY = {
    "DA-31": DA_31,
    "DD-1351-2": DD_1351_2,
    "DA-4856": DA_4856,
}


def get_schema(form_id: str) -> dict:
    """Look up a form schema by ID. Raises KeyError if unknown."""
    if form_id not in REGISTRY:
        raise KeyError(f"Unknown form: {form_id}. Available: {list(REGISTRY)}")
    return REGISTRY[form_id]


def list_forms() -> list[dict]:
    """Return summary of all registered forms (for /forms endpoint)."""
    return [
        {"id": f["form_id"], "title": f["title"], "regulation": f["regulation"]}
        for f in REGISTRY.values()
    ]
