"""Spreadsheet fidelity: a cell's value, or the meaning of its value, is never
lost or changed without a word said.

The 2026-09-10 fidelity sweep found ``.xlsx``/``.xls`` extraction
(``src/dociq/ingest/extract.py``: ``_extract_xlsx``, ``_extract_xls`` and
their helpers) losing cell values and meanings silently -- the confirmed
defect (E1) together with its class. These tests pin the FIXED behavior,
against the corpus fixtures (``workbook_constructs`` ->
``18_workbook_constructs.xlsx``, ``legacy_workbook`` ->
``19_legacy_workbook.xls``) and against small workbooks each test builds for
the siblings a fixture does not hold (review-fix round 1, 2026-09-14): time
and duration cells, stored empty formula results, numbers that are no date,
header/footer codes, threaded comments, and the rest named per test.

Output is pinned EXACTLY where the wording is decided: a substring check lets
a wrong line, a wrong order or a stray code letter through.

Every sentinel word is invented and is not a substring of any other sentinel
in the same workbook (client-data rule D-12).
"""

from __future__ import annotations

import datetime
import html
import io
import re
import zipfile
import xml.etree.ElementTree as ET

import make_fixtures
import openpyxl
import openpyxl.chart
import openpyxl.comments
import pytest

from dociq.contracts import ProcessingStatus, RunConfig
from dociq.identify import bates
from dociq.ingest import extract as ex
from dociq.ingest import walker
from dociq.ingest.pagemodel import normalize

from .conftest import FIXTURES

UNREAD = ex.M_XLSX_SHEET_UNREAD
PAGE_NOTE_XLSX = "XLSX has no page boundaries; one synthetic page per worksheet"
PAGE_NOTE_XLS = "XLS has no page boundaries; one synthetic page per worksheet"
FORMULA_NOTE_ONE = ("1 cell(s): a formula cell had no stored value; the formula is "
                    "shown in the cell's place instead of the missing value")
CHART1_NOTE = f"chartsheet 'Chart1': its chart was not read ({UNREAD})"
CAP_LINE = "[not read: the row cap was reached before this sheet]"

REGISTER_PAGE = (
    "[sheet: Register]\n"
    "OSPREY\n"
    "Ref\tAmount\tNote\n"
    "\t10\t15%\n"
    "\t20\t15.00%\n"
    "HOLLY ¶ IVY\t=SUM(B2:B3)\t\tTAB STOP\n"
    "[blank rows 5-6]\n"
    "JUNIPER\n"
    "KESTREL <https://example.invalid/LARK>\n"
    "[comment on B2 by NIGHTJAR] MAGPIE\n"
    "PUFFIN")
"""Fixture 18's Register page, whole: every E-finding's output in place."""

LEGACY_PAGE = ("[sheet: Legacy]\n"
               "RAVENXLS\t2024-07-16\tTRUE\t#DIV/0!\t5\n"
               "[blank row 2]\n"
               "SWIFT")
"""Fixture 19's only page, whole."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pages(name: str, opt: ex.ExtractOptions | None = None):
    path = FIXTURES / name
    return ex.extract(path.name, path.read_bytes(), opt)


def _page_with(pages, prefix: str):
    """The first page whose text starts with ``prefix``, or ``None``.

    Never indexes into ``pages`` directly -- a wrong page count must fail an
    assertion with a clear message, not raise ``IndexError`` first.
    """
    return next((p for p in pages if p.text.startswith(prefix)), None)


def _assert_found(text: str, sentinel: str, note: str = "") -> None:
    """``sentinel in text``, via ``str.find`` so a miss reports its position."""
    pos = text.find(sentinel)
    assert pos != -1, (
        f"{sentinel!r} not found in extracted text (str.find -> {pos}). "
        f"{note}Text was:\n{text!r}")


def _texts(doc) -> list[str]:
    return [p.text for p in doc.pages]


def _assert_doc(doc, pages: list[str], notes: list[str]) -> None:
    """Status FULL, and the pages and notes exactly as given."""
    assert doc.status is ProcessingStatus.FULL, (doc.status, doc.error)
    assert _texts(doc) == pages, f"pages were {_texts(doc)!r}"
    assert list(doc.notes) == notes, f"notes were {list(doc.notes)!r}"


def _xlsx(build) -> bytes:
    """An ``.xlsx`` built by openpyxl: ``build(workbook)`` fills it in."""
    wb = openpyxl.Workbook()
    build(wb)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _patch(raw: bytes, edits: dict, renames: dict | None = None) -> bytes:
    """Rewrite package parts as raw XML -- for constructs openpyxl cannot
    write, or writes differently from Excel. ``edits`` maps a part name to
    ``fn(old text or None) -> new text or None`` (``None`` deletes the part;
    a part not present is added when ``fn(None)`` returns text); ``renames``
    maps an old part name to a new one."""
    renames = renames or {}
    zin = zipfile.ZipFile(io.BytesIO(raw))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        seen = set()
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename in edits:
                seen.add(info.filename)
                new = edits[info.filename](data.decode("utf-8"))
                if new is None:
                    continue
                data = new.encode("utf-8")
            zout.writestr(renames.get(info.filename, info.filename), data)
        for name, fn in edits.items():
            if name not in seen:
                new = fn(None)
                if new is not None:
                    zout.writestr(name, new.encode("utf-8"))
    return out.getvalue()


def _part_names(raw: bytes) -> list[str]:
    return zipfile.ZipFile(io.BytesIO(raw)).namelist()


def _extract(name: str, raw: bytes):
    return ex.extract(name, raw)


def _detected_dates(tmp_path, files: dict[str, bytes]) -> dict[str, tuple]:
    """``{filename: detected_dates}`` from a real ``walker.run`` over a folder
    holding exactly ``files`` -- the product path that fills the index and the
    screen's document date."""
    src = tmp_path / "src"
    src.mkdir()
    for name, data in files.items():
        (src / name).write_bytes(data)
    config = RunConfig(source_root=str(src), output_root=str(tmp_path / "out"),
                       ocr_engine_version=ex.ocr_engine_version())
    result = walker.run(config, walker.WalkOptions(ocr_enabled=False, resume=False))
    return {d.filename: d.detected_dates for d in result.documents}


def _set_header_footer(sheet_xml: str, header_footer_xml: str) -> str:
    return re.sub(r"<headerFooter>.*?</headerFooter>", header_footer_xml,
                  sheet_xml, flags=re.S)


# ---------------------------------------------------------------------------
# E1: formulas with no stored value
# ---------------------------------------------------------------------------


def test_e1_formula_with_no_stored_value_shows_the_formula():
    """E1 (confirmed): ``data_only=True`` never computes a formula, so the
    cell showed nothing. The formula is shown and ONE marked note, pinned
    exactly, counts it; a workbook with no such cell carries no such note.

    Sibling (review A2): Excel stores the result of ``=IF(x="","",x)`` as
    ``<c t="str"><f>..</f><v/></c>`` -- a stored value that is the empty
    string. It is blank, not missing: no formula text, no note. Only a
    ``t="str"`` formula with NO ``<v>`` at all is missing."""
    doc = _pages("18_workbook_constructs.xlsx")
    reg = _page_with(doc.pages, "[sheet: Register]")
    assert reg is not None, "no page starts with '[sheet: Register]'"
    _assert_found(reg.text, "HOLLY ¶ IVY\t=SUM(B2:B3)\t\tTAB STOP",
                  "the formula cell's own formula, in its row. ")
    formula_notes = [n for n in doc.notes if ex.M_XLSX_FORMULA_NO_VALUE in n]
    assert formula_notes == [FORMULA_NOTE_ONE], f"notes were {list(doc.notes)!r}"
    assert ex.has_evidence_marker(FORMULA_NOTE_ONE)
    plain = _pages("06_register.xlsx")
    assert not [n for n in plain.notes if ex.M_XLSX_FORMULA_NO_VALUE in n], plain.notes

    def build(wb):
        ws = wb.active
        ws.title = "Idiom"
        ws["A1"], ws["B1"], ws["C1"] = "ALDER", "PLACEHOLDER1", "BIRCH"
        ws["A2"], ws["B2"] = "CEDAR", "PLACEHOLDER2"
        ws["A3"], ws["B3"] = "DOGWOOD", "PLACEHOLDER3"

    def cells(xml: str) -> str:
        xml = re.sub(r'<c r="B1"[^>]*>.*?</c>',
                     '<c r="B1" t="str"><f>IF(A1="ALDER","",A1)</f><v/></c>', xml)
        xml = re.sub(r'<c r="B2"[^>]*>.*?</c>',
                     '<c r="B2" t="str"><f>IF(A2="","",A2)</f><v></v></c>', xml)
        return re.sub(r'<c r="B3"[^>]*>.*?</c>',
                      '<c r="B3" t="str"><f>A3</f></c>', xml)

    raw = _patch(_xlsx(build), {"xl/worksheets/sheet1.xml": cells})
    _assert_doc(_extract("idiom.xlsx", raw),
                ["[sheet: Idiom]\nALDER\t\tBIRCH\nCEDAR\nDOGWOOD\t=A3"],
                [FORMULA_NOTE_ONE, PAGE_NOTE_XLSX])


def test_e1_note_is_true_for_what_each_cell_shows():
    """E1's note says "the formula is shown" -- true only where a formula is.
    An ARRAY formula with no stored value carries its text on the object and
    shows it; a DATA-TABLE formula has no text and shows the placeholder, and
    the note says so for each count, exactly."""
    from openpyxl.worksheet.formula import ArrayFormula

    def build(wb):
        ws = wb.active
        ws.title = "Arrays"
        ws["A1"], ws["A2"] = 2, 3
        ws["B1"] = ArrayFormula("B1", "=SUM(A1:A2*2)")
        ws["C1"] = "PLACEHOLDERTABLE"

    raw = _patch(_xlsx(build), {"xl/worksheets/sheet1.xml": lambda x: re.sub(
        r'<c r="C1"[^>]*>.*?</c>',
        '<c r="C1"><f t="dataTable" ref="C1" dt2D="0" dtr="0" r1="A1"/></c>', x)})
    both = ("2 cell(s): a formula cell had no stored value; the formula is shown "
            "in the cell's place for 1; for the other 1 the formula's own text "
            "was not available, so '[formula with no stored value]' is shown")
    _assert_doc(_extract("arrays.xlsx", raw),
                ["[sheet: Arrays]\n2\t=SUM(A1:A2*2)\t[formula with no stored value]\n3"],
                [both, PAGE_NOTE_XLSX])

    only_table = _patch(_xlsx(build), {"xl/worksheets/sheet1.xml": lambda x: re.sub(
        r'<c r="B1"[^>]*>.*?</c>', "", re.sub(
            r'<c r="C1"[^>]*>.*?</c>',
            '<c r="C1"><f t="dataTable" ref="C1" dt2D="0" dtr="0" r1="A1"/></c>', x),
        flags=re.S)})
    placeholder_only = (
        "1 cell(s): a formula cell had no stored value; the formula's own text "
        "was not available either, so '[formula with no stored value]' is shown "
        "in the cell's place")
    _assert_doc(_extract("table.xlsx", only_table),
                ["[sheet: Arrays]\n2\t\t[formula with no stored value]\n3"],
                [placeholder_only, PAGE_NOTE_XLSX])


# ---------------------------------------------------------------------------
# E2: dates, times, durations, and numbers that are no date
# ---------------------------------------------------------------------------


def _clock_xls() -> bytes:
    return make_fixtures.xls_bytes([{
        "name": "Clocks",
        "cells": [(0, 0, "GEARWHEEL", 0),
                  (0, 1, 0.35, 20),        # h:mm
                  (0, 2, 1.5, 46),         # [h]:mm:ss, over a day
                  (0, 3, 0.0, 20),         # midnight
                  (0, 4, 0.75, 164),       # custom hh:mm
                  (0, 5, 61.25, 46),       # a duration past the 1900 leap-day quirk
                  (0, 6, 45489.5, 22)],    # a real date and time
    }], formats={164: "hh:mm"})


def _clock_xls_1904() -> bytes:
    return make_fixtures.xls_bytes([{
        "name": "Clocks1904",
        "cells": [(0, 0, "SPROCKET", 0), (0, 1, 0.35, 20), (0, 2, 1.5, 46),
                  (0, 3, 44027.0, 14)],    # 2024-07-16 in the 1904 system
    }], datemode=1)


def _clock_xlsx(epoch=None) -> bytes:
    def build(wb):
        if epoch is not None:
            wb.epoch = epoch
        ws = wb.active
        ws.title = "Clocks"
        ws["A1"] = "GEARWHEEL"
        ws["B1"] = datetime.time(8, 24)
        ws["C1"] = datetime.timedelta(days=1.5)
        ws["C1"].number_format = "[h]:mm:ss"
        ws["D1"] = datetime.time(0, 0)
        ws["E1"] = datetime.datetime(2024, 7, 16)
        ws["E1"].number_format = "yyyy-mm-dd"
    return _xlsx(build)


def test_e2_xls_date_formatted_cell_reads_as_iso_date():
    """E2 (confirmed): a date-formatted ``.xls`` cell reads as an ISO date;
    fixture 19's whole page is pinned (E3, E4, E15 with it).

    Sibling (review A1): xlrd types a TIME or DURATION cell as a date too, and
    ``xldate_as_datetime`` put it on the 1899-12-31 epoch -- a date the file
    does not hold. A time reads as a time, a duration as hours past 24, and
    the 1904 date system (``datemode`` 1) reads its own dates."""
    doc = _pages("19_legacy_workbook.xls")
    _assert_doc(doc, [LEGACY_PAGE], [PAGE_NOTE_XLS])
    _assert_doc(_extract("clocks.xls", _clock_xls()),
                ["[sheet: Clocks]\nGEARWHEEL\t08:24:00\t36:00:00\t00:00:00\t18:00:00"
                 "\t1470:00:00\t2024-07-16 12:00:00"],
                [PAGE_NOTE_XLS])
    _assert_doc(_extract("clocks1904.xls", _clock_xls_1904()),
                ["[sheet: Clocks1904]\nSPROCKET\t08:24:00\t36:00:00\t2024-07-16"],
                [PAGE_NOTE_XLS])


def test_e2_xlsx_duration_reads_as_hours_like_xls():
    """The ``.xlsx`` half of A1: openpyxl hands a ``[h]:mm:ss`` cell over as a
    ``timedelta``, which printed ``1 day, 12:00:00``. Both formats read a
    duration the same way, as Excel shows it -- in either date system."""
    from openpyxl.utils.datetime import MAC_EPOCH

    for epoch in (None, MAC_EPOCH):
        _assert_doc(_extract("clocks.xlsx", _clock_xlsx(epoch)),
                    ["[sheet: Clocks]\nGEARWHEEL\t08:24:00\t36:00:00\t00:00:00\t2024-07-16"],
                    [PAGE_NOTE_XLSX])


def test_e2_time_and_duration_cells_add_no_detected_date(tmp_path):
    """Review A1's harm: the invented 1899/1900 dates reached
    ``DocumentRecord.detected_dates`` -- the index and the screen's document
    date. Through the real walk, each workbook's only date is its real one."""
    dates = _detected_dates(tmp_path, {
        "clocks.xls": _clock_xls(), "clocks1904.xls": _clock_xls_1904(),
        "clocks.xlsx": _clock_xlsx()})
    assert dates == {"clocks.xls": ("2024-07-16",),
                     "clocks1904.xls": ("2024-07-16",),
                     "clocks.xlsx": ("2024-07-16",)}, dates


def test_e2_date_formatted_number_that_is_no_date_shows_the_number():
    """Review B2: one out-of-calendar number under a date format failed the
    WHOLE ``.xls`` workbook. A number that is no date in Excel's calendar --
    past 9999-12-31, negative, NaN -- reads as stored, beside the rest of the
    workbook, with an unmarked note counting them. ``.xlsx`` did not degrade
    to the number either: openpyxl substitutes ``#VALUE!`` for the first and
    counts a negative serial back into an invented 1899 date."""
    raw = make_fixtures.xls_bytes([
        {"name": "Costs",
         "cells": [(0, 0, "COSTROW", 0), (0, 1, 3000000.0, 14), (0, 2, -5.0, 14),
                   (0, 3, float("nan"), 14), (0, 4, 2958465.0, 14)]},
        {"name": "Other", "cells": [(0, 0, "THIRDSHEET", 0)]},
    ])
    note = ("3 cell(s) formatted as a date held a number that is no date in "
            "Excel's calendar (negative, past 9999-12-31, or not a number); the "
            "number is shown as stored")
    _assert_doc(_extract("costs.xls", raw),
                ["[sheet: Costs]\nCOSTROW\t3000000\t-5\tnan\t9999-12-31",
                 "[sheet: Other]\nTHIRDSHEET"],
                [note, PAGE_NOTE_XLS])
    assert not ex.has_evidence_marker(note)

    def build(wb):
        ws = wb.active
        ws.title = "Costs"
        ws["A1"], ws["B1"], ws["C1"], ws["D1"] = "COSTROW", 3000000, -5, -0.5
        ws["B1"].number_format = ws["C1"].number_format = "yyyy-mm-dd"
        ws["D1"].number_format = "h:mm"

    with pytest.warns(UserWarning, match="outside the limits for dates"):
        doc = _extract("costs.xlsx", _xlsx(build))
    _assert_doc(doc, ["[sheet: Costs]\nCOSTROW\t3000000\t-5\t-0.5"],
                [note, PAGE_NOTE_XLSX])


# ---------------------------------------------------------------------------
# E3 / E15: .xls booleans, errors, numbers
# ---------------------------------------------------------------------------


def _legacy_row_xls() -> bytes:
    return make_fixtures.xls_bytes([{
        "name": "Row",
        "cells": [(0, 0, "EGRET\r\nFINCH", 0), (0, 1, False, 0),
                  (0, 2, make_fixtures.XlsError(0x2A), 0), (0, 3, 5.5, 0),
                  (0, 4, 0.15, 9), (0, 5, 0.15, 10), (0, 6, "TAB\tSTOP", 0)],
    }])


def test_e3_xls_boolean_and_error_render_their_own_text():
    """E3 (confirmed): TRUE and ``#DIV/0!`` on fixture 19, and their siblings
    on one ``.xls`` row: FALSE, a second error code (``#N/A``), a fraction, a
    CRLF inside a cell (ONE pilcrow), a tab inside a cell, and -- review
    unverified item -- the ``.xls`` percentages, which read as bare decimals
    while ``.xlsx`` read them as percentages."""
    doc = _pages("19_legacy_workbook.xls")
    _assert_doc(doc, [LEGACY_PAGE], [PAGE_NOTE_XLS])
    _assert_doc(_extract("row.xls", _legacy_row_xls()),
                ["[sheet: Row]\nEGRET ¶ FINCH\tFALSE\t#N/A\t5.5\t15%\t15.00%\tTAB STOP"],
                [PAGE_NOTE_XLS])


def test_e15_xls_whole_number_reads_as_an_integer():
    """E15: a whole number reads ``5``, not ``5.0``; a fraction keeps its
    fraction; and infinity or NaN under the General format reads as the
    number -- ``int(value)`` on either raised and failed the whole workbook."""
    doc = _pages("19_legacy_workbook.xls")
    sheet = _page_with(doc.pages, "[sheet: Legacy]")
    assert sheet is not None, "no page starts with '[sheet: Legacy]'"
    row0 = next((ln for ln in sheet.text.split("\n") if "RAVENXLS" in ln), None)
    assert row0 is not None, f"no line contains 'RAVENXLS'; text was {sheet.text!r}"
    assert row0.split("\t")[-1] == "5", row0
    raw = make_fixtures.xls_bytes([{"name": "Numbers", "cells": [
        (0, 0, "PLOVER", 0), (0, 1, 5.5, 0), (0, 2, float("inf"), 0),
        (0, 3, float("-inf"), 0), (0, 4, float("nan"), 0), (0, 5, -7.0, 0)]}])
    _assert_doc(_extract("numbers.xls", raw),
                ["[sheet: Numbers]\nPLOVER\t5.5\tinf\t-inf\tnan\t-7"],
                [PAGE_NOTE_XLS])


# ---------------------------------------------------------------------------
# E4 / E5: row positions and in-cell whitespace
# ---------------------------------------------------------------------------


def test_e4_blank_rows_collapse_to_one_marked_line():
    """E4 (confirmed): rows 5 and 6 of Register are one ``[blank rows 5-6]``.

    Sibling (review unverified): a row whose cells hold only whitespace is as
    blank as an empty row. Printed, it normalized to an empty line, and every
    row number after it was lost -- in both formats. A blank row AFTER the
    last data row, whitespace-only or not, still produces nothing."""
    doc = _pages("18_workbook_constructs.xlsx")
    reg = _page_with(doc.pages, "[sheet: Register]")
    assert reg is not None, "no page starts with '[sheet: Register]'"
    _assert_found(reg.text, "\n[blank rows 5-6]\nJUNIPER\n", "the collapsed blank run. ")

    def build(wb):
        ws = wb.active
        ws.title = "Space"
        ws["A1"], ws["A2"], ws["B3"], ws["A4"], ws["A5"], ws["C7"] = (
            "SISKIN", " ", "   ", "  ", "TWITE", " ")

    expected = ["[sheet: Space]\nSISKIN\n[blank rows 2-4]\nTWITE"]
    _assert_doc(_extract("space.xlsx", _xlsx(build)), expected, [PAGE_NOTE_XLSX])
    raw = make_fixtures.xls_bytes([{"name": "Space", "cells": [
        (0, 0, "SISKIN", 0), (1, 0, " ", 0), (2, 1, "   ", 0), (4, 0, "TWITE", 0),
        (6, 2, " ", 0)]}])
    _assert_doc(_extract("space.xls", raw), expected, [PAGE_NOTE_XLS])


def test_e5_newline_and_tab_inside_a_cell_stay_on_one_line():
    """E5: a cell's own newline becomes `` ¶ `` and its tab one space, so a row
    stays one line.

    Sibling (review B6): comment text and author go through the same
    cleaning, so one comment is one line -- an ordinary Excel note starts
    with ``Author:`` and a line break, and its second line was
    indistinguishable from a print footer."""
    doc = _pages("18_workbook_constructs.xlsx")
    reg = _page_with(doc.pages, "[sheet: Register]")
    assert reg is not None, "no page starts with '[sheet: Register]'"
    lines = reg.text.split("\n")
    assert "HOLLY ¶ IVY\t=SUM(B2:B3)\t\tTAB STOP" in lines, lines

    def build(wb):
        ws = wb.active
        ws.title = "Notes"
        ws["A1"], ws["A2"] = "GROUSEROW", "JACANAROW"
        ws["A1"].comment = openpyxl.comments.Comment("GROUSE:\nHERON", "GROUSE")
        ws["A2"].comment = openpyxl.comments.Comment("IBIS\t30", "JACANA")

    _assert_doc(_extract("notes.xlsx", _xlsx(build)),
                ["[sheet: Notes]\nGROUSEROW\nJACANAROW\n"
                 "[comment on A1 by GROUSE] GROUSE: ¶ HERON\n"
                 "[comment on A2 by JACANA] IBIS 30"],
                [PAGE_NOTE_XLSX])


def test_e5_a_date_a_cell_wraps_is_still_detected(tmp_path):
    """Review B4: E5's pilcrow sat between a wrapped date's parts, so
    ``16 July`` / ``2024`` stopped being a date for detection, and the index
    and screen date changed. Detection now reads the cell's break as a break
    (``extract.date_detection_text``); the page text keeps the pilcrow; and a
    pilcrow in a document that is not a spreadsheet stays a character."""
    def build(wb):
        ws = wb.active
        ws.title = "Letters"
        ws["A1"] = "Letter dated 16 July\n2024"
        ws["A2"] = "Issued March 1,\n2025"
        ws["A3"] = "Plain 2019-02-11"

    xls = make_fixtures.xls_bytes([{"name": "Letters", "cells": [
        (0, 0, "Letter dated 16 July\n2024", 0), (1, 0, "Issued March 1,\r\n2025", 0),
        (2, 0, "Plain 2019-02-11", 0)]}])
    page = ("[sheet: Letters]\nLetter dated 16 July ¶ 2024\nIssued March 1, ¶ 2025\n"
            "Plain 2019-02-11")
    assert _texts(_extract("letters.xlsx", _xlsx(build))) == [page]
    assert _texts(_extract("letters.xls", xls)) == [page]
    dates = _detected_dates(tmp_path, {
        "letters.xlsx": _xlsx(build), "letters.xls": xls,
        "memo.txt": "letter of March ¶ 12, 2020\n".encode("utf-8")})
    assert dates == {"letters.xlsx": ("2024-07-16", "2025-03-01", "2019-02-11"),
                     "letters.xls": ("2024-07-16", "2025-03-01", "2019-02-11"),
                     "memo.txt": ()}, dates


# ---------------------------------------------------------------------------
# E6: every tab keeps its page, whatever its kind
# ---------------------------------------------------------------------------


def _ledger_and_plot(wb, rows: int = 2):
    ws = wb.active
    ws.title = "Ledger"
    for i in range(1, rows + 1):
        ws[f"A{i}"] = f"ROW{i}"
    ws.oddHeader.center.text = "HERONHEAD"
    plot = wb.create_chartsheet("Plot")
    chart = openpyxl.chart.BarChart()
    chart.add_data(openpyxl.chart.Reference(ws, min_col=1, min_row=1, max_row=2))
    plot.add_chart(chart)


PLOT_NOTE = f"chartsheet 'Plot': its chart was not read ({UNREAD})"


def test_e6_chartsheet_gets_its_own_page_in_tab_order():
    """E6: a chartsheet gets its page, in tab order, reading exactly
    ``[chartsheet: <name>]``, with a marked note naming it.

    Sibling (review unverified): the KIND comes from the relationship Type,
    not from ``chartsheets/`` in the part's path. A chartsheet stored at
    ``xl/plots/plot1.xml`` failed the whole workbook."""
    doc = _pages("18_workbook_constructs.xlsx")
    first_lines = [p.text.splitlines()[0] if p.text else "" for p in doc.pages]
    assert first_lines == ["[sheet: Register]", "[chartsheet: Chart1]", "[sheet: Later]"], (
        first_lines)
    assert doc.pages[1].text == "[chartsheet: Chart1]"
    assert list(doc.notes) == [CHART1_NOTE, FORMULA_NOTE_ONE, PAGE_NOTE_XLSX], doc.notes

    raw = _xlsx(_ledger_and_plot)
    part = next(n for n in _part_names(raw) if n.startswith("xl/chartsheets/sheet"))
    rels = next(n for n in _part_names(raw) if n.startswith("xl/chartsheets/_rels/"))
    moved = _patch(raw, {
        "[Content_Types].xml": lambda t: t.replace("/" + part, "/xl/plots/plot1.xml"),
        "xl/_rels/workbook.xml.rels": lambda t: t.replace("/" + part, "/xl/plots/plot1.xml"),
    }, renames={part: "xl/plots/plot1.xml", rels: "xl/plots/_rels/plot1.xml.rels"})
    _assert_doc(_extract("moved.xlsx", moved),
                ["[sheet: Ledger]\nHERONHEAD\nROW1\nROW2", "[chartsheet: Plot]"],
                [PLOT_NOTE, PAGE_NOTE_XLSX])


def test_e6_tab_order_comes_from_the_packages_relationships():
    """Review unverified: the workbook part was read from the fixed name
    ``xl/workbook.xml``. A package whose ``_rels/.rels`` names another part
    fell back to openpyxl's worksheet list: the chartsheet's page vanished,
    and no sheet's header was read -- with no note."""
    raw = _xlsx(_ledger_and_plot)
    moved = _patch(raw, {
        "[Content_Types].xml": lambda t: t.replace("/xl/workbook.xml", "/xl/book.xml"),
        "_rels/.rels": lambda t: t.replace("xl/workbook.xml", "xl/book.xml"),
    }, renames={"xl/workbook.xml": "xl/book.xml",
                "xl/_rels/workbook.xml.rels": "xl/_rels/book.xml.rels"})
    _assert_doc(_extract("book.xlsx", moved),
                ["[sheet: Ledger]\nHERONHEAD\nROW1\nROW2", "[chartsheet: Plot]"],
                [PLOT_NOTE, PAGE_NOTE_XLSX])


def test_e6_xls_chart_and_macro_tabs_keep_their_pages():
    """Review unverified: xlrd reads worksheets only, so a chart tab and an
    Excel 4.0 macro-sheet tab vanished and every later page number shifted.
    Each keeps its page, named from the workbook's own tab records, with a
    marked note."""
    raw = make_fixtures.xls_bytes([
        {"name": "Legacy", "cells": [(0, 0, "WOODLARK", 0)]},
        {"name": "Plot", "kind": "chart"},
        {"name": "Macro1", "kind": "macro"},
        {"name": "Annex", "cells": [(0, 0, "YELLOWHAMMER", 0)]},
    ])
    _assert_doc(_extract("tabs.xls", raw),
                ["[sheet: Legacy]\nWOODLARK", "[chartsheet: Plot]",
                 "[sheet: Macro1]\n[not read: an Excel 4.0 macro sheet]",
                 "[sheet: Annex]\nYELLOWHAMMER"],
                [PLOT_NOTE,
                 f"sheet 'Macro1': an Excel 4.0 macro sheet; its content was not read ({UNREAD})",
                 PAGE_NOTE_XLS])


def _three_sheets(wb):
    ws = wb.active
    ws.title = "First"
    ws["A1"] = "LAPWING"
    wb.create_sheet("Gone")["A1"] = "GONEWORD"
    wb.create_sheet("Last")["A1"] = "LINNETWORD"


def test_e6_dialog_sheet_and_missing_sheet_part_are_disclosed_as_unread():
    """Review unverified: a sheet whose part is missing from the file had no
    page and no note, shifting every later page; a dialog sheet read as an
    empty page with no note. Each keeps its page and says why it was not
    read, with a marked note."""
    missing = _patch(_xlsx(_three_sheets), {"xl/worksheets/sheet2.xml": lambda t: None})
    _assert_doc(_extract("missing.xlsx", missing),
                ["[sheet: First]\nLAPWING",
                 "[sheet: Gone]\n[not read: its part is missing from the file]",
                 "[sheet: Last]\nLINNETWORD"],
                [f"sheet 'Gone': its part is missing from the file; its content "
                 f"was not read ({UNREAD})", PAGE_NOTE_XLSX])

    dialog_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                  '<dialogsheet xmlns="http://schemas.openxmlformats.org/'
                  'spreadsheetml/2006/main"><sheetPr/></dialogsheet>')
    dialog = _patch(_xlsx(_three_sheets), {
        "xl/worksheets/sheet2.xml": lambda t: None,
        "xl/dialogsheets/sheet1.xml": lambda t: dialog_xml,
        "xl/_rels/workbook.xml.rels": lambda t: t.replace(
            'relationships/worksheet" Target="/xl/worksheets/sheet2.xml"',
            'relationships/dialogsheet" Target="/xl/dialogsheets/sheet1.xml"'),
        "[Content_Types].xml": lambda t: t.replace(
            '"/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-'
            'officedocument.spreadsheetml.worksheet+xml"',
            '"/xl/dialogsheets/sheet1.xml" ContentType="application/vnd.openxmlformats-'
            'officedocument.spreadsheetml.dialogsheet+xml"'),
    })
    _assert_doc(_extract("dialog.xlsx", dialog),
                ["[sheet: First]\nLAPWING", "[sheet: Gone]\n[not read: a dialog sheet]",
                 "[sheet: Last]\nLINNETWORD"],
                [f"sheet 'Gone': a dialog sheet; its content was not read ({UNREAD})",
                 PAGE_NOTE_XLSX])


def test_e6_duplicate_sheet_names_each_read_their_own_sheet():
    """Review C: two tabs with one name (a non-Excel writer can produce it)
    were looked up by name, and openpyxl returns the first -- the second
    page silently showed the first sheet's content. Sheets are matched by
    their own part."""
    def build(wb):
        ws = wb.active
        ws.title = "Data"
        ws["A1"] = "FIRSTSHEETWORD"
        wb.create_sheet("Other")["A1"] = "SECONDSHEETWORD"

    raw = _patch(_xlsx(build), {"xl/workbook.xml": lambda t: t.replace(
        'name="Other"', 'name="Data"')})
    _assert_doc(_extract("dups.xlsx", raw),
                ["[sheet: Data]\nFIRSTSHEETWORD", "[sheet: Data]\nSECONDSHEETWORD"],
                [PAGE_NOTE_XLSX])


# ---------------------------------------------------------------------------
# E7: the row cap
# ---------------------------------------------------------------------------


def test_e7_row_cap_note_names_what_was_lost(monkeypatch):
    """E7, pinned exactly with the cap biting inside Register: the in-page
    truncation line; a page for EVERY tab never opened, a chartsheet keeping
    its exact ``[chartsheet: <name>]`` label (review unverified: it read
    ``[sheet: ...]`` behind the cap); ONE marked note naming the truncated
    sheet and every sheet never opened; and no formula note, because the
    formula row was discarded to the cap, never shown."""
    monkeypatch.setattr(ex, "_XLSX_MAX_ROWS", 3)
    doc = _pages("18_workbook_constructs.xlsx")
    note = ("workbook truncated at 3 rows while reading 'Register'; the rest of "
            "that sheet, and the 2 sheet(s) never opened ('Chart1', 'Later'), "
            f"were not read ({UNREAD})")
    _assert_doc(doc,
                ["[sheet: Register]\nOSPREY\nRef\tAmount\tNote\n\t10\t15%\n\t20\t15.00%\n"
                 "[... workbook truncated at 3 rows]\n[comment on B2 by NIGHTJAR] MAGPIE\n"
                 "PUFFIN",
                 f"[chartsheet: Chart1]\n{CAP_LINE}",
                 f"[sheet: Later]\n{CAP_LINE}"],
                [note, PAGE_NOTE_XLSX])
    assert ex.has_evidence_marker(note)


def test_e7_xls_row_cap_gives_every_unopened_tab_its_page(monkeypatch):
    """E7 for ``.xls``: the same pages, labels and ONE note -- a chart tab
    behind the cap included."""
    monkeypatch.setattr(ex, "_XLSX_MAX_ROWS", 3)
    raw = make_fixtures.xls_bytes([
        {"name": "Ledger", "cells": [(r, 0, f"ROW{r + 1}", 0) for r in range(5)]},
        {"name": "Plot", "kind": "chart"},
        {"name": "Annex", "cells": [(0, 0, "YELLOWHAMMER", 0)]},
    ])
    _assert_doc(_extract("cap.xls", raw),
                ["[sheet: Ledger]\nROW1\nROW2\nROW3\n[... workbook truncated at 3 rows]",
                 f"[chartsheet: Plot]\n{CAP_LINE}", f"[sheet: Annex]\n{CAP_LINE}"],
                ["workbook truncated at 3 rows while reading 'Ledger'; the rest of "
                 "that sheet, and the 2 sheet(s) never opened ('Plot', 'Annex'), "
                 f"were not read ({UNREAD})", PAGE_NOTE_XLS])


def test_e7_cap_in_the_last_sheet_says_only_its_rest_was_lost(monkeypatch):
    """Review C: with no sheet after the truncated one, the note read "no
    further sheets not read". It says what was lost: the rest of that sheet."""
    monkeypatch.setattr(ex, "_XLSX_MAX_ROWS", 2)

    def build(wb):
        ws = wb.active
        ws.title = "Log"
        for i in range(1, 5):
            ws[f"A{i}"] = f"ENTRY{i}"

    _assert_doc(_extract("log.xlsx", _xlsx(build)),
                ["[sheet: Log]\nENTRY1\nENTRY2\n[... workbook truncated at 2 rows]"],
                ["workbook truncated at 2 rows while reading 'Log'; the rest of that "
                 f"sheet was not read ({UNREAD})", PAGE_NOTE_XLSX])


# ---------------------------------------------------------------------------
# E10: percentages
# ---------------------------------------------------------------------------


def test_e10_percentage_cell_reads_as_a_percentage():
    """E10: ``0%`` and ``0.00%`` on Register.

    Siblings (review unverified): a ``%`` escaped with a backslash is literal
    text Excel does not scale (``0\\%`` holding 15 read ``1500%``); ``?`` and
    ``#`` are digit placeholders; a negative keeps its sign; and only the
    format's FIRST section decides."""
    doc = _pages("18_workbook_constructs.xlsx")
    reg = _page_with(doc.pages, "[sheet: Register]")
    assert reg is not None, "no page starts with '[sheet: Register]'"
    _assert_found(reg.text, "\n\t10\t15%\n\t20\t15.00%\n", "both percentage cells. ")

    def build(wb):
        ws = wb.active
        ws.title = "Pct"
        for row, (value, fmt) in enumerate([
                (15, "0\\%"), (0.1525, "0.0?%"), (0.1525, "0.0#%"),
                (-0.125, "0.0%;[Red]-0.0%"), (0.15, "0.00;-0.00%"),
                (15, '0"%"')], start=1):
            ws[f"A{row}"] = value
            ws[f"A{row}"].number_format = fmt

    _assert_doc(_extract("pct.xlsx", _xlsx(build)),
                ["[sheet: Pct]\n15\n15.25%\n15.25%\n-12.5%\n0.15\n15"],
                [PAGE_NOTE_XLSX])


# ---------------------------------------------------------------------------
# E11: comments
# ---------------------------------------------------------------------------


def test_e11_cell_comment_is_read_after_the_sheets_rows():
    """E11: Register's page is pinned whole -- the comment line after the rows,
    before the footer.

    Siblings: comments print in CELL order whatever order the part holds
    them, each with its own author; rich-text runs join; and (review C) one
    comment whose ref is a range no longer drops every comment on the sheet."""
    doc = _pages("18_workbook_constructs.xlsx")
    reg = _page_with(doc.pages, "[sheet: Register]")
    assert reg is not None, "no page starts with '[sheet: Register]'"
    assert reg.text == REGISTER_PAGE, reg.text

    def build(wb):
        ws = wb.active
        ws.title = "Notes"
        ws["A1"], ws["B2"], ws["D5"] = "KINGLETROW", "MARTINROW", "DUNLINROW"
        ws["A1"].comment = openpyxl.comments.Comment("PLACEHOLDERA", "AUK")
        ws["B2"].comment = openpyxl.comments.Comment("PLACEHOLDERB", "AUK")

    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    comments = (
        f'<comments xmlns="{ns}"><authors><author>AUK</author>'
        '<author>SWIFTLET</author></authors><commentList>'
        '<comment ref="D5" authorId="1"><text><r><rPr><b/></rPr><t>SWIFTLET:</t></r>'
        '<r><t xml:space="preserve">&#10;LATERNOTE</t></r></text></comment>'
        '<comment ref="B2" authorId="0"><text><t>MARTIN</t></text></comment>'
        '<comment ref="A1:B1" authorId="0"><text><t>KINGLET</t></text></comment>'
        '</commentList></comments>')
    raw = _xlsx(build)
    part = next(n for n in _part_names(raw) if re.match(r"xl/comments/comment\d+\.xml$", n))
    _assert_doc(_extract("notes.xlsx", _patch(raw, {part: lambda t: comments})),
                ["[sheet: Notes]\nKINGLETROW\n\tMARTINROW\n[blank rows 3-4]\n\t\t\tDUNLINROW\n"
                 "[comment on A1:B1 by AUK] KINGLET\n"
                 "[comment on B2 by AUK] MARTIN\n"
                 "[comment on D5 by SWIFTLET] SWIFTLET: ¶ LATERNOTE"],
                [PAGE_NOTE_XLSX])


def test_e11_threaded_comments_are_credited_to_named_people():
    """Review unverified: Excel's default comment is THREADED -- the
    conversation in ``xl/threadedComments``, its people in ``xl/persons``, and
    a placeholder note credited to ``tc={id}`` for older readers. Only the
    placeholder was read, so no named person reached the page. Each thread
    entry is its own line credited to its person; the placeholder is not
    repeated; an ordinary note beside it still prints."""
    guid = "{00000000-0000-4000-8000-000000000001}"

    def build(wb):
        ws = wb.active
        ws.title = "Ledger"
        ws["A1"], ws["A2"] = "ALDER", "REEDROW"
        ws["A1"].comment = openpyxl.comments.Comment(
            "[Threaded comment]\n\nComment:\n    MAGPIE\nReplies:\n    NUTHATCH",
            "tc=" + guid)
        ws["A2"].comment = openpyxl.comments.Comment("BITTERN", "CRAKE")

    tc_ns = "http://schemas.microsoft.com/office/spreadsheetml/2018/threadedcomments"
    thread = (
        f'<ThreadedComments xmlns="{tc_ns}">'
        f'<threadedComment ref="A1" dT="2026-01-01T00:00:00.00" personId="{{P1}}" '
        f'id="{guid}"><text>MAGPIE</text></threadedComment>'
        f'<threadedComment ref="A1" dT="2026-01-02T00:00:00.00" personId="{{P2}}" '
        f'id="{{00000000-0000-4000-8000-000000000002}}" parentId="{guid}">'
        '<text>NUTHATCH</text></threadedComment></ThreadedComments>')
    persons = (f'<personList xmlns="{tc_ns}">'
               '<person displayName="NIGHTJAR" id="{P1}" userId="N" providerId="None"/>'
               '<person displayName="ORIOLE" id="{P2}" userId="O" providerId="None"/>'
               '</personList>')
    rel = ('<Relationship Id="rIdTC" Type="http://schemas.microsoft.com/office/'
           '2017/10/relationships/threadedComment" '
           'Target="../threadedComments/threadedComment1.xml"/></Relationships>')
    person_rel = ('<Relationship Id="rIdP" Type="http://schemas.microsoft.com/office/'
                  '2017/10/relationships/person" Target="persons/person.xml"/>'
                  '</Relationships>')
    raw = _patch(_xlsx(build), {
        "xl/worksheets/_rels/sheet1.xml.rels": lambda t: t.replace("</Relationships>", rel),
        "xl/_rels/workbook.xml.rels": lambda t: t.replace("</Relationships>", person_rel),
        "xl/threadedComments/threadedComment1.xml": lambda t: thread,
        "xl/persons/person.xml": lambda t: persons,
    })
    _assert_doc(_extract("threads.xlsx", raw),
                ["[sheet: Ledger]\nALDER\nREEDROW\n"
                 "[comment on A1 by NIGHTJAR] MAGPIE\n"
                 "[comment on A1 by ORIOLE] NUTHATCH\n"
                 "[comment on A2 by CRAKE] BITTERN"],
                [PAGE_NOTE_XLSX])


# ---------------------------------------------------------------------------
# E12: print header and footer
# ---------------------------------------------------------------------------


def test_e12_print_header_and_footer_bound_the_sheets_page():
    """E12: Register's header is the line right after ``[sheet: Register]``,
    exactly ``OSPREY`` (no code letter, no ``&C``), and its footer is the
    page's last line, exactly ``PUFFIN``, after the comment.

    Siblings (review B3): Excel's full code set leaves no stray letter --
    color ``&K`` (hex or theme), fonts, sizes, picture ``&G``, strikethrough
    ``&S``, super/subscript ``&X``/``&Y``, file path ``&Z`` and the rest;
    ``&&`` is ``&``; the left, center and right sections are separate lines;
    even-page and first-page variants are read; and a red Bates stamp in the
    footer's left section parses with its own prefix."""
    doc = _pages("18_workbook_constructs.xlsx")
    reg = _page_with(doc.pages, "[sheet: Register]")
    assert reg is not None, "no page starts with '[sheet: Register]'"
    lines = reg.text.split("\n")
    assert lines[1] == "OSPREY", lines
    assert lines[-1] == "PUFFIN", lines
    assert lines[-2] == "[comment on B2 by NIGHTJAR] MAGPIE", lines

    def build(wb):
        ws = wb.active
        ws.title = "Stamps"
        ws["A1"] = "STAMPROW"
        ws.oddHeader.center.text = "PLACEHOLDERHEAD"

    header_footer = (
        '<headerFooter differentOddEven="1" differentFirst="1">'
        '<oddHeader>&amp;L&amp;"Arial,Bold"&amp;KFF0000LEFTWORD&amp;CMID&amp;&amp;WORD'
        '&amp;R&amp;K04+000RIGHTWORD</oddHeader>'
        '<oddFooter>&amp;L&amp;KFF0000QXZ 000123&amp;RPage &amp;P</oddFooter>'
        '<evenHeader>&amp;C&amp;S&amp;X&amp;Y&amp;E&amp;O&amp;H&amp;G&amp;Z&amp;F'
        '&amp;A&amp;D&amp;T&amp;N&amp;B&amp;I&amp;U&amp;12EVENWORD</evenHeader>'
        '<firstFooter>&amp;CFIRSTFOOTWORD</firstFooter></headerFooter>')
    raw = _patch(_xlsx(build), {"xl/worksheets/sheet1.xml": lambda t: _set_header_footer(
        t, header_footer)})
    doc = _extract("stamps.xlsx", raw)
    _assert_doc(doc,
                ["[sheet: Stamps]\nLEFTWORD\nMID&WORD\nRIGHTWORD\nEVENWORD\nSTAMPROW\n"
                 "QXZ 000123\nPage\nFIRSTFOOTWORD"],
                [PAGE_NOTE_XLSX])
    parsed = bates._parse_line("QXZ 000123")
    assert (parsed.prefix, parsed.number) == ("QXZ", 123), parsed


# ---------------------------------------------------------------------------
# E13: hyperlinks
# ---------------------------------------------------------------------------


def test_e13_hyperlink_target_follows_the_cell_text():
    """E13: KESTREL's external target follows it as `` <URL>``.

    Siblings (review unverified): a link over a RANGE is one link, attached
    to the range's first cell only -- it was copied into every cell, making
    empty cells non-blank; a location fragment on an external link is kept;
    an internal link adds nothing; and a link on a cell the part holds no
    ``<c>`` for, on a row past the last one it holds, still prints in its
    column and row."""
    from openpyxl.worksheet.hyperlink import Hyperlink

    doc = _pages("18_workbook_constructs.xlsx")
    reg = _page_with(doc.pages, "[sheet: Register]")
    assert reg is not None, "no page starts with '[sheet: Register]'"
    assert "KESTREL <https://example.invalid/LARK>" in reg.text.split("\n"), reg.text

    def build(wb):
        ws = wb.active
        ws.title = "Links"
        ws["A1"] = "LINNET"
        ws.merge_cells("A1:C1")
        ws["A1"].hyperlink = "https://example.invalid/NODDY"
        ws["A3"] = "PIPIT"
        ws["A3"].hyperlink = "https://example.invalid/PIPIT.pdf"
        ws["A5"] = "WAGTAIL"
        ws["A5"].hyperlink = Hyperlink(ref="A5", location="'Links'!A1")

    r_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

    def links(xml: str) -> str:
        xml = re.sub(r'<hyperlink ([^>]*)ref="A1" ', r'<hyperlink \1ref="A1:C1" ', xml)
        xml = re.sub(r'<hyperlink ([^>]*)ref="A3" ', r'<hyperlink \1ref="A3" location="page=5" ',
                     xml)
        return xml.replace("</hyperlinks>", f'<hyperlink xmlns:r="{r_ns}" ref="C9" '
                           'r:id="rIdROWLESS"/></hyperlinks>')

    def rels(xml: str) -> str:
        return xml.replace("</Relationships>", (
            '<Relationship Id="rIdROWLESS" Type="http://schemas.openxmlformats.org/'
            'officeDocument/2006/relationships/hyperlink" '
            'Target="https://example.invalid/ROWLESS" TargetMode="External"/>'
            "</Relationships>"))

    raw = _patch(_xlsx(build), {"xl/worksheets/sheet1.xml": links,
                                "xl/worksheets/_rels/sheet1.xml.rels": rels})
    sheet_xml = zipfile.ZipFile(io.BytesIO(raw)).read("xl/worksheets/sheet1.xml").decode()
    assert 'ref="A1:C1"' in sheet_xml and 'ref="C9"' in sheet_xml, sheet_xml
    assert '<c r="B1"' not in sheet_xml and '<row r="9"' not in sheet_xml, sheet_xml
    _assert_doc(_extract("links.xlsx", raw),
                ["[sheet: Links]\nLINNET <https://example.invalid/NODDY>\n[blank row 2]\n"
                 "PIPIT <https://example.invalid/PIPIT.pdf#page=5>\n[blank row 4]\nWAGTAIL\n"
                 "[blank rows 6-8]\n\t\t<https://example.invalid/ROWLESS>"],
                [PAGE_NOTE_XLSX])


# ---------------------------------------------------------------------------
# Constructs outside E1-E15 that lost evidence silently
# ---------------------------------------------------------------------------


def _stale_dimension(xml: str) -> str:
    return re.sub(r'<dimension ref="[^"]*"/>', '<dimension ref="A1"/>', xml)


def test_a_stale_dimension_does_not_truncate_rows_or_columns():
    """Review unverified (predates the package): a writer can leave
    ``<dimension ref="A1"/>`` on a sheet holding far more; read-only openpyxl
    stops at it, and 11 of these 12 cells vanished with status FULL."""
    def build(wb):
        ws = wb.active
        ws.title = "Stale"
        for r in range(1, 5):
            for c in "ABC":
                ws[f"{c}{r}"] = f"VIREO{c}{r}"

    raw = _patch(_xlsx(build), {"xl/worksheets/sheet1.xml": _stale_dimension})
    _assert_doc(_extract("stale.xlsx", raw),
                ["[sheet: Stale]\n" + "\n".join(
                    "\t".join(f"VIREO{c}{r}" for c in "ABC") for r in range(1, 5))],
                [PAGE_NOTE_XLSX])


def test_hidden_rows_columns_and_sheets_are_read_and_disclosed():
    """Review unverified: hidden rows, hidden columns, hidden and very hidden
    sheets were read with nothing saying they were hidden. They are still
    read, and an unmarked note says so, in both formats."""
    def build(wb):
        ws = wb.active
        ws.title = "Shown"
        ws["A1"], ws["C1"], ws["A2"] = "GOLDCREST", "HIDDENCOLWORD", "HIDDENROWWORD"
        ws.row_dimensions[2].hidden = True
        ws.column_dimensions["C"].hidden = True
        tucked = wb.create_sheet("Tucked")
        tucked["A1"] = "TUCKEDWORD"
        tucked.sheet_state = "hidden"
        buried = wb.create_sheet("Buried")
        buried["A1"] = "BURIEDWORD"
        buried.sheet_state = "veryHidden"

    pages = ["[sheet: Shown]\nGOLDCREST\t\tHIDDENCOLWORD\nHIDDENROWWORD",
             "[sheet: Tucked]\nTUCKEDWORD", "[sheet: Buried]\nBURIEDWORD"]
    notes = ["sheet 'Shown' marks 1 row(s) and 1 column(s) hidden; hidden cells "
             "are read like any other",
             "sheet 'Tucked' is hidden in the workbook; it was read like any other sheet",
             "sheet 'Buried' is very hidden in the workbook; it was read like any "
             "other sheet"]
    _assert_doc(_extract("hidden.xlsx", _xlsx(build)), pages, notes + [PAGE_NOTE_XLSX])
    raw = make_fixtures.xls_bytes([
        {"name": "Shown", "hidden_rows": [1], "hidden_cols": [2],
         "cells": [(0, 0, "GOLDCREST", 0), (0, 2, "HIDDENCOLWORD", 0),
                   (1, 0, "HIDDENROWWORD", 0)]},
        {"name": "Tucked", "visibility": 1, "cells": [(0, 0, "TUCKEDWORD", 0)]},
        {"name": "Buried", "visibility": 2, "cells": [(0, 0, "BURIEDWORD", 0)]},
    ])
    _assert_doc(_extract("hidden.xls", raw), pages, notes + [PAGE_NOTE_XLS])
    assert not any(ex.has_evidence_marker(n) for n in notes)


# ---------------------------------------------------------------------------
# How the per-sheet extras are read
# ---------------------------------------------------------------------------


def test_sheet_extras_never_parse_the_whole_worksheet_part(monkeypatch):
    """Review B1: header, footer and hyperlinks were found by parsing the
    WHOLE worksheet part into a tree, so memory grew with the sheet however
    low the row cap. With whole-part parsing made to fail, every extra still
    reads: the part is streamed."""
    real_fromstring = ET.fromstring

    def no_whole_sheet(data, *args, **kwargs):
        if b"<sheetData" in (data if isinstance(data, bytes) else data.encode()):
            raise MemoryError("a worksheet part was parsed whole")
        return real_fromstring(data, *args, **kwargs)

    monkeypatch.setattr(ET, "fromstring", no_whole_sheet)
    doc = _pages("18_workbook_constructs.xlsx")
    _assert_doc(doc, [REGISTER_PAGE, "[chartsheet: Chart1]", "[sheet: Later]\nQUETZAL"],
                [CHART1_NOTE, FORMULA_NOTE_ONE, PAGE_NOTE_XLSX])


def test_sheet_extras_read_failure_is_a_marked_note(monkeypatch):
    """Review B1: a failure reading a sheet's extras (a real MemoryError, on a
    loaded machine) was swallowed by a bare ``except: pass`` -- header,
    footer, links and comments gone, status FULL, no note. The rows and the
    separately stored comments still read, and each failed read is a MARKED
    note naming the sheet and what was not read."""
    real_fromstring, real_iterparse = ET.fromstring, ET.iterparse

    def failing_fromstring(data, *args, **kwargs):
        if b"<sheetData" in (data if isinstance(data, bytes) else data.encode()):
            raise MemoryError()
        return real_fromstring(data, *args, **kwargs)

    def failing_iterparse(source, *args, **kwargs):
        if "worksheets/" in getattr(source, "name", ""):
            raise MemoryError()
        return real_iterparse(source, *args, **kwargs)

    monkeypatch.setattr(ET, "fromstring", failing_fromstring)
    monkeypatch.setattr(ET, "iterparse", failing_iterparse)
    doc = _pages("18_workbook_constructs.xlsx")
    extras_note = ("its print header/footer, hyperlinks and hidden rows and columns "
                   f"could not be read: MemoryError ({UNREAD})")
    _assert_doc(doc,
                ["[sheet: Register]\nRef\tAmount\tNote\n\t10\t15%\n\t20\t15.00%\n"
                 "HOLLY ¶ IVY\t=SUM(B2:B3)\t\tTAB STOP\n[blank rows 5-6]\nJUNIPER\nKESTREL\n"
                 "[comment on B2 by NIGHTJAR] MAGPIE",
                 "[chartsheet: Chart1]", "[sheet: Later]\nQUETZAL"],
                [f"sheet 'Register': {extras_note}",
                 "sheet 'Register': its own cell XML could not be read (MemoryError), "
                 "so a formula whose stored result is empty may be reported as having "
                 f"no stored value ({UNREAD})",
                 CHART1_NOTE,
                 f"sheet 'Later': {extras_note}",
                 FORMULA_NOTE_ONE, PAGE_NOTE_XLSX])
    assert all(ex.has_evidence_marker(n) for n in doc.notes[:4]), doc.notes


def test_formula_and_sheet_list_read_failures_are_marked_notes(monkeypatch):
    """The two other bare fallbacks. The second, formula-text reading of the
    workbook failing left a formula with no stored value blank, with no note.
    The package's own sheet list failing to parse fell back to openpyxl's
    worksheet list -- the chartsheet's page gone, no sheet's header read, no
    note."""
    real_load = openpyxl.load_workbook

    def no_formula_reading(*args, **kwargs):
        if kwargs.get("data_only") is False:
            raise MemoryError()
        return real_load(*args, **kwargs)

    monkeypatch.setattr(openpyxl, "load_workbook", no_formula_reading)
    doc = _pages("18_workbook_constructs.xlsx")
    formula_note = ("the workbook's formulas could not be read (MemoryError); a formula "
                    "cell with no stored value shows blank, so 'a formula cell had no "
                    "stored value' cannot be ruled out")
    assert list(doc.notes) == [formula_note, CHART1_NOTE, PAGE_NOTE_XLSX], doc.notes
    assert ex.has_evidence_marker(formula_note)
    assert "HOLLY ¶ IVY\t\t\tTAB STOP" in doc.pages[0].text.split("\n"), doc.pages[0].text
    monkeypatch.setattr(openpyxl, "load_workbook", real_load)

    real_fromstring = ET.fromstring

    def no_sheet_list(data, *args, **kwargs):
        if b"<sheets>" in (data if isinstance(data, bytes) else data.encode()):
            raise MemoryError()
        return real_fromstring(data, *args, **kwargs)

    monkeypatch.setattr(ET, "fromstring", no_sheet_list)
    doc = _pages("18_workbook_constructs.xlsx")
    list_note = ("the workbook's own sheet list could not be read (MemoryError); tabs "
                 "follow openpyxl's list, and no sheet's print header/footer, "
                 f"hyperlinks or comments were read ({UNREAD})")
    first_lines = [p.text.split("\n")[0] for p in doc.pages]
    assert first_lines == ["[sheet: Register]", "[chartsheet: Chart1]", "[sheet: Later]"], (
        first_lines)
    assert list(doc.notes) == [list_note, CHART1_NOTE, FORMULA_NOTE_ONE, PAGE_NOTE_XLSX], (
        doc.notes)


# ---------------------------------------------------------------------------
# Derived class test: no cell's own literal text may be lost, whatever else
# happens to it.
# ---------------------------------------------------------------------------


def _assert_every_string_survives(name: str, raw: bytes) -> int:
    """Every string in ``xl/sharedStrings.xml`` (each ``si``'s text, its runs
    joined) and every inline-string cell of every WORKSHEET part appears,
    E5-transformed and normalized with dociq's own ``normalize``, in the
    extracted text. Returns how many strings were checked."""
    doc = _extract(name, raw)
    full_text = normalize("\n".join(p.text for p in doc.pages))
    strings: list[str] = []
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names = z.namelist()
        if "xl/sharedStrings.xml" in names:
            sst = z.read("xl/sharedStrings.xml").decode("utf-8")
            for si in re.findall(r"<si>(.*?)</si>", sst, re.S):
                runs = re.findall(r"<t[^>]*>(.*?)</t>", si, re.S)
                strings.append(html.unescape("".join(runs)))
        for part in names:
            if not re.match(r"xl/worksheets/sheet\d+\.xml$", part):
                continue
            xml = z.read(part).decode("utf-8")
            for is_block in re.findall(r"<is>(.*?)</is>", xml, re.S):
                runs = re.findall(r"<t[^>]*>(.*?)</t>", is_block, re.S)
                strings.append(html.unescape("".join(runs)))
    assert strings, f"expected at least one shared or inline string in {name}"
    for s in strings:
        # E5 is an exact, disclosed transformation of a cell's own line breaks
        # and tabs, not a loss, so each string is looked for through it.
        e5 = (s.replace("\r\n", "\n").replace("\r", "\n")
               .replace("\n", " ¶ ").replace("\t", " "))
        needle = normalize(e5)
        pos = full_text.find(needle)
        assert pos != -1, (
            f"{name}: cell text {s!r} (E5-transformed {e5!r}, normalized "
            f"{needle!r}) not found anywhere in the extracted text (str.find -> {pos})")
    return len(strings)


def test_every_shared_or_inline_string_cell_survives_extraction():
    """No shared or inline string cell's text is lost, in fixture 18 (whose
    strings openpyxl writes inline) AND in a workbook holding its strings in
    ``xl/sharedStrings.xml`` the way Excel writes them -- a branch fixture 18
    never reached (review C). That workbook also declares a stale
    ``<dimension>``, the case where strings vanished."""
    path = FIXTURES / "18_workbook_constructs.xlsx"
    assert _assert_every_string_survives(path.name, path.read_bytes()) >= 7

    def build(wb):
        ws = wb.active
        ws.title = "Shared"
        ws["A1"] = "PLACEHOLDER"

    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    sst = (f'<sst xmlns="{ns}" count="3" uniqueCount="3">'
           '<si><t>SHAREDWORD</t></si>'
           '<si><r><t>RUNPART</t></r><r><t>JOINED</t></r></si>'
           '<si><t xml:space="preserve">LINEONE&#10;LINETWO</t></si></sst>')
    sheet_data = ('<sheetData><row r="1"><c r="A1" t="s"><v>0</v></c>'
                  '<c r="B1" t="s"><v>1</v></c></row>'
                  '<row r="3"><c r="C3" t="s"><v>2</v></c></row></sheetData>')
    raw = _patch(_xlsx(build), {
        "xl/sharedStrings.xml": lambda t: sst,
        "xl/worksheets/sheet1.xml": lambda t: _stale_dimension(
            re.sub(r"<sheetData>.*?</sheetData>", sheet_data, t, flags=re.S)),
        "xl/_rels/workbook.xml.rels": lambda t: t.replace(
            "</Relationships>",
            '<Relationship Id="rIdSST" Type="http://schemas.openxmlformats.org/'
            'officeDocument/2006/relationships/sharedStrings" '
            'Target="sharedStrings.xml"/></Relationships>'),
        "[Content_Types].xml": lambda t: t.replace(
            "</Types>",
            '<Override PartName="/xl/sharedStrings.xml" ContentType="application/'
            'vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/></Types>'),
    })
    assert _assert_every_string_survives("shared.xlsx", raw) == 3
