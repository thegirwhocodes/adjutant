"""Extract the XFA template + datasets from a DA/DD form PDF and emit a
small JSON layout file we can render in reportlab.

Why: armypubs' DA-31 and DA-4856 are XFA-only PDFs. Non-Adobe viewers show
"Please wait..." because the page-content stream is just a loading stub —
the actual form lives in pdf.Root.AcroForm.XFA, an array of XML packets.
We extract the `template` packet, walk every <draw>, <field>, <subform>,
<area>, and emit a JSON file with each field's:
   - x, y, w, h          (in PDF points, origin bottom-left)
   - kind                ("draw" / "field" / "subform")
   - name                (XFA SOM name, used to bind data)
   - caption             (human-readable label drawn on the page)
   - font                (typeface + size from <font>)
   - bind / value        (default static text, used by <draw>)
This makes pdf_fill.py a generic walker rather than a hand-coded layout per
form. Same script works for any future XFA form.

Usage:  python scripts/extract_xfa_template.py forms/da_31_blank.pdf

Outputs:
   forms/<basename>_template.xml      — raw XFA template XML, for debugging
   forms/<basename>_layout.json       — flattened, rendered-ready geometry
"""
import argparse
import json
import re
import sys
from pathlib import Path

import pikepdf
from lxml import etree

XFA_NS = "http://www.xfa.org/schema/xfa-template/3.3/"
NSMAP = {"x": XFA_NS}

# Adobe LiveCycle Designer authored DA-31 against XFA 3.3 and DA-4856 against
# XFA 3.6. The element shapes are identical; only the namespace URI differs.
# We do all lookups by localname so the extractor works for any XFA version.

def _find_local(node, localname):
    """Return the first direct child whose localname matches (any namespace)."""
    for child in node:
        if isinstance(child.tag, str) and etree.QName(child.tag).localname == localname:
            return child
    return None


def _findall_local(node, localname, recursive=False):
    """All elements with this localname. recursive=True searches descendants."""
    out = []
    iterator = node.iter() if recursive else node
    for child in iterator:
        if isinstance(child.tag, str) and etree.QName(child.tag).localname == localname:
            if child is not node:
                out.append(child)
    return out


# ---- unit parsing -------------------------------------------------------

# XFA expresses coordinates in mixed units: "180mm", "2.5in", "144pt", "12px".
_UNIT_RE = re.compile(r"^(-?\d*\.?\d+)\s*([a-zA-Z]*)$")
_PT_PER = {
    "pt": 1.0,
    "in": 72.0,
    "mm": 72.0 / 25.4,
    "cm": 72.0 / 2.54,
    "px": 0.75,         # CSS px → pt
    "":   1.0,
}


def to_points(s):
    """Convert an XFA dimension string to PDF points. Returns 0.0 on failure."""
    if s is None:
        return 0.0
    m = _UNIT_RE.match(str(s).strip())
    if not m:
        return 0.0
    val, unit = float(m.group(1)), m.group(2).lower()
    return val * _PT_PER.get(unit, 1.0)


# ---- XFA → flat layout walker ------------------------------------------

def walk(node, parent_x_pt, parent_y_pt, page_h_pt, items, depth=0):
    """Recursively walk the XFA tree, accumulating absolute coordinates.

    XFA <area>, <subform>, <draw>, <field> all carry x/y attributes that
    are RELATIVE to the parent <area>/<subform>. We resolve to absolute by
    adding the parent's offset.

    Coordinate convention: XFA y-axis points DOWN from the page's top-left.
    PDF y-axis points UP from the page's bottom-left. We flip on emit.
    """
    # Skip processing-instructions, comments, entities — only walk elements.
    if not isinstance(node.tag, str):
        return
    tag = etree.QName(node.tag).localname
    x = to_points(node.get("x")) + parent_x_pt
    y = to_points(node.get("y")) + parent_y_pt
    w = to_points(node.get("w"))
    h = to_points(node.get("h"))
    name = node.get("name", "")

    if tag in ("draw", "field"):
        caption = ""
        value = ""
        is_rule = False
        font_face = "Helvetica"
        font_size = 9.0
        font_bold = False
        font_italic = False
        align = "left"

        # Caption (rare in armypubs forms — usually empty)
        cap_node = _find_local(node, "caption")
        if cap_node is not None:
            caption = _extract_text_or_html(cap_node)

        # Default value — for <draw>: the static label / decoration. For
        # <field>: usually empty but can carry a placeholder.
        val_node = _find_local(node, "value")
        if val_node is not None:
            # Detect drawn rectangles / lines — XFA uses <rectangle>/<line>
            # inside <value> for borders and rules.
            if (_find_local(val_node, "rectangle") is not None
                    or _find_local(val_node, "line") is not None
                    or _find_local(val_node, "arc") is not None):
                is_rule = True
            else:
                value = _extract_text_or_html(val_node)

        # Font + alignment — search direct child first, then descendants.
        font_node = _find_local(node, "font")
        if font_node is None:
            for desc in node.iter():
                if isinstance(desc.tag, str) and etree.QName(desc.tag).localname == "font":
                    font_node = desc
                    break
        if font_node is not None:
            face = font_node.get("typeface")
            size = font_node.get("size")
            weight = (font_node.get("weight") or "").lower()
            posture = (font_node.get("posture") or "").lower()
            if weight == "bold":
                font_bold = True
            if posture in ("italic", "oblique"):
                font_italic = True
            if face:
                font_face = _normalize_face(face, font_bold, font_italic)
            else:
                font_face = _normalize_face("Helvetica", font_bold, font_italic)
            if size:
                font_size = to_points(size)

        para = _find_local(node, "para")
        if para is not None:
            ha = (para.get("hAlign") or "").lower()
            if ha in ("left", "center", "right", "justify"):
                align = "center" if ha == "center" else ("right" if ha == "right" else "left")

        items.append({
            "kind":    tag,
            "name":    name,
            "x":       round(x, 2),
            "y":       round(page_h_pt - y - h, 2),  # flip XFA→PDF
            "w":       round(w, 2),
            "h":       round(h, 2),
            "caption": caption,
            "value":   value,
            "font":    font_face,
            "size":    round(font_size, 2),
            "align":   align,
            "rule":    is_rule,
        })

    # Recurse into containers — coords there are relative to this node's (x,y).
    if tag in ("subform", "area", "pageArea", "contentArea"):
        for child in node:
            walk(child, x, y, page_h_pt, items, depth + 1)
    else:
        # Even for <draw>/<field> recurse — some have nested <draw> for
        # decoration like horizontal rules.
        for child in node:
            walk(child, x, y, page_h_pt, items, depth + 1)


# Map XFA typeface names to reportlab built-in faces. Reportlab ships with
# Helvetica, Helvetica-Bold, Helvetica-Oblique, Helvetica-BoldOblique,
# Times-Roman variants, Courier variants. Anything fancier renders as plain
# Helvetica unless we register a TTF.
def _normalize_face(face: str, bold: bool = False, italic: bool = False) -> str:
    f = face.strip().lower()
    is_serif = "times" in f or "serif" in f
    is_mono  = "courier" in f or "mono" in f or "menlo" in f
    base = "Times-Roman" if is_serif else ("Courier" if is_mono else "Helvetica")
    # Embedded markers in the face name override the explicit booleans.
    if "bold" in f: bold = True
    if "italic" in f or "oblique" in f: italic = True
    if base == "Helvetica":
        if bold and italic: return "Helvetica-BoldOblique"
        if bold:            return "Helvetica-Bold"
        if italic:          return "Helvetica-Oblique"
        return "Helvetica"
    if base == "Times-Roman":
        if bold and italic: return "Times-BoldItalic"
        if bold:            return "Times-Bold"
        if italic:          return "Times-Italic"
        return "Times-Roman"
    if base == "Courier":
        if bold and italic: return "Courier-BoldOblique"
        if bold:            return "Courier-Bold"
        if italic:          return "Courier-Oblique"
        return "Courier"
    return "Helvetica"


def _extract_text_or_html(node) -> str:
    """Extract human-readable text from either an XFA <text> child OR an
    <exData contentType="text/html"><body>...</body></exData> child.
    Namespace-agnostic — works for XFA 3.3 (DA-31) and 3.6 (DA-4856).
    """
    if node is None:
        return ""
    # Plain <text>
    text_node = _find_local(node, "text")
    if text_node is not None and text_node.text:
        return _normalize_ws(text_node.text)
    # Rich <exData> HTML body
    exdata = _find_local(node, "exData")
    if exdata is not None:
        parts = []
        for desc in exdata.iter():
            if isinstance(desc.tag, str) and desc.text:
                parts.append(desc.text)
            if desc.tail:
                parts.append(desc.tail)
        return _normalize_ws("".join(parts))
    return ""


_WS_RE = re.compile(r"\s+")


def _normalize_ws(s: str) -> str:
    return _WS_RE.sub(" ", s.replace(" ", " ").replace(" ", " ")).strip()


# ---- per-page driver ---------------------------------------------------

def extract_pages(template_root):
    """XFA documents authored by Adobe LiveCycle Designer (DA-31, DA-4856,
    every armypubs PDF) follow a consistent shape:

        <template>
          <subform name="form1">          ← document
            <pageSet>...</pageSet>         ← page chrome / size hints
            <subform name="Page1" w=… h=…>  ← real content lives here
              <draw> <field> <subform> ...
            </subform>
            <subform name="Page2" w=… h=…>
              ...
            </subform>
            <proto/> <desc/> <variables/> <event/>
          </subform>
        </template>

    The page-sized inner subforms (those with `w` AND `h` attributes set
    to a page-sized dimension) are where every <draw> and <field> lives.
    The <pageSet> is just media-box metadata.
    """
    pages = []

    # Find every page-sized subform anywhere in the template.
    candidates = []
    seen = set()
    for sf in template_root.iter():
        if not isinstance(sf.tag, str):
            continue
        if etree.QName(sf.tag).localname != "subform":
            continue
        w_attr = sf.get("w")
        h_attr = sf.get("h")
        if not (w_attr and h_attr):
            continue
        w_pt = to_points(w_attr)
        h_pt = to_points(h_attr)
        # Page-sized: at least 5 inches in each axis.
        if w_pt < 360 or h_pt < 360:
            continue
        # Avoid nested page-sized subforms.
        ancestor_is_page = any(id(a) in seen for a in sf.iterancestors())
        if ancestor_is_page:
            continue
        seen.add(id(sf))
        candidates.append((sf, w_pt, h_pt))

    if not candidates:
        # Fallback — no page-sized subform. Walk the whole template with
        # a US Letter page assumption.
        items = []
        walk(template_root, 0, 0, 792.0, items)
        return [{"width": 612.0, "height": 792.0, "items": items}]

    for sf, w_pt, h_pt in candidates:
        # The page subform itself has x/y of 0 most of the time. If it
        # carries an offset, treat that as the page-content origin.
        sf_x = to_points(sf.get("x"))
        sf_y = to_points(sf.get("y"))
        items = []
        for child in sf:
            if not isinstance(child.tag, str):
                continue
            walk(child, sf_x, sf_y, h_pt, items)
        pages.append({"width": w_pt, "height": h_pt, "items": items})

    return pages


# ---- main --------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", help="path to a blank XFA-only PDF")
    ap.add_argument("--out-dir", default="forms")
    args = ap.parse_args()

    pdf_path = Path(args.pdf)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(exist_ok=True)
    base = pdf_path.stem.removesuffix("_blank")

    print(f"[extract] {pdf_path}")
    with pikepdf.open(str(pdf_path)) as pdf:
        af = pdf.Root.AcroForm
        if "/XFA" not in af:
            sys.exit("no XFA in this PDF — nothing to extract")
        xfa = af.XFA
        if isinstance(xfa, pikepdf.Array):
            packets = {}
            for i in range(0, len(xfa), 2):
                packets[str(xfa[i])] = bytes(xfa[i + 1].read_bytes())
        else:
            packets = {"template": bytes(xfa.read_bytes())}

    template_xml = packets.get("template")
    if not template_xml:
        sys.exit("no `template` packet found")

    # Save raw XML for debugging.
    raw_xml_path = out_dir / f"{base}_template.xml"
    raw_xml_path.write_bytes(template_xml)
    print(f"[extract]   raw template → {raw_xml_path} ({len(template_xml):,} bytes)")

    # Parse + walk.
    parser = etree.XMLParser(remove_blank_text=False, ns_clean=True, recover=True)
    root = etree.fromstring(template_xml, parser=parser)

    pages = extract_pages(root)
    total_items = sum(len(p["items"]) for p in pages)
    print(f"[extract]   {len(pages)} pages, {total_items} drawable items")
    for i, p in enumerate(pages):
        kinds = {}
        for it in p["items"]:
            kinds[it["kind"]] = kinds.get(it["kind"], 0) + 1
        print(f"[extract]     page {i+1}: {p['width']:.0f} × {p['height']:.0f} pt — {kinds}")

    layout_path = out_dir / f"{base}_layout.json"
    layout_path.write_text(json.dumps({
        "source":    str(pdf_path),
        "form_id":   base.upper().replace("_", "-"),
        "pages":     pages,
    }, indent=2))
    print(f"[extract]   layout → {layout_path} ({layout_path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()