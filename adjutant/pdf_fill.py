"""Populate a blank DA / DD form PDF with extracted field values."""

import logging
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import BooleanObject, NameObject, TextStringObject

log = logging.getLogger("adjutant.pdf_fill")


def fill_pdf(template_path: str, data: dict, output_path: str) -> str:
    """Fill an AcroForm PDF with the given field values.

    Args:
        template_path: path to the blank PDF (with form fields)
        data: dict of {field_name: value} matching the PDF's field names
        output_path: where to write the filled PDF

    Returns:
        The output path on success.

    Raises:
        FileNotFoundError if the template is missing.
        ValueError if no AcroForm fields are present in the template.
    """
    template = Path(template_path)
    if not template.exists():
        raise FileNotFoundError(
            f"Blank PDF not found: {template_path}. "
            f"Run: python scripts/download_corpus.py"
        )

    reader = PdfReader(str(template))

    # Many Army forms ship "encrypted" with an empty owner password — pypdf
    # refuses to clone them until decrypted, even though no real password
    # is set. Try the empty-string decrypt; it's a no-op if not encrypted.
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as e:
            log.warning(f"PDF decrypt with empty password failed ({e}); attempting clone anyway")

    writer = PdfWriter(clone_from=reader)

    fields = reader.get_fields() or {}
    if not fields:
        raise ValueError(
            f"{template_path} has no AcroForm fields. "
            f"Some Army PDFs are flat scans — re-extract schema or use overlay mode."
        )

    log.info(f"Available PDF fields: {list(fields)[:8]}{'...' if len(fields) > 8 else ''}")

    # Convert all values to strings; PDF field values must be strings.
    str_data = {k: ("" if v is None else str(v)) for k, v in data.items()}

    for page in writer.pages:
        writer.update_page_form_field_values(page, str_data)

    # Mark fields as needing a re-render so values display in viewers.
    if "/AcroForm" in writer._root_object:
        writer._root_object["/AcroForm"].update(
            {NameObject("/NeedAppearances"): BooleanObject(True)}
        )

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "wb") as f:
        writer.write(f)

    log.info(f"Wrote filled PDF: {out}")
    return str(out)
