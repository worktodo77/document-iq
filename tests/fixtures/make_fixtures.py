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


def workbook_constructs(path: Path) -> None:
    """18_workbook_constructs.xlsx -- the spreadsheet-fidelity fixture for the
    2026-09-10 sweep (findings E1, E4-E7, E10-E13). Sentinel words only, per
    the client-data rule D-12; no sentinel is a substring of another.

    ``Register``: a header row; a formula with NO stored value (E1 -- openpyxl
    never computes it); two cells holding the SAME raw 0.15 under different
    percentage formats (E10); a cell with an embedded newline and a cell with
    an embedded tab (E5); a two-row blank run (E4); a hyperlinked cell (E13)
    and a cell comment (E11); a print header and footer (E12).
    ``Chart1``: a chartsheet (E6) -- ``Workbook.worksheets`` silently omits it.
    ``Later``: follows the chartsheet, so a chartsheet that lost its page
    would shift Later's page number -- the reason a page per tab matters.
    """
    import openpyxl
    import openpyxl.chart
    import openpyxl.comments

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Register"
    ws["A1"], ws["B1"], ws["C1"] = "Ref", "Amount", "Note"
    ws["B2"] = 10
    ws["B3"] = 20
    ws["B4"] = "=SUM(B2:B3)"
    ws["C2"] = 0.15
    ws["C2"].number_format = "0%"
    ws["C3"] = 0.15
    ws["C3"].number_format = "0.00%"
    ws["A4"] = "HOLLY\nIVY"
    ws["D4"] = "TAB\tSTOP"
    # Rows 5 and 6 are left with no cells at all -- a two-row blank run.
    ws["A7"] = "JUNIPER"
    ws["A8"] = "KESTREL"
    ws["A8"].hyperlink = "https://example.invalid/LARK"
    ws["B2"].comment = openpyxl.comments.Comment("MAGPIE", "NIGHTJAR")
    ws.oddHeader.center.text = "OSPREY"
    ws.oddFooter.center.text = "PUFFIN"

    chart_sheet = wb.create_chartsheet("Chart1")
    chart = openpyxl.chart.BarChart()
    chart.add_data(openpyxl.chart.Reference(ws, min_col=2, min_row=2, max_row=3))
    chart_sheet.add_chart(chart)

    later = wb.create_sheet("Later")
    later["A1"] = "QUETZAL"

    wb.properties.created = FIXED_TIMESTAMP
    wb.properties.modified = FIXED_TIMESTAMP
    wb.properties.creator = "DocIQ fixtures"
    wb.properties.lastModifiedBy = "DocIQ fixtures"
    wb.save(str(path))
    _pin_ooxml(path)


class XlsError(int):
    """A BIFF error code (``0x07`` is ``#DIV/0!``) for :func:`xls_bytes` --
    an ``int`` subclass so a cell can say "error" rather than "number"."""


_XLS_SUBSTREAM_TYPE = {"worksheet": (0, 0x0010), "macro": (1, 0x0040),
                       "chart": (2, 0x0020), "vbmodule": (6, 0x0006)}


def xls_bytes(sheets, *, datemode: int = 0,
              formats: dict[int, str] | None = None,
              globals_extra: list[tuple[int, bytes]] | None = None) -> bytes:
    """A BIFF8 workbook inside a hand-built OLE2 compound file, since no
    ``.xls`` writer is installed anywhere on this machine.

    ``sheets`` is a sequence of dicts, in TAB order:

    * ``name``; ``kind`` -- ``"worksheet"`` (default), ``"chart"``,
      ``"macro"`` or ``"vbmodule"``, written as the BOUNDSHEET type and an
      empty substream of the matching BOF type; ``visibility`` -- 0 visible,
      1 hidden, 2 very hidden.
    * ``cells`` -- ``(row, col, value, fmt)`` in write order: a ``str`` is a
      LABEL, a ``bool`` a BOOLERR boolean, an :class:`XlsError` a BOOLERR
      error, anything else a NUMBER. ``fmt`` is a number-format index: a
      built-in (0 General, 9 ``0%``, 14 a date, 20 ``h:mm``, 46
      ``[h]:mm:ss``) or a custom one declared in ``formats``.
    * ``hidden_rows`` / ``hidden_cols`` -- ROW / COLINFO records flagged
      hidden; ``zero_height_rows`` -- ROW records of height 0, not flagged;
      ``rows_hidden_by_default`` -- a DEFAULTROWHEIGHT record saying so.
    * ``dimensions`` -- an explicit ``(first_row, last_row + 1, first_col,
      last_col + 1)``; derived from the cells otherwise.
    * ``header`` / ``footer`` -- HEADER / FOOTER record text, Excel's codes
      and all; ``even_first`` -- ``(even header, even footer, first header,
      first footer)`` for a HEADERFOOTER record, laid out as Excel 2016
      writes it.
    * ``dialog`` -- a WSBOOL record flagging the sheet a dialog sheet, as
      Excel saves one.
    * ``notes`` -- ``(row, col, author, text)`` cell comments, each an OBJ,
      TXO (text and runs in CONTINUE records) and NOTE record, as Excel
      writes them; ``notes_without_text`` -- the same, with no OBJ or TXO.
    * ``links`` -- ``(first_row, last_row, first_col, last_col, kind, target,
      location)`` HLINK records: ``kind`` is ``"url"``, ``"file"``,
      ``"internal"`` (a location only) or ``"unknown"`` (a moniker class no
      reader knows).
    * ``records`` -- raw records written last, in order, before the sheet's
      EOF: an ``(opcode, data)`` pair, or bytes already framed (a Custom View
      block copied from a file Excel wrote, drawing objects).

    ``globals_extra`` -- raw ``(opcode, data)`` records appended to the
    workbook globals after the XF records (e.g. a malformed PALETTE).

    One XF record per distinct format, in order of first use after XF 0
    (General). The stream is padded to at least the 4096-byte mini-stream
    cutoff so no MiniFAT is needed. A workbook using none of the optional
    keys is byte-identical to what this writer produced before they existed.
    """
    import struct

    formats = formats or {}

    def rec(opcode: int, data: bytes) -> bytes:
        return struct.pack("<HH", opcode, len(data)) + data

    def xl_str(s: str, lenfmt: str) -> bytes:
        if all(ord(ch) < 256 for ch in s):
            return struct.pack(lenfmt + "B", len(s), 0) + s.encode("latin-1")
        return struct.pack(lenfmt + "B", len(s), 1) + s.encode("utf-16-le")

    std_link = bytes.fromhex("D0C9EA79F9BACE118C8200AA004BA90B")
    url_moniker = bytes.fromhex("E0C9EA79F9BACE118C8200AA004BA90B")
    file_moniker = bytes.fromhex("0303000000000000C000000000000046")

    def hlink(frow, lrow, fcol, lcol, kind, target, location) -> bytes:
        head = struct.pack("<HHHH", frow, lrow, fcol, lcol) + std_link + b"\x02\x00\x00\x00"
        body = b""
        options = 0
        if kind == "url":
            options = 0x03
            text = (target + "\0").encode("utf-16-le")
            body = url_moniker + struct.pack("<I", len(text)) + text
        elif kind == "file":
            options = 0x01
            short = target.encode("latin-1") + b"\0"
            body = (file_moniker + struct.pack("<Hi", 0, len(short)) + short
                    + b"\xff\xff\xad\xde" + b"\0" * 20 + struct.pack("<i", 0))
        elif kind == "unknown":
            options = 0x01
            body = b"\x11" * 16
        if location:
            options |= 0x08
            body += (struct.pack("<I", len(location) + 1)
                     + (location + "\0").encode("utf-16-le"))
        return rec(0x01B8, head + struct.pack("<i", options) + body)

    def note_records(notes, with_text: bool, first_id: int) -> tuple[bytes, bytes]:
        objects, note_recs = b"", b""
        for n, (r, c, author, text) in enumerate(notes):
            obj_id = first_id + n
            if with_text:
                objects += rec(0x005D, struct.pack("<HHHHH", 0x15, 0x12, 0x19, obj_id, 0x4011)
                               + b"\0" * 12 + struct.pack("<HH", 0x0D, 0x16) + b"\0" * 22
                               + struct.pack("<HH", 0, 0))
                objects += rec(0x01B6, struct.pack("<HH6sHHH", 0x0212, 0, b"\0" * 6,
                                                   len(text), 16, 0) + b"\0\0")
                encoded = (b"\x00" + text.encode("latin-1")
                           if all(ord(ch) < 256 for ch in text)
                           else b"\x01" + text.encode("utf-16-le"))
                objects += rec(0x003C, encoded)
                objects += rec(0x003C, struct.pack("<HH4x", 0, 0)
                               + struct.pack("<HH4x", len(text), 0))
            note_recs += rec(0x001C, struct.pack("<HHHH", r, c, 0, obj_id)
                             + xl_str(author, "<H") + b"\0")
        return objects, note_recs

    xf_formats = [0]
    for sheet in sheets:
        for _r, _c, value, fmt in sheet.get("cells", ()):
            if fmt not in xf_formats:
                xf_formats.append(fmt)

    def xf_for(fmt: int) -> int:
        return xf_formats.index(fmt)

    def cell_record(r: int, c: int, value, fmt: int) -> bytes:
        xf = xf_for(fmt)
        if isinstance(value, str):
            return rec(0x0204, struct.pack("<HHH", r, c, xf) + xl_str(value, "<H"))
        if isinstance(value, bool):
            return rec(0x0205, struct.pack("<HHHBB", r, c, xf, int(value), 0))
        if isinstance(value, XlsError):
            return rec(0x0205, struct.pack("<HHHBB", r, c, xf, int(value), 1))
        return rec(0x0203, struct.pack("<HHHd", r, c, xf, float(value)))

    head = (rec(0x0809, struct.pack("<HHHHII", 0x0600, 0x0005, 0, 0, 0, 0))
            + rec(0x0042, struct.pack("<H", 1200))
            + rec(0x0022, struct.pack("<H", datemode)))
    for idx, fmt_str in sorted(formats.items()):
        head += rec(0x041E, struct.pack("<H", idx) + xl_str(fmt_str, "<H"))
    for fmt in xf_formats:
        head += rec(0x00E0, struct.pack("<HHHBBBBIiH", 0, fmt, 0, 0, 0, 0, 0, 0, 0, 0))
    for opcode, data in globals_extra or ():
        head += rec(opcode, data)

    def boundsheet(offset: int, sheet) -> bytes:
        sheet_type = _XLS_SUBSTREAM_TYPE[sheet.get("kind", "worksheet")][0]
        return rec(0x0085, struct.pack("<iBB", offset, sheet.get("visibility", 0),
                                       sheet_type) + xl_str(sheet["name"], "<B"))

    eof = rec(0x000A, b"")
    globals_len = (len(head) + sum(len(boundsheet(0, s)) for s in sheets)
                   + len(eof))

    substreams: list[bytes] = []
    for sheet in sheets:
        bof_type = _XLS_SUBSTREAM_TYPE[sheet.get("kind", "worksheet")][1]
        body = rec(0x0809, struct.pack("<HHHHII", 0x0600, bof_type, 0, 0, 0, 0))
        if sheet.get("kind", "worksheet") == "worksheet":
            if sheet.get("rows_hidden_by_default"):
                body += rec(0x0225, struct.pack("<HH", 0x0002, 255))
            if "dialog" in sheet:
                body += rec(0x0081, struct.pack("<BB", 0xD1 if sheet["dialog"] else 0xC1,
                                                0x04))
            if "header" in sheet:
                body += rec(0x0014, xl_str(sheet["header"], "<H"))
            if "footer" in sheet:
                body += rec(0x0015, xl_str(sheet["footer"], "<H"))
            if "even_first" in sheet:
                strings = sheet["even_first"]
                body += rec(0x089C, struct.pack("<HH8x", 0x089C, 0) + b"\0" * 16
                            + struct.pack("<H", 0x333F)
                            + struct.pack("<4H", *(len(s) for s in strings))
                            + b"".join(xl_str(s, "<H") for s in strings if s))
            for colx in sheet.get("hidden_cols", ()):
                body += rec(0x007D, struct.pack("<HHHHHH", colx, colx, 2962, 0,
                                                0x0001, 0))
            cells = list(sheet.get("cells", ()))
            dims = sheet.get("dimensions")
            if dims is None:
                dims = ((min(c[0] for c in cells), max(c[0] for c in cells) + 1,
                         min(c[1] for c in cells), max(c[1] for c in cells) + 1)
                        if cells else (0, 0, 0, 0))
            body += rec(0x0200, struct.pack("<IIHHH", *dims, 0))
            for rowx in sheet.get("hidden_rows", ()):
                body += rec(0x0208, struct.pack("<HHHHHHI", rowx, 0, 0, 255, 0,
                                                0, 0x20))
            for rowx in sheet.get("zero_height_rows", ()):
                body += rec(0x0208, struct.pack("<HHHHHHI", rowx, 0, 0, 0, 0, 0, 0))
            for r, c, value, fmt in cells:
                body += cell_record(r, c, value, fmt)
            objects, notes = note_records(sheet.get("notes", ()), True, 1)
            bare_objects, bare_notes = note_records(sheet.get("notes_without_text", ()),
                                                    False, 100)
            body += objects + bare_objects + notes + bare_notes
            for link in sheet.get("links", ()):
                body += hlink(*link)
            for record in sheet.get("records", ()):
                body += (rec(*record) if isinstance(record, tuple) else record)
        substreams.append(body + eof)

    offsets, pos = [], globals_len
    for body in substreams:
        offsets.append(pos)
        pos += len(body)
    globals_bytes = (head + b"".join(boundsheet(o, s)
                                     for o, s in zip(offsets, sheets)) + eof)
    assert len(globals_bytes) == globals_len, (len(globals_bytes), globals_len)
    workbook_stream = globals_bytes + b"".join(substreams)

    sec = 512
    stream_total = max(4096, -(-len(workbook_stream) // sec) * sec)
    workbook_stream += b"\x00" * (stream_total - len(workbook_stream))

    # --- OLE2/CFB container: the FAT sector(s), one directory sector, then
    # the padded Workbook stream as a plain sector chain. ---
    freesect, endofchain, fatsect = -1, -2, -3
    n_stream_sectors = stream_total // sec
    n_fat = 1
    while n_fat * (sec // 4) < n_fat + 1 + n_stream_sectors:
        n_fat += 1
    assert n_fat <= 109, n_fat
    dir_sector_idx = n_fat
    stream_first_sid = n_fat + 1
    total_sectors = n_fat + 1 + n_stream_sectors

    fat_entries = [freesect] * (n_fat * (sec // 4))
    for i in range(n_fat):
        fat_entries[i] = fatsect
    fat_entries[dir_sector_idx] = endofchain
    for i in range(n_stream_sectors):
        sid = stream_first_sid + i
        fat_entries[sid] = endofchain if i == n_stream_sectors - 1 else sid + 1
    fat_sector_bytes = b"".join(struct.pack("<i", v) for v in fat_entries)

    def dir_entry(name, etype, color, left, right, child, first_sid, tot_size):
        name_utf16 = name.encode("utf-16-le") + b"\x00\x00" if name else b""
        name_field = name_utf16.ljust(64, b"\x00")
        cb = len(name_utf16) if name else 0
        rest = struct.pack("<HBBiii", cb, etype, color, left, right, child)
        rest += b"\x00" * 16  # CLSID
        rest += b"\x00" * 4   # state bits
        rest += b"\x00" * 16  # timestamps
        rest += struct.pack("<ii", first_sid, tot_size)
        rest += b"\x00" * 4
        entry = name_field + rest
        assert len(entry) == 128, len(entry)
        return entry

    root_entry = dir_entry("Root Entry", 5, 1, -1, -1, 1, -2, 0)
    wb_entry = dir_entry("Workbook", 2, 1, -1, -1, -1, stream_first_sid, stream_total)
    empty_entry = b"\x00" * 128
    dir_sector_bytes = root_entry + wb_entry + empty_entry + empty_entry

    header = bytearray(512)
    struct.pack_into("<8s", header, 0, b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1")
    struct.pack_into("<H", header, 24, 0x003E)
    struct.pack_into("<H", header, 26, 0x0003)
    struct.pack_into("<H", header, 28, 0xFFFE)
    struct.pack_into("<H", header, 30, 9)
    struct.pack_into("<H", header, 32, 6)
    struct.pack_into("<I", header, 40, 0)
    struct.pack_into("<I", header, 44, n_fat)
    struct.pack_into("<I", header, 48, dir_sector_idx)
    struct.pack_into("<I", header, 56, 4096)
    struct.pack_into("<i", header, 60, endofchain)
    struct.pack_into("<i", header, 68, endofchain)
    difat = [freesect] * 109
    for i in range(n_fat):
        difat[i] = i
    for i, v in enumerate(difat):
        struct.pack_into("<i", header, 76 + i * 4, v)

    file_bytes = bytes(header) + fat_sector_bytes + dir_sector_bytes + workbook_stream
    assert len(file_bytes) == 512 + sec * total_sectors, (
        len(file_bytes), 512 + sec * total_sectors)
    return file_bytes


def legacy_workbook(path: Path) -> None:
    """19_legacy_workbook.xls -- BIFF8 records inside a hand-built OLE2
    compound file (:func:`xls_bytes`). Exercises E2 (a date-formatted cell
    reads as an ISO date, not its serial number), E3 (a boolean and an error
    cell read as ``TRUE`` and ``#DIV/0!``, not raw codes), E4 (the blank row
    reads ``[blank row 2]``) and E15 (a whole-number cell reads ``5``, not
    ``5.0``).

    One sheet ("Legacy"): A1 a text cell, B1 a NUMBER formatted as a date
    (the serial for 2024-07-16), C1 a BOOLEAN TRUE, D1 an ERROR cell holding
    ``#DIV/0!`` (BIFF error code 0x07), E1 a whole-number NUMBER, a blank
    row, then a trailing text cell -- so a reader that mis-decodes any one of
    them still has to explain the others.

    Read back with ``xlrd`` before returning and raises if any cell it wrote
    is missing or wrong: a byte-level BIFF/CFB writer with no library behind
    it has to prove its own output rather than merely emit bytes.
    """
    import xlrd

    serial = (datetime.date(2024, 7, 16) - datetime.date(1899, 12, 30)).days
    path.write_bytes(xls_bytes([{
        "name": "Legacy",
        "cells": [(0, 0, "RAVENXLS", 0),
                  (0, 1, float(serial), 14),   # built-in 14 = a date format
                  (0, 2, True, 0),
                  (0, 3, XlsError(0x07), 0),   # #DIV/0!
                  (0, 4, 5.0, 0),
                  (2, 0, "SWIFT", 0)],
    }]))

    # Round-trip self-check: prove every cell this wrote is actually there.
    book = xlrd.open_workbook(str(path))
    sheet = book.sheet_by_index(0)
    expected = {
        (0, 0): "RAVENXLS",
        (0, 1): float(serial),
        (0, 2): 1,
        (0, 3): 0x07,
        (0, 4): 5.0,
        (2, 0): "SWIFT",
    }
    for (r, c), want in expected.items():
        got = sheet.cell_value(r, c)
        if got != want:
            raise AssertionError(
                f"legacy_workbook self-check failed: cell ({r},{c}) read "
                f"back as {got!r}, expected {want!r} -- the fixture writer "
                f"produced bytes xlrd cannot read the way it was written")


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
    # 2026-09-10 spreadsheet-fidelity sweep. Both are in
    # dociq.selftest._EXPECTED: 18 at 3 pages (its chartsheet keeps a page),
    # 19 at 1.
    workbook_constructs(src / "18_workbook_constructs.xlsx")
    legacy_workbook(src / "19_legacy_workbook.xls")
    return src


if __name__ == "__main__":  # pragma: no cover — developer entry point
    print(build())
