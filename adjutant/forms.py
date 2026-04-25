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
    "fields": {
        "name": {"desc": "Traveler full name", "type": "string", "required": True},
        "ssn": {"desc": "DoD ID / SSN last 4", "type": "string", "required": True},
        "rank": {"desc": "Pay grade", "type": "string", "required": True},
        "duty_station": {"desc": "Permanent duty station", "type": "string", "required": True},
        "purpose": {"desc": "Purpose of TDY (e.g., training, conference)", "type": "string", "required": True},
        "tdy_location": {"desc": "Destination city, state", "type": "string", "required": True},
        "depart_date": {"desc": "Date of departure", "type": "date", "required": True},
        "return_date": {"desc": "Date of return", "type": "date", "required": True},
        "lodging_per_day": {"desc": "GSA lodging rate at destination (USD)", "type": "number", "required": True},
        "mie_per_day": {"desc": "GSA M&IE rate at destination (USD)", "type": "number", "required": True},
        "total_days": {"desc": "Number of TDY days", "type": "integer", "required": True},
        "estimated_total": {"desc": "Estimated total reimbursement (USD)", "type": "number", "required": True},
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
