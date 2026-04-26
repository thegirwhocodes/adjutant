# PDF / XFA Form-Fill Engines — Adjutant Deep Dive

**Compiled:** 2026-04-26 (SCSP Hackathon Boston, day 2)
**Question that triggered this:** "Can we somehow run Adobe Acrobat at the back end of the website to fill in any kind of form?"
**Companion docs:** [VOICE_PIPELINE_PLAN.md](VOICE_PIPELINE_PLAN.md), [VOICE_LATENCY_DOSSIER.md](VOICE_LATENCY_DOSSIER.md)

---

## TL;DR

**No, you cannot legally run Adobe Acrobat headless server-side on a Mac to fill XFA forms.** Acrobat is GUI-bound, requires a logged-in user, and Adobe's EULA forbids server use. The only Adobe products licensed for true server-side dynamic-XFA fill are **Adobe Experience Manager Forms** (formerly LiveCycle) and **Adobe PDF Services API** — both are commercial, both start in the five-figure-per-year range, and neither is offline.

There is no battle-tested 100% open-source, offline, Apple-Silicon-native dynamic-XFA renderer in 2026. Every $0 OSS option (PDFBox, mutool, qpdf, pdf-lib, PDF.js) can *populate* the XFA XML stream but cannot *render* the form layout to flat PDF — so the resulting file still shows "Please wait..." in Chrome / Safari / Preview / pypdfium2.

**The only fully-offline, $0, M2-native, demo-grade path is to give up on rendering the Army's XFA template and rebuild the form layout ourselves in reportlab — which is exactly what `adjutant/pdf_fill.py` already does for DA-31 and DA-4856.**

The polish opportunity below the fold: **extract the XFA template once, drive reportlab from its `<rect>`, `<font>`, `<caption>` data programmatically.** Pixel-faithful clone of the Army's actual layout, generated from the Army's own XML, $0, offline, M2-native. ~6–10 hours of build time per form (only the first form is hard).

---

## 0. Why this matters for Adjutant

The blank PDFs in [forms/](../forms/) are 1-page PDF-1.7 zip-deflate-encoded files from armypubs.army.mil:

```
da_31_blank.pdf      PDF document, version 1.7, 1 pages (zip deflate encoded)
da_4856_blank.pdf    PDF document, version 1.7, 1 pages (zip deflate encoded)
dd_1351_2_blank.pdf  PDF document, version 1.7         (zip deflate encoded)
```

DA-31 and DA-4856 are **XFA-only** — page contents are a "Please wait…" loading stub and the real form lives in `pdf.Root.AcroForm.XFA`. DD-1351-2 is a real AcroForm with widget rectangles (already extracted in [forms/dd_1351_2_widgets.json](../forms/dd_1351_2_widgets.json)). The current [adjutant/pdf_fill.py](../adjutant/pdf_fill.py) (3 strategies: `rect-overlay`, `flat-rebuild`, `acroform-fill`) sidesteps XFA entirely by hand-drawing DA-31 and DA-4856 in reportlab — visually decent, structurally invented.

A soldier downloading any of these PDFs needs to print, sign, and route through chain of command. That's the contract.

---

## 1. Running real Adobe Acrobat headless server-side

### 1.1 Adobe Acrobat Pro CLI on macOS

Acrobat is GUI-bound. From Adobe's automation guidelines and community threads:

> "Adobe Acrobat is a GUI-based application and does not support full background execution for printing tasks via shell scripts or Task Scheduler when no user session is active. … Adobe does not permit automation of Acrobat through scripts or RPA unless initiated by a user and executed on the same machine with the GUI available."

You can run Acrobat's JavaScript engine (`/Acrobat/JavaScripts/*.js`, `app.execMenuItem`, `Doc.exportAsFDF`) under a logged-in user via AppleScript `do shell script "open -a 'Adobe Acrobat'"`, but you cannot run it as a daemon, you cannot run it for a non-interactive user, and Adobe's EULA explicitly forbids server use.

**Verdict:** non-starter for a hackathon demo.

### 1.2 Adobe Acrobat SDK (the C++ one)

Free download for registered Adobe developers (Acrobat SDK 2020 / DC SDK), but it requires an installed copy of Acrobat Pro on the same machine and is licensed for **plugins** to Acrobat, not for stand-alone server use. The full server-class redistribution is gated behind LiveCycle / AEM Forms commercial agreements.

### 1.3 Adobe LiveCycle / AEM Forms

XFA's original engine — renders dynamic XFA to flat PDF and HTML5. Adobe Experience Manager Forms is offered as on-premises or cloud-managed.

Adobe does not publish list prices. From community / reseller chatter:
- A 2019 forum post pegged "AEM Forms Manager Pro Server that renders HTML documents" at "**at least $84K per year** to license."
- 2026 Brainvire cost guide ([brainvire.com](https://www.brainvire.com/blog/adobe-aem-cost-breakdown-us-enterprises/)): "AEM is offered both as an on-premise solution or through managed services in the cloud" and "Adobe will provide numbers based on a private quote tailored to specific business factors, which are what separate a six-figure deal from a multi-million-dollar commitment."

**Verdict:** ~$84K/year minimum, six-figure most likely. Out of scope.

### 1.4 Adobe Acrobat Reader DC headless

`AcroRd32.exe /t "path_to_pdf" "printer_name" "Adobe PDF" "printer_port"` — **Windows only**, requires a logged-in user session, dialog-suppression flags `/s /o /h /n`. Apryse's own virtual-printer flow uses exactly this pipeline:

```
C:\Program Files (x86)\Adobe\Acrobat Reader DC\Reader\AcroRd32.exe /t C:\path\to\xfa\document.pdf "PDFTronPDFNet"
```

dumps an XPS file at `C:\Users\%USERNAME%\AppData\Local\Temp\pdfnet.xps`, then converts XPS → PDF using the Apryse SDK. **Not a Mac/Linux option without a VM.**

### 1.5 Apple Preview AppleScript / `osascript`

Preview has no XFA renderer. Filling AcroForm fields via Preview's scripting bridge is undocumented and Preview shows the same "Please wait…" stub that pypdfium2 shows for XFA. Dead end.

### 1.6 Adobe Document Generation API / Adobe PDF Services API

Cloud REST API. From [developer.adobe.com/document-services/pricing](https://developer.adobe.com/document-services/pricing/main/):

> "Adobe PDF Services API licensing is measured per Document Transaction, which is based on the initial endpoint request and the digital output."
>
> "Adobe offers pay-as-you-go and volume pricing plans, with a free tier of 500 Document Transactions available for 6 months."

Form-data import/export endpoints exist for "AcroForm/Static XFA" — note: **static XFA only**. Dynamic XFA (which DA-31 is) is not in the documented endpoint list. Anecdotally one user got a quote of "approximately **$55,000 or more per year** for enterprise usage" (Adobe community thread, early 2024).

Adobe Sign (server-side fill + signature) is a separate product, also cloud, also subscription. No offline mirror.

---

## 2. Drop-in alternative engines that handle XFA

### 2.1 Apache PDFBox 3.0.7 (Java)

Free, Apache 2.0. Java 8+. M2-friendly via OpenJDK or Temurin.

```bash
# Maven
<dependency>
  <groupId>org.apache.pdfbox</groupId>
  <artifactId>pdfbox</artifactId>
  <version>3.0.7</version>
</dependency>
# or via brew on M2
brew install --cask temurin
curl -O https://repo.maven.apache.org/maven2/org/apache/pdfbox/pdfbox-app/3.0.7/pdfbox-app-3.0.7.jar
```

PDFBox exposes a `PDXFA` class on `PDAcroForm`. You can read the XFA XML byte stream, mutate the DOM, and write it back:

```java
PDDocument doc = Loader.loadPDF(new File("da_31_blank.pdf"));
PDAcroForm form = doc.getDocumentCatalog().getAcroForm();
PDXFA xfa = form.getXFA();
byte[] xml = xfa.getBytes();
// parse with javax.xml DOM, set <field><value><text>…</text></value></field>
COSStream cosout = doc.getDocument().createCOSStream();
try (OutputStream os = cosout.createOutputStream()) { os.write(modifiedXml); }
form.setXFA(new PDXFA(cosout));
doc.save("filled.pdf");
```

**Caveat that kills it:** PDFBox does **not render** the XFA. It just modifies the embedded XML. The output PDF still has the "Please wait…" stub on the page-content stream, so Chrome/Safari/Preview still won't show anything. PDFBox can fill an *AcroForm* and regenerate appearance streams, but it does *not* know how to lay out an XFA template into PDF graphics primitives.

PDFBox 3.0 release notes (PDFBOX-2857, PDFBOX-2858) only fixed "saving XFA documents caused extended-features prompts" — cosmetic, not rendering.

**Verdict:** useful for *populating* the XFA so a downstream Adobe Reader will render it correctly. Not a self-contained solution.

### 2.2 iText 7 + pdfXFA add-on

The right-shaped product, but commercial.

From [itextpdf.com/products/flatten-pdf-pdfxfa](https://itextpdf.com/products/flatten-pdf-pdfxfa):
> "pdfXFA is an iText Core add-on for Java and C# (.NET) that allows you to flatten dynamic XFA forms to static PDF and add a digital signature as additional security for further processing in PDF workflows or for archiving."
>
> "pdfXFA is the successor of XFA Worker, offering all of the same functionality but with a more convenient API."

Licensing:
- iText Core itself is dual-licensed: **AGPL** (open source, copyleft — your whole codebase becomes AGPL) **or commercial**.
- pdfXFA is **commercial-only**.
- Free 30-day trial of "the entire iText Suite".

Pricing is "request a quote" — historically iText commercial licenses for a single-server deployment have been $5K–$15K/year, and pdfXFA is an additional add-on cost on top.

```java
PdfDocument pdf = new PdfDocument(new PdfReader("da_31_blank.pdf"), new PdfWriter("out.pdf"));
XFAFlattener flattener = new XFAFlattener();
flattener.flatten(pdf);
pdf.close();
```

**Verdict: the working server-side flattener for dynamic XFA.** AGPL only safe if Adjutant's whole codebase goes AGPL; commercial license is real money.

### 2.3 Aspose.PDF for Python

Commercial, mature, M2-friendly via `aspose-pdf` PyPI package.

```bash
pip install aspose-pdf
```

XFA-to-AcroForm conversion ([docs.aspose.com/pdf/python-net/xfa-forms](https://docs.aspose.com/pdf/python-net/xfa-forms/)):

```python
import aspose.pdf as ap

with ap.Document("DynamicXFAToAcroForm.pdf") as document:
    document.form.type = ap.forms.FormType.STANDARD
    document.save("StandardAcroForm_out.pdf")
```

Variant with `ignore_needs_rendering`:
```python
with ap.Document(path_infile) as document:
    if not document.form.needs_rendering and document.form.has_xfa:
        document.form.ignore_needs_rendering = True
    document.form.type = ap.forms.FormType.STANDARD
    document.save(path_outfile)
```

Filling: `Document.Form.XFA.set_field_value(name, value)` then `document.save(...)`. Flattening: `Facades.Form.flatten_all_fields()`.

**Licensing/limits:** evaluation mode prints "Evaluation Only. Created with Aspose.PDF" watermark on every page. **30-day temporary license available on request.** Production licenses are per-developer-seat OEM or per-server, typically **$1,500–$3,000 per developer** for Aspose.PDF.

Aspose.PDF Cloud — alternative route — has a free tier of **150 API calls per month** at [purchase.aspose.cloud/pricing](https://purchase.aspose.cloud/pricing/).

### 2.4 PDFTron / Apryse

Commercial, Linux/macOS/Windows binaries, including arm64 macOS (M2). Python bindings.

From [docs.apryse.com](https://docs.apryse.com/documentation/linux/faq/xfa-lifecycle-support/): "the PDFTron SDK does not natively support Dynamic XFA forms because it has been deprecated in the latest PDF specification." Their workaround is the **Windows-only** Virtual Printer flow.

Pricing ([apryse.com/pricing](https://apryse.com/pricing)): "entry-level pricing starts at $1,500" with custom quotes. [Vendr](https://www.vendr.com/marketplace/apryse) chatter: developer seats $3,000–$5,000/year, server licenses $15,000–$30,000/year.

### 2.5 Foxit PDF SDK

From [developers.foxit.com/products/mac](https://developers.foxit.com/products/mac/): "Foxit PDF SDK supports 7 platforms including Mac" and "The SDK can create, edit and fill PDF (AcroForms and XFA) forms programmatically. … full XFA form support was introduced in version 6.2 for Android and iOS."

Pricing: not publicly listed, "starting from $1.00 with a one-time license model and a free trial available" per third-party aggregators — quote-on-request, real prices commensurate with Apryse.

### 2.6 PSPDFKit / Nutrient

Linux-container Server SDK at [nutrient.io](https://www.nutrient.io/). Docker-based. From the docs: "PSPDFKit Server is a Linux-based container and requires a Docker environment capable of running Linux containers."

Their feature matrix lists "form filling" (AcroForm). XFA is not explicitly listed in the public docs as supported. Pricing is enterprise-quote.

### 2.7 qpdf

Pure-C++, MIT-licensed, brew-installable: `brew install qpdf`. qpdf can structure-rewrite, decrypt, linearize, and run `--qdf` to produce a human-readable form of a PDF. qpdf does **not** render content streams and does **not** fill forms. Useful only for inspection: `qpdf --qdf da_31_blank.pdf - | less` lets you grep the XFA XML.

### 2.8 PDFtk Server

Confirmed dead-end for dynamic XFA. From the Drupal fillpdf issue thread: "pdftk is listed among PDF software without full XFA support, and pdftk cannot fill dynamic forms. Flattening only works with AcroForms; dynamic XFA forms require special handling."

PDFtk's only XFA option is `drop_xfa` — strips XFA so AcroForm fields take over, which is what we already attempted via pikepdf and which doesn't help because the underlying page content streams are still the "Please wait…" stub.

### 2.9 podofo / podofopp

C++ library. AcroForm fill works. No XFA layout engine. Same limitation as PDFBox.

### 2.10 MuPDF / mutool

`brew install mupdf`. `mutool form -F` can flatten **AcroForm** fields. No dynamic-XFA layout. mutool treats XFA streams as opaque XML and renders only the underlying page-content stream — so DA-31 still shows "Please wait…".

### 2.11 SumatraPDF

Windows-only binary; works under Wine on Mac/Linux. SumatraPDF uses MuPDF as its rendering engine — same XFA limitations. Ruled out.

### 2.12 Datalogics PDF Forms Flattener

From [pdfa.org](https://pdfa.org/datalogics-announces-pdf-forms-flattener-40-with-support-for-linux/) and [datalogics.com/flatten-pdf-forms](https://www.datalogics.com/flatten-pdf-forms): "PDF Forms Flattener is a standalone command-line interface tool that can flatten XFA and AcroForm documents."

Datalogics' Adobe PDF Library is the **Adobe-licensed C++ engine itself**, so it actually renders dynamic XFA. Linux build exists since 4.0. License: annual or multi-year, no public pricing, expect five figures.

### 2.13 SetaPDF-FormFiller (PHP)

Verbatim pricing from [setasign.com/products/setapdf-formfiller/pricing](https://www.setasign.com/products/setapdf-formfiller/pricing/):

**Lite** (text fields only):
- Server license: 300 EUR / license
- Cluster/Cloud: 450 EUR / 1-3 nodes, scaling up

**Full** (all field types):
- Server license: 600 EUR / license
- Cluster/Cloud: 900 EUR / 1-3 nodes, up to 2,700 EUR / 1-15

> "All licenses include updates for one year. After one year, the access to updates can be renewed for an annual fee of 20% of the current license price."

```php
$xfa = $formFiller->getXfa();
if ($xfa->isDynamic()) {
    $xfa->setData('<form1><TextField1>a new value in TextField1</TextField1></form1>');
}
```

**Caveat:** "Dynamic forms cannot be flattened and lack AcroForm field access." So SetaPDF can *populate* dynamic XFA but cannot *render it to flat PDF* — same trap as PDFBox. The PHP requirement also forces a second runtime alongside FastAPI.

### 2.14 Qoppa jPDFFields (deprecated)

From [kbdeveloper.qoppa.com](https://kbdeveloper.qoppa.com/livecycle-dynamic-xfa-forms/): "Qoppa will not be adding support for LiveCycle XFA forms rendering as they are being discontinued." Discontinued.

---

## 3. Browser-side / JavaScript engines

### 3.1 Mozilla PDF.js

XFA support landed in Firefox 93 (October 5, 2021). Feature flag `pdfjs.enableXfa` in `about:config`. Status from [github.com/mozilla/pdf.js/issues/13508](https://github.com/mozilla/pdf.js/issues/13508) and #14249: **experimental at best**.

> "Some parts render OK, but the more complex parts look really strange, with warnings like 'XFA - Invalid reference'."
> "XFA form saved using pdf.js cannot be re-opened with pdf.js — 'Warning: XFA Foreground documents are not supported'."

Chrome's built-in PDF viewer is a *fork* of an earlier PDF.js without XFA enabled, so DA-31 *still* shows "Please wait…" in Chrome even in 2026. Safari uses PDFKit (Preview), which has zero XFA support.

### 3.2 pdf-lib / react-pdf / pdfme / muhammara / HummusJS

From [pdf-lib.js.org](https://pdf-lib.js.org/docs/api/classes/pdfform): "pdf-lib does not support creation, modification, or reading of XFA fields."

None of these claim XFA support.

### 3.3 FormVu (IDR Solutions)

Commercial Java tool that converts XFA → standalone HTML5. From IDR Solutions blog: "FormVu converts Acrobat forms into standalone HTML5. Dynamic XFA forms contain JavaScript which needs some considerable tidying up." Quote-on-request pricing, mid-five-figure per server typical.

### 3.4 PixelPirate69420/XFA_to_HTML

A GitHub script ([github.com/PixelPirate69420/XFA_to_HTML](https://github.com/PixelPirate69420/XFA_to_HTML)): "Full pipeline to extract XFA (XML Forms Architecture) content from PDF files and convert it into HTML with working UI elements and JavaScript logic." Hobby-tier, quality unknown. Worth a 30-minute spike if all else fails — could become Adjutant's web preview path.

---

## 4. The "render-then-flatten" workaround pattern

The classic enterprise hack: use Adobe's own engine (Reader, AEM Forms, LiveCycle) as a black box to render dynamic XFA → flat PDF, then post-process.

**Adobe Reader on Linux via Wine + xvfb**: theoretically possible (acrordrdc snap from [snapcraft.io/acrordrdc](https://snapcraft.io/acrordrdc)), but Adobe officially discontinued Reader for Linux in June 2013. Last native Linux version is 9.5.5 (32-bit binary from 2013 with known CVEs). Plus Adobe's EULA forbids server use.

**Apryse's Virtual Printer (Windows-only):** documented as the official workaround. Pipes Adobe Reader → "PDFTronPDFNet" virtual printer → XPS file → SDK converts XPS to PDF. **Will not run on M2 macOS at all.** A Windows VM is required.

**Foxit Reader CLI on macOS:** known to hang in memory after batch print jobs (Foxit forum), making it unreliable for unattended automation.

**For Adjutant's M2 + offline + 24-hour constraint:** Wine-Reader is too fragile, Apryse Virtual Printer needs Windows, Foxit hangs. None of the render-then-flatten options are demo-grade for Sunday.

---

## 5. The "extract XFA template, render ourselves" pattern

XFA inside a PDF is XML. From [Wikipedia on XFA](https://en.wikipedia.org/wiki/XFA):

> "XFA is stored as a set of XML streams inside traditional PDF Objects (so they can be encrypted and compressed). … The parts of an XFA form are comprised of a set of XML documents such as a preamble, postamble, configuration, templates, and dataset."

Location: `pdf.Root.AcroForm.XFA` — either a stream or an array of `[name, stream, name, stream, …]` packets.

### Extraction tools

**pikepdf** can read raw bytes:
```python
import pikepdf
pdf = pikepdf.open("da_31_blank.pdf")
xfa = pdf.Root.AcroForm.XFA
# xfa is either a stream or an array of (name, stream)
if isinstance(xfa, pikepdf.Array):
    packets = {str(xfa[i]): bytes(xfa[i+1].read_bytes()) for i in range(0, len(xfa), 2)}
    template_xml = packets.get("template")
else:
    template_xml = bytes(xfa.read_bytes())
```

Other extractors:
- **pdftk** `dump_data_fields_utf8` — AcroForm metadata only
- **mutool extract** — embedded streams, no XFA-specific handling
- **qpdf --qdf** — `qpdf --qdf da_31_blank.pdf decoded.pdf` produces a readable form
- **[pdf-xfa-tools](https://github.com/AF-VCD/pdf-xfa-tools)** — Python toolkit. Three scripts: `xfa-extract.py`, `deco-unlock.py`, `xfaTools.py`. Deps: BeautifulSoup, pikepdf
- **[xfa-tools](https://github.com/nmbooker/xfa-tools)**: extracts XFA as JSON pairs
- **[opentaxforms](https://pypi.org/project/opentaxforms/)**: "extracts the XFA from each PDF form, and parses out relationships among fields and math formulas" — IRS-targeted but parser is general

### Rendering the extracted template ourselves

**Apache FOP** (XSL-FO renderer): XFA's `<template>` element has surface similarity to XSL-FO but is *not* a strict subset — XFA uses its own layout primitives (`<subform>`, `<exclGroup>`, `<area>`) and has bind/calc/event semantics XSL-FO can't express. Practical XFA → XSL-FO → PDF via FOP is not a published path.

**PrinceXML / WeasyPrint:** HTML/CSS only, no XFA.

So **"render the XFA ourselves" reduces to: write a script that walks the XFA `<template>` XML, maps each `<draw>`/`<field>` to reportlab primitives, and uses the bound `<dataset>` values.** This is essentially **what Adjutant's `_build_da_31` and `_build_da_4856` already do, except hand-coded** instead of programmatically generated from the XFA.

### Concrete upgrade path

In `adjutant/scripts/`, add `extract_xfa_template.py`:
1. Open `forms/da_31_blank.pdf` with pikepdf
2. Walk `pdf.Root.AcroForm.XFA` (handle both stream and array forms)
3. Extract the `template` packet → `forms/da_31_template.xml`
4. Parse the XML: every `<draw>` and `<field>` carries `<ui>`, `<value>`, `<font>`, `<caption>`, and an `<area>` parent with `<rect>` (x, y, w, h in mm or pt)
5. Emit a JSON layout file: `forms/da_31_layout.json` listing every box, label, and bound semantic field

Then in `adjutant/pdf_fill.py`, replace the hand-coded `_build_da_31` body with a generic `_build_from_xfa_layout(layout, data)` that walks the JSON and emits reportlab `c.rect`, `c.drawString`, `c.setFont` calls. **Same function works for DA-4856** once you generate its layout JSON.

Result: pixel-accurate reproductions of the real Army form, drawn from the XFA template the Army actually ships. Renders everywhere because output is plain page-content streams.

This is **the only fully offline, $0, M2-native, 24-hour-feasible path** to "real-looking" output for any government XFA. Coding effort: ~6–10 hours per form for a pixel-faithful clone, but only the first form is hard — once you have the parser, the second form is mostly data.

---

## 6. The "find a non-XFA version of the same form" pattern

DA-31 and DA-4856: official source is `armypubs.army.mil`. Their canonical files are XFA. Search results show many third-party rehosts (pdfFiller, TemplateRoller, PDF Guru, USLegalForms, PDFRun, FormSwift, CocoDoc, DocHub, Wondershare PDFelement, airSlate signNow, ArmyWriter), but these are all flatten-then-rebuild as AcroForm by the third-party — none are official, none can be cited as the authoritative form, and using them on a hackathon demo of a "fully offline" pipeline defeats the offline requirement.

Official armypubs eForm URL pattern: `https://armypubs.army.mil/pub/eforms/DR_a/ARN36501-DA_FORM_31-000-EFILE-1.pdf` — that's the XFA file.

There is also an AEM-rendered variant in the wild: `dod.hawaii.gov/hiarng/files/2026/01/A4.-RCMC-DA-Form-4856.pdf` is labeled "APD AEM v1.02ES" — this is an AEM Forms HTML render of the same DA-4856 — a flat PDF/A or flat AcroForm. **Worth downloading and inspecting**; if it's a flat AcroForm, our existing `acroform-fill` strategy in `pdf_fill.py` would just work on it.

DTIC, DoD Forms Management Program, MILSPEC, OPM eform mirrors: none host non-XFA versions of DA-31. The Army standardized on XFA in IPPS-A's predecessor era.

**IPPS-A** (Integrated Personnel and Pay System–Army) is the modern replacement. From [ipps-a.army.mil](https://ipps-a.army.mil/tag/da-31/): "An absence request through IPPS-A is the equivalent to filling out a legacy DA 31." IPPS-A generates a flat PDF on print — but the backend stack is not public, it's PeopleSoft (Oracle) underneath, and there's no offline mirror. **DTS** (Defense Travel System) generates DD-1351-2 internally and offers no public API.

---

## 7. Pure-Python / pip-installable XFA libraries

| Library | XFA support | Notes |
|---|---|---|
| `pikepdf` | Read/write the raw XFA stream only; no parser, no renderer | Already used by Adjutant |
| `pypdf` | Read XFA bytes; cannot fill dynamic XFA | Active fork of PyPDF2 |
| `pdfminer.six` | Extracts text and structure; no XFA-specific parser | Read-only |
| `aspose-pdf` | **Yes** — fill, flatten, XFA→AcroForm conversion | Commercial; watermarks until licensed |
| `pdf-xfa-tools` | Extract XFA XML, modify with BeautifulSoup | GitHub-only, low-level |
| `xfa-tools` | Extract XFA as JSON pairs | GitHub-only |
| `opentaxforms` | XFA parser for IRS forms | PyPI, narrow scope |
| `xfaforms`, `pyxfa`, `pdf-xfa` | **Do not exist on PyPI** | Searched, no results |
| `reportlab` | None — write-only | Used to draw flat output |
| `fpdf2` | None — write-only | Alternative to reportlab |

There is no `pip install xfa-renderer` that does the job. The closest is `aspose-pdf` (commercial) for true rendering, or `pdf-xfa-tools` + `pikepdf` + custom reportlab parser for the DIY route.

---

## 8. Industry lore — how production systems actually fill DA-31

- **IPPS-A**: PeopleSoft/Oracle backend. Renders absence requests to flat PDF on the "Print" button. Stack is not public; based on Oracle BI Publisher (formerly XML Publisher), which generates PDFs from RTF/XSL-FO templates — meaning **IPPS-A does not use the legacy XFA DA-31 at all**; it generates a fresh layout and labels it "DA-31-equivalent." This is exactly the same approach as Adjutant's current `flat-rebuild`.
- **DTS (Defense Travel System)**: closed-source DoD app, generates DD-1351-2 internally. Uses Adobe LiveCycle Forms ES on the backend per old SOWs and procurement records.
- **ID.me / VA.gov**: when these pre-fill DA-31 for veterans, they typically rebuild with Adobe Document Generation API (cloud) or AEM Forms (private cloud).
- **CamoGPT / NIPRGPT / Ask Sage** (the AI assistants Adjutant competes with): per the GenAI.mil track briefings, these mostly *don't* fill PDFs at all — they answer questions about regulations and link to the form. Form-filling is a known gap, which is part of why Adjutant has a chance at the hackathon.

---

## 9. M2 + offline + 24-hour ranked recommendation

| Rank | Path | Build hrs | Offline | License | M2 native | Robust on every gov XFA |
|---|---|---|---|---|---|---|
| 1 | **Keep current `flat-rebuild`, upgrade to XFA-template-driven reportlab** | 6–10 | Yes | $0 | Yes | High once you parse the XFA |
| 2 | **Aspose.PDF Python in evaluation mode for demo, request 30-day temp license** | 1–2 | Yes (post-install) | $0 (eval w/ watermark) → $1.5K–3K/yr | Yes | High |
| 3 | **iText 7 + pdfXFA via subprocess JVM call** | 4–6 | Yes | AGPL or commercial | Yes (JVM) | High |
| 4 | **Datalogics PDF Forms Flattener (Adobe-licensed engine)** | 2–3 | Yes | $$ commercial | Yes (Linux/Mac builds) | Highest |
| 5 | **Apache PDFBox: populate XFA XML, ship PDF that opens correctly only in Adobe Reader** | 3–4 | Yes | $0 (Apache 2.0) | Yes (JVM) | Medium — only Adobe-side |
| 6 | **pdfRest container, on-prem Docker, free trial** | 2–3 | Yes (after pull) | 14-day free trial / $12,499/mo Standard | Yes (Docker) | High |
| 7 | **Aspose.PDF Cloud free tier (150 calls/month)** | 1 | No (online) | $0 | N/A | High |
| 8 | **Adobe PDF Services API free tier (500 transactions / 6 mo)** | 1–2 | No | $0 trial → ~$55K/yr enterprise | N/A | Medium (static XFA only) |
| 9 | **PDF.js in WebView fallback for browser preview only** | 2 | Yes | $0 (Apache 2.0) | Yes | Low — experimental |
| 10 | **AEM Forms / LiveCycle on-prem** | 40+ | Yes | ~$84K+/yr | Linux | Highest |

---

## 10. Final recommendation for the demo (under 24 hours)

**Ship Path 1 + Path 9 in parallel, fall back to Path 2 if forms multiply.**

### Primary: upgrade `flat-rebuild` to be XFA-template-driven

Already detailed in §5 above. Plain Python, plain reportlab, ~6–10 hours.

### Fallback if extractor proves brittle

```bash
pip install aspose-pdf
```

Request a 30-day temp license from [purchase.aspose.com/temporary-license](https://purchase.aspose.com/temporary-license), then:

```python
import aspose.pdf as ap

def fill_xfa(template_path, data, out_path):
    with ap.Document(template_path) as doc:
        if doc.form.has_xfa:
            doc.form.ignore_needs_rendering = True
            doc.form.type = ap.forms.FormType.STANDARD
            for field_name, value in data.items():
                doc.form.xfa.set_field_value(field_name, str(value))
            ap.facades.Form(doc).flatten_all_fields()
        doc.save(out_path)
```

90-minute integration. Risk: temp license arrives by email, may need a working day.

### Web preview path

Embed PDF.js (with `pdfjs.enableXfa = true`) in `web/index.html` solely so a soldier can *see* their filled form before hitting "Download." If PDF.js renders poorly for a specific form, fall back to rendering the reportlab output server-side and shipping a PNG preview via pypdfium2.

---

## Sources

- [Apache PDFBox](https://pdfbox.apache.org/) / [3.0 Migration Guide](https://pdfbox.apache.org/3.0/migration.html)
- [Filling XFA PDF Forms using PDFBox – Law and Software](https://www.lawandsoftware.com/blog/filling-xfa-pdf-forms-using-pdfbox/)
- [PDFBox mailing list – Fill XFA PDF using PDFBox](https://lists.apache.org/thread/xbyolrwj3vo8cjbdp9lmg55scl6tpo3d)
- [iText pdfXFA product page](https://itextpdf.com/products/flatten-pdf-pdfxfa) / [How to flatten XFA](https://itextpdf.com/en/resources/faq/technical-support/itext-7/how-flatten-xfa-pdf-form-using-pdfxfa)
- [NuGet itext7.pdfxfa 5.0.6](https://www.nuget.org/packages/itext7.pdfxfa)
- [Adobe PDF Services API pricing](https://developer.adobe.com/document-services/pricing/main/) / [Overview](https://developer.adobe.com/document-services/docs/overview/pdf-services-api/)
- [Adobe Experience Manager Forms](https://business.adobe.com/products/experience-manager/forms/aem-forms.html) / [Pricing](https://business.adobe.com/products/experience-manager/forms/pricing.html)
- [AEM cost guide 2026 — Brainvire](https://www.brainvire.com/blog/adobe-aem-cost-breakdown-us-enterprises/)
- [Mozilla PDF.js issue 13508](https://github.com/mozilla/pdf.js/issues/13508) / [14249](https://github.com/mozilla/pdf.js/issues/14249) / [2373](https://github.com/mozilla/pdf.js/issues/2373)
- [Apryse SDK](https://apryse.com/products/core-sdk) / [Pricing](https://apryse.com/pricing) / [XFA FAQ](https://docs.apryse.com/documentation/linux/faq/xfa-lifecycle-support/) / [Virtual printer guide](https://apryse.com/blog/flatten-dynamic-xfa-forms-pdf-apryse-virtual-printer)
- [Foxit PDF SDK for Mac](https://developers.foxit.com/products/mac/) / [6.2 announcement](https://developers.foxit.com/tech/announcing-pdf-sdk-6-2/)
- [PSPDFKit / Nutrient Docker docs](https://www.nutrient.io/guides/web/pspdfkit-server/deployment/choosing-docker-registry/)
- [PDFtk Server Manual](https://www.pdflabs.com/docs/pdftk-man-page/)
- [Aspose.PDF Python — XFA Forms](https://docs.aspose.com/pdf/python-net/xfa-forms/) / [Licensing](https://docs.aspose.com/pdf/python-net/licensing/) / [PyPI](https://pypi.org/project/aspose-pdf/) / [Cloud pricing](https://purchase.aspose.cloud/pricing/)
- [Datalogics Forms Flattener](https://www.datalogics.com/flatten-pdf-forms) / [4.0 Linux announcement](https://pdfa.org/datalogics-announces-pdf-forms-flattener-40-with-support-for-linux/) / [User Guide v4.4](https://docs.datalogics.com/FormsFlattener/PDFFormsFlattener.pdf) / [Adobe PDF Library](https://www.datalogics.com/adobe-pdf-library) / [Pricing](https://www.datalogics.com/pricing-and-licensing)
- [SetaPDF-FormFiller XFA manual](https://manuals.setasign.com/setapdf-formfiller-manual/xfa-forms/) / [Pricing](https://www.setasign.com/products/setapdf-formfiller/pricing/)
- [Qoppa XFA support KB](https://kbdeveloper.qoppa.com/xfa-support-in-qoppas-pdf-library-products/) / [Fill dynamic XFA Java](https://kbdeveloper.qoppa.com/fill-populate-xfa-dynamic-form-with-field-data-in-java/) / [LiveCycle XFA (deprecated)](https://kbdeveloper.qoppa.com/livecycle-dynamic-xfa-forms/)
- [pdfRest XFA to AcroForms](https://pdfrest.com/apitools/xfa-to-acroforms/) / [Pricing](https://pdfrest.com/pricing/) / [Container](https://pdfrest.com/products/pdfrest-api-toolkit-container/) / [Docker image](https://hub.docker.com/r/pdfrest/pdf-api-toolkit) / [Datalogics blog](https://www.datalogics.com/blog-pdf-rest-container-docker)
- [pdf-lib PDFForm API](https://pdf-lib.js.org/docs/api/classes/pdfform)
- [Apache FOP](https://xmlgraphics.apache.org/fop/)
- [pdf-xfa-tools README](https://github.com/AF-VCD/pdf-xfa-tools) / [xfa-extract.py](https://github.com/AF-VCD/pdf-xfa-tools/blob/master/xfa-extract.py)
- [nmbooker xfa-tools](https://github.com/nmbooker/xfa-tools)
- [opentaxforms PyPI](https://pypi.org/project/opentaxforms/)
- [pikepdf XFA discussion #526](https://github.com/pikepdf/pikepdf/discussions/526) / [Form docs](https://pikepdf.readthedocs.io/en/latest/api/form.html) / [Streams docs](https://pikepdf.readthedocs.io/en/latest/topics/streams.html)
- [XFA Wikipedia](https://en.wikipedia.org/wiki/XFA) / [XFA 3.3 spec PDF](https://pdfa.org/norm-refs/XFA-3_3.pdf) / [Apryse XFA extract](https://docs.apryse.com/documentation/web/faq/extract-xml-from-xfa/)
- [SumatraPDF print-to docs](https://github.com/sumatrapdfreader/sumatrapdf/blob/master/docs/md/Commands.md) / [Linux thread](https://forum.sumatrapdfreader.org/t/sumatrapdf-on-linux/1353)
- [acrordrdc snap (Wine Adobe Reader)](https://snapcraft.io/acrordrdc) / [Adobe Reader 9.5.5 last Linux](https://ubuntuhandbook.org/index.php/2023/04/install-adobe-reader-ubuntu-2204/)
- [DA Form 31 official armypubs](https://armypubs.army.mil/ProductMaps/PubForm/Details.aspx?PUB_ID=1020382) / [DA Form 4856](https://armypubs.army.mil/ProductMaps/PubForm/Details.aspx?PUB_ID=1026753) / [ARN36501 DA-31 EFILE](https://armypubs.army.mil/pub/eforms/DR_a/ARN36501-DA_FORM_31-000-EFILE-1.pdf)
- [HIARNG AEM-rendered DA-4856](https://dod.hawaii.gov/hiarng/files/2026/01/A4.-RCMC-DA-Form-4856.pdf)
- [IPPS-A absence request as DA-31 equivalent](https://ipps-a.army.mil/tag/da-31/) / [IPPS-A homepage](https://ipps-a.army.mil/)
- [PixelPirate69420 XFA_to_HTML](https://github.com/PixelPirate69420/XFA_to_HTML)
- [Mescius JS PDF Viewer XFA demo](https://developer.mescius.com/document-solutions/javascript-pdf-viewer/demos/viewer-features/xfa/purejs)
- [IDR Solutions PDF.js alternative for forms](https://blog.idrsolutions.com/pdf-js-alternative-for-pdf-forms/)
- [VeryUtils jpdfkit XFA flatten guide](https://veryutils.com/blog/how-to-flatten-pdf-forms-on-linux-via-php-script-and-java-pdf-toolkit/)
