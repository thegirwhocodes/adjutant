"""Sanity checks on the form registry."""

import pytest

from adjutant.forms import REGISTRY, get_schema, list_forms


def test_registry_has_three_forms():
    assert set(REGISTRY) == {"DA-31", "DD-1351-2", "DA-4856"}


def test_each_form_has_required_metadata():
    for fid, schema in REGISTRY.items():
        assert schema["form_id"] == fid
        assert schema["title"]
        assert schema["regulation"]
        assert schema["pdf_path"].endswith(".pdf")
        assert schema["fields"], f"{fid} has no fields"


def test_get_schema_unknown_raises():
    with pytest.raises(KeyError):
        get_schema("DA-9999")


def test_list_forms_summary_shape():
    summary = list_forms()
    assert len(summary) == 3
    assert all({"id", "title", "regulation"} <= set(f) for f in summary)
