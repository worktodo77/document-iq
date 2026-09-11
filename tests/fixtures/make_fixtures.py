"""Generate the synthetic fixture corpus. No client data, ever.

Run it directly or import :func:`build`. The output is deterministic given the
same libraries: every timestamp, author and ID that a container format would
otherwise fill in from the clock is pinned to :data:`FIXED_TIMESTAMP`.

The generated files are NOT committed. PDF and OOXML containers embed producer
strings and creation times that vary by library version, so committing them
would put a binary blob in the repo that a dependency bump silently invalidates
— and the whole point of the corpus is that it is reproducible from this
script. ``tests/fixtures/generated/`` is gitignored; the tests build it on
demand.

Coverage is deliberate, and the mixed native+scanned PDF is the one the §11
reuse audit flagged as untested in the original extractor.
"""

from __future__ import annotations

import csv
import hashlib
import datetime
import io
import shutil
import time
import zipfile
from pathlib import Path

FIXED_TIMESTAMP = datetime.datetime(2024, 7, 16, 9, 30, 0)
"""Pinned so a rebuild of the fixtures does not change their bytes."""

_ZIP_DATE = (2024, 7, 16, 9, 30, 0)

HERE = Path(__file__).resolve().parent
OUT = HERE / "generated"


# ---------------------------------------------------------------------------
# PDFs
# ---------------------------------------------------------------------------


def _pin_ooxml(path: Path) -> None:
    """Rewrite an OOXML file with fixed zip member timestamps.

    DOCX and XLSX are zip containers, and python-docx / openpyxl stamp each
    member with the current time. Pinning ``core_properties`` fixes the
    metadata *inside* the parts and leaves the container varying, so two builds
    of the same fixture still differ — and because the file's SHA-256 is a Doc
    ID input, that propagates into the index, the ledger and every downstream
    artifact. The corpus then looks nondeterministic when only its inputs were.

    Member ORDER is preserved. OOXML readers rely on it — rewriting these
    archives in sorted order produces a file python-docx refuses to open — so
    determinism here comes from pinning the timestamps, never from reordering.
    """
    import xml.etree.ElementTree as ET

    stamp = FIXED_TIMESTAMP.strftime("%Y-%m-%dT%H:%M:%SZ")
    _DC = "{http://purl.org/dc/terms/}"

    def pin_core(data: bytes) -> bytes:
        # Parsed, not regexed. The obvious regex over `<dcterms:created …>`
        # also matches the `dcterms:W3CDTF` inside the element's own xsi:type
        # attribute, which silently corrupts the part.
        root = ET.fromstring(data)
        for tag in ("created", "modified"):
            for el in root.iter(_DC + tag):
                el.text = stamp
        for prefix, uri in (
            ("cp", "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"),
            ("dc", "http://purl.org/dc/elements/1.1/"),
            ("dcterms", "http://purl.org/dc/terms/"),
            ("dcmitype", "http://purl.org/dc/dcmitype/"),
            ("xsi", "http://www.w3.org/2001/XMLSchema-instance"),
        ):
            ET.register_namespace(prefix, uri)
        return ET.tostring(root, encoding="UTF-8", xml_declaration=True)

    with zipfile.ZipFile(path) as zin:
        payload = [(i.filename, zin.read(i.filename)) for i in zin.infolist()]

    # openpyxl rewrites dcterms:modified with the clock at save time whatever
    # `wb.properties.modified` was set to, so the value has to be pinned after
    # the fact rather than before it.
    payload = [
        (name, pin_core(data) if name == "docProps/core.xml" else data)
        for name, data in payload
    ]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in payload:
            info = zipfile.ZipInfo(name, date_time=_ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            zout.writestr(info, data)


def _pdf_canvas(path: Path):
    from reportlab.pdfgen import canvas

    # invariant=1 is the load-bearing argument: without it reportlab stamps a
    # CreationDate and a random document ID into every PDF, so two builds of
    # the fixture corpus differ and the self-test's reported corpus hash means
    # nothing across sessions. Setting the title/author/producer below does NOT
    # achieve this, which is what an earlier comment here claimed.
    #
    # The blast radius was wider than the PDFs: 14_transmittal.eml embeds one,
    # and 10_misnamed.docx IS one under a wrong extension, so a single
    # unpinned timestamp moved six of fourteen fixture files.
    c = canvas.Canvas(str(path), invariant=1)
    c.setTitle("DocIQ synthetic fixture")
    c.setAuthor("DocIQ fixture generator")
    c.setSubject("synthetic")
    c.setProducer("DocIQ fixtures")
    return c


def _text_page(c, lines: list[str]) -> None:
    y = 760
    for line in lines:
        c.drawString(60, y, line)
        y -= 18
    c.showPage()


def _image_page(c, lines: list[str]) -> None:
    """A page whose text is DRAWN, not typed — no text layer, so it can only be
    read by OCR. Large, high-contrast, plain glyphs: the fixture must exercise
    the OCR route, not benchmark the engine."""
    from reportlab.lib.utils import ImageReader
    from PIL import Image, ImageDraw

    img = Image.new("L", (1240, 1754), 255)
    d = ImageDraw.Draw(img)
    y = 120
    for line in lines:
        # Default bitmap font scaled up: legible to OCR without shipping a TTF.
        tile = Image.new("L", (620, 40), 255)
        ImageDraw.Draw(tile).text((4, 8), line, fill=0)
        img.paste(tile.resize((1116, 72), Image.LANCZOS), (60, y))
        y += 120
    c.drawImage(ImageReader(img), 0, 0, width=595, height=842)
    c.showPage()


def native_pdf(path: Path) -> None:
    c = _pdf_canvas(path)
    _text_page(c, ["MONTHLY PROGRESS REPORT", "Period: 2024-07-01 to 2024-07-31",
                   "Prepared by: Synthetic Contractor Ltd"])
    _text_page(c, ["2. PROGRESS NARRATIVE",
                   "Piling completed 16 July 2024.",
                   "Steel erection commenced 22/07/2024."])
    c.save()


def scanned_pdf(path: Path) -> None:
    c = _pdf_canvas(path)
    _image_page(c, ["SITE INSTRUCTION 014", "DATED 2024-07-16"])
    _image_page(c, ["ISSUED TO CONTRACTOR"])
    c.save()


def mixed_pdf(path: Path) -> None:
    """Native page, scanned page, native page — the audit's untested case."""
    c = _pdf_canvas(path)
    _text_page(c, ["TRANSMITTAL 2024-07-16",
                   "Attached: one scanned instruction sheet."])
    _image_page(c, ["SITE INSTRUCTION 015", "DATED 2024-07-17"])
    _text_page(c, ["End of transmittal.",
                   "Acknowledged 18 July 2024."])
    c.save()


def mixed_content_pdf(path: Path) -> None:
    """ONE page carrying BOTH a native text layer and a >=25% image (A-24).

    ``mixed_pdf`` above is mixed PAGES within a file -- native page, scanned
    page, native page -- and never exercises the case A-24 actually amended: a
    single page whose native text layer clears ``_NATIVE_TEXT_FLOOR`` (40
    characters) sits BESIDE an embedded image covering
    ``PHOTO_MIN_IMAGE_AREA_SHARE`` (0.25) or more of the page. That page's
    image region is OCR'd separately and the result is appended after the
    native text, and the page's kind becomes ``PageKind.MIXED``.

    The text is drawn with ``c.drawString`` exactly like ``_text_page``; the
    image is built with the same PIL approach as ``_image_page`` -- large,
    high-contrast, plain glyphs, so the fixture exercises the OCR route rather
    than benchmarks the engine. Both live on ONE page (no ``c.showPage()``
    between them), and the image is sized and placed so it never overlaps the
    text drawn above it.
    """
    from reportlab.lib.utils import ImageReader
    from PIL import Image, ImageDraw

    c = _pdf_canvas(path)

    # Native text layer: a confidentiality legend plus a report letterhead,
    # comfortably over the 40-character routing floor.
    y = 760
    for line in ["CONFIDENTIALITY LEGEND: FOR INTERNAL USE ONLY",
                 "SYNTHETIC CONTRACTOR LTD MONTHLY REPORT LETTERHEAD"]:
        c.drawString(60, y, line)
        y -= 18

    # Embedded image, same construction as _image_page: large tiles resized up
    # for OCR legibility. Placed low on the page (y 40..340) so it never
    # overlaps the text drawn above (y ~706..760), and sized 530 x 300 pt --
    # 159,000 sq pt of a 612 x 792 pt letter page, i.e. ~32.8%, comfortably
    # over PHOTO_MIN_IMAGE_AREA_SHARE (0.25).
    img = Image.new("L", (1240, 700), 255)
    d = ImageDraw.Draw(img)
    yy = 80
    for line in ["NOTICE OF DELAY No 14", "APPROVED 12 MARCH 2019"]:
        tile = Image.new("L", (620, 40), 255)
        ImageDraw.Draw(tile).text((4, 8), line, fill=0)
        img.paste(tile.resize((1116, 72), Image.LANCZOS), (60, yy))
        yy += 120
    c.drawImage(ImageReader(img), 40, 40, width=530, height=300)
    c.showPage()
    c.save()


def empty_page_pdf(path: Path) -> None:
    """Page 2 is genuinely blank: no text layer and no image, so neither route
    recovers anything. It must still be page 2 of 3."""
    c = _pdf_canvas(path)
    _text_page(c, ["COVER SHEET — CONTRACT ADMINISTRATION FILE",
                   "Document dated 2024-07-16, issued to the Engineer."])
    c.showPage()  # a page with nothing on it at all
    _text_page(c, ["APPENDIX A — SCHEDULE OF ATTACHMENTS",
                   "Nothing further was appended to this transmittal."])
    c.save()


# ---------------------------------------------------------------------------
# Office / text formats
# ---------------------------------------------------------------------------


def docx(path: Path) -> None:
    import docx as _docx

    d = _docx.Document()
    d.add_paragraph("CORRESPONDENCE")
    d.add_paragraph("Dear Sir, ref. our letter of 16 July 2024.")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text = "Item"
    t.cell(0, 1).text = "Date"
    t.cell(1, 0).text = "Notice of delay"
    t.cell(1, 1).text = "2024-07-16"
    d.core_properties.created = FIXED_TIMESTAMP
    d.core_properties.modified = FIXED_TIMESTAMP
    d.core_properties.author = "DocIQ fixtures"
    d.core_properties.last_modified_by = "DocIQ fixtures"
    d.core_properties.revision = 1
    d.save(str(path))
    _pin_ooxml(path)


_WC_NS_ALL = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
    'xmlns:v="urn:schemas-microsoft-com:vml" '
    'xmlns:o="urn:schemas-microsoft-com:office:office" '
    'xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"'
)
_WC_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
_WC_TRACKED_DATE = "2018-05-06T00:00:00Z"

_WC_FOOTNOTES_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<w:footnotes ' + _WC_W + '>'
    '<w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>'
    '<w:footnote w:type="continuationSeparator" w:id="0"><w:p><w:r><w:continuationSeparator/></w:r></w:p></w:footnote>'
    '<w:footnote w:id="1"><w:p>'
    '<w:r><w:footnoteRef/></w:r>'
    '<w:r><w:t xml:space="preserve"> SALAMANDER is the footnote text.</w:t></w:r>'
    '</w:p></w:footnote>'
    '</w:footnotes>'
)

_WC_ENDNOTES_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<w:endnotes ' + _WC_W + '>'
    '<w:endnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:endnote>'
    '<w:endnote w:type="continuationSeparator" w:id="0"><w:p><w:r><w:continuationSeparator/></w:r></w:p></w:endnote>'
    '<w:endnote w:id="1"><w:p>'
    '<w:r><w:endnoteRef/></w:r>'
    '<w:r><w:t xml:space="preserve"> URCHIN is the endnote text.</w:t></w:r>'
    '</w:p></w:endnote>'
    '</w:endnotes>'
)

_WC_CHART_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
    '<c:chart><c:plotArea><c:layout/></c:plotArea></c:chart>'
    '</c:chartSpace>'
)

_WC_ALTCHUNK_HTML = b"<html><body>ALTCHUNK PLACEHOLDER CONTENT</body></html>"
_WC_OLE_BIN = b"OLE-PLACEHOLDER-BYTES-NOT-A-REAL-COMPOUND-FILE"
_WC_OLE_IMG = b"EMF-PLACEHOLDER-NOT-A-REAL-IMAGE"


def word_constructs_docx(path: Path) -> None:
    """D-50 fixture: one sentinel word per Word construct the extractor must
    (or must not) surface. See ``docs/reviews`` / the D-50 build spec for the
    full sentinel table. Built with python-docx's API where it has one and raw
    injected OOXML where it does not (content controls, tracked changes,
    fields, text box, footnotes, endnotes, altChunk, OLE object, chart,
    rendered page break) -- the same technique ``docx()`` above uses for
    determinism (``_pin_ooxml``) and the scratchpad ``build_docx.py`` showed
    for footnote/endnote part injection.

    Never client text (D-12): every sentinel is an invented animal name (or,
    for the footer stamp, an invented production prefix), chosen so none is a
    substring of another.
    """
    import docx as _docx
    from docx.oxml import parse_xml
    from docx.oxml.ns import qn
    from docx.opc.constants import RELATIONSHIP_TYPE as RT

    d = _docx.Document()
    body = d.element.body
    sect_pr = body.find(qn('w:sectPr'))

    def insert_raw(xml: str) -> None:
        # Inserted immediately before sectPr, which -- because add_paragraph
        # and add_table ALSO always land immediately before sectPr -- keeps
        # every insertion (python-docx or raw) in true call order.
        sect_pr.addprevious(parse_xml(xml))

    # 1. AARDVARK -- first body paragraph.
    d.add_paragraph("AARDVARK opens this synthetic construct fixture.")

    # 2. Table: BADGER (gridSpan 2), CARIBOU (vMerge restart), DINGO,
    #    ELAND (+ nested table holding FERRET).
    t = d.add_table(rows=2, cols=3)
    badger_cell = t.cell(0, 0).merge(t.cell(0, 1))
    badger_cell.text = "BADGER spans two grid columns."
    caribou_cell = t.cell(0, 2).merge(t.cell(1, 2))
    caribou_cell.text = "CARIBOU vertical merge restart."
    t.cell(1, 0).text = "DINGO is the first cell of row two."
    eland_cell = t.cell(1, 1)
    eland_cell.text = "ELAND paragraph beside a nested table."
    nested = eland_cell.add_table(rows=1, cols=1)
    nested.cell(0, 0).text = "FERRET is the nested table's only cell."

    # 3. GAZELLE -- paragraph after the table; one run carries a rendered
    #    page break marker.
    gazelle_p = d.add_paragraph(
        "GAZELLE follows the table and one run marks a rendered page break.")
    gazelle_p._p.append(parse_xml('<w:r ' + _WC_W + '><w:lastRenderedPageBreak/></w:r>'))

    # 4. HERON -- paragraph inside a BLOCK content control (body-level w:sdt).
    insert_raw(
        '<w:sdt ' + _WC_NS_ALL + '><w:sdtPr><w:id w:val="2001"/></w:sdtPr>'
        '<w:sdtContent><w:p><w:r><w:t>HERON is inside a block content control.'
        '</w:t></w:r></w:p></w:sdtContent></w:sdt>')

    # 5. IBEX (inline content control) + JACKAL (tracked ins) + KOALA (tracked
    #    del) + LYNX (fldSimple cached result) + MARMOT/NARWHAL (complex
    #    field) -- all in the "next paragraph" after HERON.
    p5_el = d.add_paragraph()._p
    p5_el.append(parse_xml(
        '<w:sdt ' + _WC_NS_ALL + '><w:sdtPr><w:id w:val="2002"/></w:sdtPr>'
        '<w:sdtContent><w:r><w:t>IBEX</w:t></w:r></w:sdtContent></w:sdt>'))
    p5_el.append(parse_xml(
        '<w:ins w:id="2003" w:author="DocIQ fixtures" w:date="' + _WC_TRACKED_DATE
        + '" ' + _WC_W + '><w:r><w:t>JACKAL</w:t></w:r></w:ins>'))
    p5_el.append(parse_xml(
        '<w:del w:id="2004" w:author="DocIQ fixtures" w:date="' + _WC_TRACKED_DATE
        + '" ' + _WC_W + '><w:r><w:delText>KOALA</w:delText></w:r></w:del>'))
    p5_el.append(parse_xml(
        '<w:fldSimple w:instr=" DOCPROPERTY LYNXFIELD " ' + _WC_W
        + '><w:r><w:t>LYNX</w:t></w:r></w:fldSimple>'))
    p5_el.append(parse_xml('<w:r ' + _WC_W + '><w:fldChar w:fldCharType="begin"/></w:r>'))
    p5_el.append(parse_xml(
        '<w:r ' + _WC_W + '><w:instrText xml:space="preserve"> DOCPROPERTY NARWHAL </w:instrText></w:r>'))
    p5_el.append(parse_xml('<w:r ' + _WC_W + '><w:fldChar w:fldCharType="separate"/></w:r>'))
    p5_el.append(parse_xml('<w:r ' + _WC_W + '><w:t>MARMOT</w:t></w:r>'))
    p5_el.append(parse_xml('<w:r ' + _WC_W + '><w:fldChar w:fldCharType="end"/></w:r>'))

    # 6. OCELOT -- external hyperlink; display text OCELOT, target carries
    #    PELICAN.
    p6 = d.add_paragraph()
    r_id = d.part.relate_to(
        "https://example.invalid/PELICAN", RT.HYPERLINK, is_external=True)
    p6._p.append(parse_xml(
        '<w:hyperlink r:id="' + r_id + '" ' + _WC_NS_ALL + '>'
        '<w:r><w:t>OCELOT</w:t></w:r></w:hyperlink>'))

    # 7. QUAIL anchors a text box; RAVEN is stored as BOTH mc:Choice
    #    (wps:txbx) and mc:Fallback (v:textbox), verbatim in each.
    insert_raw(
        '<w:p ' + _WC_NS_ALL + '>'
        '<w:r><w:t>QUAIL anchors the following text box.</w:t></w:r>'
        '<w:r><mc:AlternateContent>'
        '<mc:Choice Requires="wps">'
        '<w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
        '<wp:extent cx="914400" cy="914400"/>'
        '<wp:docPr id="2" name="TextBox1"/>'
        '<a:graphic><a:graphicData uri="http://schemas.microsoft.com/office/word/2010/wordprocessingShape">'
        '<wps:wsp><wps:cNvSpPr txBox="1"/>'
        '<wps:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="914400" cy="914400"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></wps:spPr>'
        '<wps:txbx><w:txbxContent><w:p><w:r><w:t>RAVEN is the text box content.</w:t></w:r></w:p></w:txbxContent></wps:txbx>'
        '<wps:bodyPr/></wps:wsp>'
        '</a:graphicData></a:graphic></wp:inline></w:drawing>'
        '</mc:Choice>'
        '<mc:Fallback>'
        '<w:pict>'
        '<v:shapetype id="_x0000_t202" coordsize="21600,21600" path="m,l,21600r21600,l21600,xe"/>'
        '<v:shape id="_x0000_s1027" type="#_x0000_t202" style="width:72pt;height:72pt">'
        '<v:textbox><w:txbxContent><w:p><w:r><w:t>RAVEN is the text box content.</w:t></w:r></w:p></w:txbxContent></v:textbox>'
        '</v:shape>'
        '</w:pict>'
        '</mc:Fallback>'
        '</mc:AlternateContent></w:r>'
        '</w:p>')

    # 8. TAPIR -- footnote reference, endnote reference and a comment range,
    #    all on one paragraph. The comment date is set explicitly (UTC-aware)
    #    because python-docx would otherwise stamp the clock at build time.
    tapir_p = d.add_paragraph(
        "TAPIR carries a footnote, an endnote and a comment range.")
    comment = d.add_comment(
        runs=tapir_p.runs,
        text="VULTURE flags this passage for review.",
        author="WALRUS Reviewer",
        initials="WR",
    )
    comment._comment_elm.date = datetime.datetime(
        2019, 1, 2, 0, 0, 0, tzinfo=datetime.timezone.utc)
    tapir_p._p.append(parse_xml('<w:r ' + _WC_W + '><w:footnoteReference w:id="1"/></w:r>'))
    tapir_p._p.append(parse_xml('<w:r ' + _WC_W + '><w:endnoteReference w:id="1"/></w:r>'))

    # 9. altChunk -- unread, disclosed (body-level element, not a paragraph).
    insert_raw('<w:altChunk r:id="rIdAltChunk" ' + _WC_NS_ALL + '/>')

    # 10. Embedded OLE object -- unread, disclosed.
    insert_raw(
        '<w:p ' + _WC_NS_ALL + '><w:r><w:object w:dxaOrig="1440" w:dyaOrig="1440">'
        '<v:shape id="_x0000_i1025" type="#_x0000_t75" style="width:15pt;height:15pt">'
        '<v:imagedata r:id="rIdOleImg" o:title=""/>'
        '</v:shape>'
        '<o:OLEObject Type="Embed" ProgID="Package" ShapeID="_x0000_i1025" '
        'DrawAspect="Icon" ObjectID="_1000000001" r:id="rIdOleObj"/>'
        '</w:object></w:r></w:p>')

    # 11. Chart -- unread, disclosed.
    insert_raw(
        '<w:p ' + _WC_NS_ALL + '>'
        '<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
        '<wp:extent cx="1828800" cy="1828800"/>'
        '<wp:docPr id="3" name="Chart1"/>'
        '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/chart">'
        '<c:chart r:id="rIdChart"/>'
        '</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>')

    # 12. ALPACA -- last body paragraph, after the altChunk, the OLE object
    #     and the chart.
    d.add_paragraph(
        "ALPACA is the last body paragraph, after the altChunk, the OLE "
        "object and the chart.")

    # 13. Two body pictures (D-50 part 9, stage 2a): one covers >= 25% of the
    # page (6in x 8in on an 8.5in x 11in page, ~51%) and must be disclosed as
    # unread; one is far below that (1in x 1in, ~1%) and must not be. Both
    # are the SAME generated one-pixel PNG -- the disclosure test is about
    # the PLACED size (wp:extent), not the stored pixel data. Placed after
    # ALPACA so no position test above moves.
    from docx.shared import Inches
    from PIL import Image

    pixel_buf = io.BytesIO()
    Image.new("RGB", (1, 1), (0, 0, 0)).save(pixel_buf, format="PNG")
    pixel_png = pixel_buf.getvalue()
    d.add_picture(io.BytesIO(pixel_png), width=Inches(6), height=Inches(8))
    d.add_picture(io.BytesIO(pixel_png), width=Inches(1), height=Inches(1))

    # ---- headers / footers -------------------------------------------------
    section = d.sections[0]
    section.different_first_page_header_footer = True
    section.header.paragraphs[0].text = "XERUS default header line."
    section.first_page_header.paragraphs[0].text = "YAK first-page header line."
    section.footer.paragraphs[0].text = "ZEBU default footer line one."
    section.footer.add_paragraph("MNFV 000777")

    d.core_properties.created = FIXED_TIMESTAMP
    d.core_properties.modified = FIXED_TIMESTAMP
    d.core_properties.author = "DocIQ fixtures"
    d.core_properties.last_modified_by = "DocIQ fixtures"
    d.core_properties.revision = 1

    buf = io.BytesIO()
    d.save(buf)
    base = buf.getvalue()

    # ---- rewrite the package: footnotes, endnotes, chart, OLE, altChunk ----
    # Same technique as ``build_docx.py``: python-docx has no API for any of
    # these parts, so they are added by hand, with their own Content_Types
    # overrides/defaults and document.xml.rels relationships.
    zin = zipfile.ZipFile(io.BytesIO(base))
    names = zin.namelist()
    ct = zin.read("[Content_Types].xml").decode("utf-8")
    rels = zin.read("word/_rels/document.xml.rels").decode("utf-8")

    pfx = "application/vnd.openxmlformats-officedocument.wordprocessingml."
    add_ct = (
        '<Override PartName="/word/footnotes.xml" ContentType="' + pfx + 'footnotes+xml"/>'
        '<Override PartName="/word/endnotes.xml" ContentType="' + pfx + 'endnotes+xml"/>'
        '<Override PartName="/word/charts/chart1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.drawingml.chart+xml"/>'
        '<Default Extension="bin" '
        'ContentType="application/vnd.openxmlformats-officedocument.oleObject"/>'
        '<Default Extension="emf" ContentType="image/x-emf"/>'
        '<Default Extension="html" ContentType="text/html"/>'
    )
    assert "</Types>" in ct
    ct = ct.replace("</Types>", add_ct + "</Types>")

    rpfx = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
    add_rel = (
        '<Relationship Id="rIdFootnotes" Type="' + rpfx + 'footnotes" Target="footnotes.xml"/>'
        '<Relationship Id="rIdEndnotes" Type="' + rpfx + 'endnotes" Target="endnotes.xml"/>'
        '<Relationship Id="rIdChart" Type="' + rpfx + 'chart" Target="charts/chart1.xml"/>'
        '<Relationship Id="rIdOleObj" Type="' + rpfx + 'oleObject" Target="embeddings/oleObject1.bin"/>'
        '<Relationship Id="rIdOleImg" Type="' + rpfx + 'image" Target="media/image_ole.emf"/>'
        '<Relationship Id="rIdAltChunk" Type="' + rpfx + 'aFChunk" Target="afchunk1.html"/>'
    )
    assert "</Relationships>" in rels
    rels = rels.replace("</Relationships>", add_rel + "</Relationships>")

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zout:
        for n in names:
            if n == "[Content_Types].xml":
                zout.writestr(n, ct)
            elif n == "word/_rels/document.xml.rels":
                zout.writestr(n, rels)
            else:
                zout.writestr(n, zin.read(n))
        zout.writestr("word/footnotes.xml", _WC_FOOTNOTES_XML)
        zout.writestr("word/endnotes.xml", _WC_ENDNOTES_XML)
        zout.writestr("word/charts/chart1.xml", _WC_CHART_XML)
        zout.writestr("word/embeddings/oleObject1.bin", _WC_OLE_BIN)
        zout.writestr("word/media/image_ole.emf", _WC_OLE_IMG)
        zout.writestr("word/afchunk1.html", _WC_ALTCHUNK_HTML)
    zin.close()

    _pin_ooxml(path)


def xlsx(path: Path) -> None:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Register"
    ws.append(["Ref", "Description", "Date"])
    ws.append(["RFI-001", "Foundation query", datetime.date(2024, 7, 16)])
    ws2 = wb.create_sheet("Empty")
    ws2["A1"] = None
    wb.properties.created = FIXED_TIMESTAMP
    wb.properties.modified = FIXED_TIMESTAMP
    wb.properties.creator = "DocIQ fixtures"
    wb.properties.lastModifiedBy = "DocIQ fixtures"
    wb.save(str(path))
    _pin_ooxml(path)


def csv_file(path: Path) -> None:
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["Ref", "Description", "Date"])
    w.writerow(["NCR-007", "Weld porosity; rework 2024-07-18", "2024-07-16"])
    path.write_text(buf.getvalue(), encoding="utf-8", newline="\n")


def txt_file(path: Path) -> None:
    """Carries the adversarial normalization input on purpose: mixed CRLF/CR,
    a run of NBSPs, stacked zero-width characters and a four-blank-line gap.
    The fixture corpus is where a normalization regression should surface."""
    payload = (
        "DAILY LOG\r\n"
        "Date: 2024-07-16\r"
        "Crew:   12 operatives   \n"
        "​​﻿Delay‍ noted.\n"
        "\n\n\n\n"
        "End of log.\t\n"
    )
    path.write_text(payload, encoding="utf-8", newline="")


def eml_file(path: Path) -> None:
    """RFC-822 message. Written by hand rather than through ``email`` so the
    bytes are pinned — a library's own Message-ID and boundary generation is
    clock- and random-seeded."""
    lines = [
        "From: engineer@example.com",
        "To: contractor@example.com",
        "Cc: pm@example.com",
        "Subject: Notice of Delay - Area 200",
        "Date: Tue, 16 Jul 2024 09:30:00 +0000",
        "Message-ID: <fixture-0001@example.com>",
        "MIME-Version: 1.0",
        "Content-Type: text/plain; charset=utf-8",
        "",
        "Please treat this as notice under clause 20.1.",
        "The delay commenced 2024-07-16 and is ongoing.",
        "",
    ]
    path.write_bytes("\r\n".join(lines).encode("utf-8"))


def eml_with_attachment(path: Path, attachment: Path) -> None:
    """A message carrying a PDF — §3's "attachments extracted as child documents".

    The corpus needs this case in its own right, not only in a unit test. Email
    attachment expansion has a *different producer* in the walker from archive
    expansion, so a ZIP proving the container path proves nothing about this one;
    and until the attachment case is in the corpus, the determinism proof and the
    self-test never touch it. "The corpus doesn't exercise it" is not a reason to
    leave it out — it is a reason to put it in.

    Written by hand for the same reason as :func:`eml_file`: a library's own
    boundary and Message-ID generation is clock- and random-seeded, and these
    bytes have to be identical on every regeneration.
    """
    import base64

    payload = base64.b64encode(attachment.read_bytes()).decode("ascii")
    body = "\r\n".join(payload[i:i + 76] for i in range(0, len(payload), 76))
    lines = [
        "From: engineer@example.com",
        "To: contractor@example.com",
        "Subject: Transmittal 2024-07-18 - one attachment",
        "Date: Thu, 18 Jul 2024 11:00:00 +0000",
        "Message-ID: <fixture-0002@example.com>",
        "MIME-Version: 1.0",
        'Content-Type: multipart/mixed; boundary="dociq-fixture-boundary"',
        "",
        "--dociq-fixture-boundary",
        "Content-Type: text/plain; charset=utf-8",
        "",
        "Please find the monthly report attached.",
        "Issued 2024-07-18 under clause 20.1.",
        "",
        "--dociq-fixture-boundary",
        'Content-Type: application/pdf; name="attached_report.pdf"',
        "Content-Transfer-Encoding: base64",
        'Content-Disposition: attachment; filename="attached_report.pdf"',
        "",
        body,
        "",
        "--dociq-fixture-boundary--",
        "",
    ]
    path.write_bytes("\r\n".join(lines).encode("utf-8"))


def nested_zip(path: Path, inner_sources: list[Path]) -> None:
    """A ZIP holding a ZIP — exercises the depth guard and child ordering."""
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as zf:
        for src in inner_sources:
            info = zipfile.ZipInfo(src.name, date_time=_ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, src.read_bytes())
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        info = zipfile.ZipInfo("readme.txt", date_time=_ZIP_DATE)
        zf.writestr(info, b"Archive produced 2024-07-16.\n")
        info = zipfile.ZipInfo("inner.zip", date_time=_ZIP_DATE)
        zf.writestr(info, inner.getvalue())


def tier2_file(path: Path) -> None:
    """A legacy .doc — D-02's Tier-2 case. The bytes are an OLE header so a
    content sniff would recognize the container; the point is that DocIQ never
    tries, because §3 lists it rather than extracting it."""
    path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 504)


def misnamed_pdf(path: Path) -> None:
    """PDF bytes under a .docx name — the content-sniffing recovery case that
    litigation productions actually deliver."""
    tmp = path.with_suffix(".sniff.tmp")
    native_pdf(tmp)
    path.write_bytes(tmp.read_bytes())
    tmp.unlink()


# ---------------------------------------------------------------------------


def _generator_stamp() -> str:
    """Identity of THIS generator, readable from a checkout or from a bundle.

    ``sha256(Path(__file__).read_bytes())`` is the natural expression and it is
    wrong inside a PyInstaller build: ``__file__`` names a path in the archive
    that does not exist on disk, so the packaged self-test died with
    ``FileNotFoundError`` before it built a single fixture. This is the fourth
    member of one class — every place in the tree that derives a *path* from
    ``__file__`` and expects a real file there. The other three were
    ``branding.palette``'s brand directory, ``ingest.extract``'s OCR model
    directory and ``verify.determinism``'s subprocess ``PYTHONPATH``; this one
    was missed on the first sweep because the sweep read ``src/`` and this file
    lives under ``tests/``. Recorded rather than quietly fixed, because the
    lesson is about the sweep, not about the line.

    Frozen, the module's own compiled code stands in for its source bytes. It
    changes when the generator changes, which is the whole property the stamp
    needs; it is simply not the same VALUE as the source hash, so a corpus
    built by the frozen build and one built from a checkout do not share a
    completion marker. They also never share a directory, so nothing rebuilds
    that would not have rebuilt anyway.
    """
    try:
        return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]
    except OSError:
        import importlib.util
        import marshal

        spec = importlib.util.find_spec(__name__)
        code = spec.loader.get_code(__name__)  # type: ignore[union-attr]
        return hashlib.sha256(marshal.dumps(code)).hexdigest()[:16]


def build(out: Path | None = None) -> Path:
    """(Re)generate the corpus and return its root.

    Concurrency-safe, and it has to be. Two pytest sessions in one worktree
    both import ``conftest``, and an unguarded rebuild rewrites files a
    concurrent session is mid-read of — which changes extracted bytes and so
    changes the content hash, failing the determinism tests intermittently
    while the product is correct. That reads as a determinism defect, which is
    the most expensive kind of false alarm this project can raise.

    The guard is a directory-creation lock (atomic on Windows and POSIX) plus a
    completion marker, so the second session waits and then reuses the corpus
    rather than rebuilding it.
    """
    out = Path(out) if out is not None else OUT
    src = out / "matter"
    lock = out / ".build.lock"
    done = out / ".build.complete"
    # The marker records WHICH generator built the corpus, not merely that one
    # did. Keyed on "ok" alone, editing this file left a stale corpus in place
    # forever and the tests silently kept asserting against the old bytes --
    # which is exactly how a determinism fix here first looked like a product
    # regression.
    stamp = _generator_stamp()
    out.mkdir(parents=True, exist_ok=True)

    for _ in range(600):  # ~60s; the build itself takes a few seconds
        if done.exists() and done.read_text(encoding='utf-8').strip() == stamp:
            return src
        try:
            lock.mkdir()
            break
        except FileExistsError:
            time.sleep(0.1)
    else:
        # Never block a test run on a lock left behind by a killed session.
        shutil.rmtree(lock, ignore_errors=True)
        lock.mkdir(exist_ok=True)

    try:
        _build_corpus(src)
        done.write_text(stamp, encoding="utf-8")
    finally:
        shutil.rmtree(lock, ignore_errors=True)
    return src


def _build_corpus(src: Path) -> Path:
    sub = src / "attachments"
    sub.mkdir(parents=True, exist_ok=True)

    native_pdf(src / "01_native_report.pdf")
    scanned_pdf(src / "02_scanned_instruction.pdf")
    mixed_pdf(src / "03_mixed_transmittal.pdf")
    mixed_content_pdf(src / "15_mixed_content_page.pdf")
    empty_page_pdf(src / "04_empty_page.pdf")
    docx(src / "05_letter.docx")
    word_constructs_docx(src / "16_word_constructs.docx")
    xlsx(src / "06_register.xlsx")
    csv_file(src / "07_ncr_log.csv")
    txt_file(src / "08_daily_log.txt")
    eml_file(src / "09_notice.eml")
    eml_with_attachment(src / "14_transmittal.eml", src / "01_native_report.pdf")
    tier2_file(src / "13_legacy.doc")
    misnamed_pdf(sub / "10_misnamed.docx")
    nested_zip(src / "11_production.zip",
               [src / "07_ncr_log.csv", src / "08_daily_log.txt",
                src / "13_legacy.doc"])
    # Same bytes as 07 under a second path — the duplicate-by-hash case.
    (sub / "12_ncr_log_copy.csv").write_bytes((src / "07_ncr_log.csv").read_bytes())
    return src


if __name__ == "__main__":  # pragma: no cover — developer entry point
    print(build())
