"""Spreadsheet fidelity: fixtures and failing tests, stage 1 of 2.

The 2026-09-10 fidelity sweep found that ``.xlsx``/``.xls`` extraction
(``src/dociq/ingest/extract.py``: ``_extract_xlsx``, ``_xlsx_cell``,
``_extract_xls``) loses or changes a cell's value, or the MEANING of its
value, without a word said -- the confirmed defect (E1) together with its
class. This stage writes the fixtures and the tests only; the fix is stage 2.

Every test below asserts the FIXED behaviour stage 2 must produce, against
the fixtures added to ``tests/fixtures/make_fixtures.py``
(``workbook_constructs`` -> ``18_workbook_constructs.xlsx``,
``legacy_workbook`` -> ``19_legacy_workbook.xls``). Each is named for the
finding it covers and is expected to FAIL against today's (unfixed) code.
Wording marked **exact** in the brief is asserted verbatim; everything else
is a reasonable stand-in the fix must satisfy, not a claim about its final
literal phrasing.

Every sentinel word is invented for this fixture and is not a substring of
any other sentinel (client-data rule D-12).
"""

from __future__ import annotations

import html
import re
import zipfile

from dociq.ingest import extract as ex
from dociq.ingest.pagemodel import normalize

from .conftest import FIXTURES


def _pages(name: str, opt: ex.ExtractOptions | None = None):
    path = FIXTURES / name
    return ex.extract(path.name, path.read_bytes(), opt)


def _page_with(pages, prefix: str):
    """The first page whose text starts with ``prefix``, or ``None``.

    Never indexes into ``pages`` directly -- a wrong page count (exactly
    what several of these findings produce) must fail an assertion with a
    clear message, not raise ``IndexError`` before the test gets to say why.
    """
    return next((p for p in pages if p.text.startswith(prefix)), None)


def _assert_found(text: str, sentinel: str, note: str = "") -> None:
    """``sentinel in text``, via ``str.find`` so a miss reports its position
    (-1) rather than raising -- ``re.search(...).group()`` or ``str.index``
    would blow up on exactly the failure this test exists to observe."""
    pos = text.find(sentinel)
    assert pos != -1, (
        f"{sentinel!r} not found in extracted text (str.find -> {pos}). "
        f"{note}Text was:\n{text!r}")


# ---------------------------------------------------------------------------
# One test per finding id.
# ---------------------------------------------------------------------------


def test_e1_formula_with_no_stored_value_shows_the_formula():
    """E1 (confirmed): ``data_only=True`` never computes a formula, so
    ``_xlsx_cell(None)`` renders Register's B4 (``=SUM(B2:B3)``) as an empty
    string -- the formula vanishes with no trace and no note at all."""
    doc = _pages("18_workbook_constructs.xlsx")
    reg = _page_with(doc.pages, "[sheet: Register]")
    assert reg is not None, "no page starts with '[sheet: Register]'"
    _assert_found(reg.text, "=SUM(B2:B3)", "the formula cell's own formula. ")
    # Stage 2 correction: checking the joined notes for two bare words lets
    # two UNRELATED notes satisfy the assertion together, and never checks
    # that the note is actually an evidence marker at all. The specification
    # asks for ONE note that is BOTH marked (``has_evidence_marker``) and
    # carries both words.
    marked = [n for n in doc.notes if ex.has_evidence_marker(n)]
    hit = next((n for n in marked if "formula" in n and "no stored value" in n),
               None)
    assert hit is not None, (
        f"expected ONE evidence-marked note saying how many formula cells "
        f"had no stored value; evidence-marked notes were {marked!r}; all "
        f"notes were {doc.notes!r}")


def test_e2_xls_date_formatted_cell_reads_as_iso_date():
    """E2 (confirmed): ``xlrd``'s ``row_values()`` never converts a
    DATE-typed cell -- the raw Excel serial comes back as a bare float, so
    B1 (date-formatted, holding 2024-07-16's serial) reads '45489.0'."""
    doc = _pages("19_legacy_workbook.xls")
    sheet = _page_with(doc.pages, "[sheet: Legacy]")
    assert sheet is not None, "no page starts with '[sheet: Legacy]'"
    _assert_found(sheet.text, "2024-07-16", "the ISO-rendered date. ")


def test_e3_xls_boolean_and_error_render_their_own_text():
    """E3 (confirmed): ``xlrd``'s ``row_values()`` returns a BOOLEAN cell as
    a bare 0/1 and an ERROR cell as its raw numeric code, so C1 (TRUE) reads
    '1' and D1 (``#DIV/0!``) reads '7' today."""
    doc = _pages("19_legacy_workbook.xls")
    sheet = _page_with(doc.pages, "[sheet: Legacy]")
    assert sheet is not None, "no page starts with '[sheet: Legacy]'"
    _assert_found(sheet.text, "TRUE", "the boolean cell's own text. ")
    _assert_found(sheet.text, "#DIV/0!", "the error cell's own text. ")


def test_e4_blank_rows_collapse_to_one_marked_line():
    """E4 (confirmed): a blank row fails ``if not any(cells)`` and is
    silently ``continue``d, so Excel rows 5 and 6 (both blank) leave no
    trace and every row number after them is unverifiable."""
    doc = _pages("18_workbook_constructs.xlsx")
    reg = _page_with(doc.pages, "[sheet: Register]")
    assert reg is not None, "no page starts with '[sheet: Register]'"
    _assert_found(reg.text, "[blank rows 5-6]", "the collapsed blank-row marker. ")


def test_e5_newline_and_tab_inside_a_cell_stay_on_one_line():
    """E5 (unverified by the sweep; CONFIRMED here): a cell's own embedded
    newline is indistinguishable from a row boundary once the worksheet
    block is joined and normalized, so row 4 (A4='HOLLY\\nIVY',
    D4='TAB\\tSTOP') prints as two lines today, with the tab-stop cell's
    text glued onto the wrong one."""
    doc = _pages("18_workbook_constructs.xlsx")
    reg = _page_with(doc.pages, "[sheet: Register]")
    assert reg is not None, "no page starts with '[sheet: Register]'"
    lines = reg.text.split("\n")
    hit = next((ln for ln in lines if "HOLLY ¶ IVY" in ln), None)
    assert hit is not None, (
        f"no single line contains 'HOLLY ¶ IVY' (newline -> ' ¶ '); "
        f"lines were {lines!r}")
    assert "TAB STOP" in hit, (
        f"the row carrying 'HOLLY ¶ IVY' must also carry the "
        f"tab-collapsed 'TAB STOP' (tab -> one space) on the SAME line; "
        f"line was {hit!r}")


def test_e6_chartsheet_gets_its_own_page_in_tab_order():
    """E6 (unverified by the sweep; CONFIRMED here): ``Workbook.worksheets``
    silently omits chartsheets, so Chart1 gets no page at all today and
    Later -- which follows it in the workbook's own tab order -- shifts up
    to page 2 instead of page 3."""
    doc = _pages("18_workbook_constructs.xlsx")
    chart_page = _page_with(doc.pages, "[chartsheet: Chart1]")
    first_lines = [p.text.splitlines()[0] if p.text else "" for p in doc.pages]
    assert chart_page is not None, (
        f"no page starts with '[chartsheet: Chart1]'; page(s) were "
        f"{first_lines!r}")
    reg = _page_with(doc.pages, "[sheet: Register]")
    later = _page_with(doc.pages, "[sheet: Later]")
    assert reg is not None and later is not None, (
        f"expected Register and Later pages too; page(s) were {first_lines!r}")
    assert reg.page_no < chart_page.page_no < later.page_no, (
        f"expected Register < Chart1 < Later in tab order; got page_no "
        f"{reg.page_no}, {chart_page.page_no}, {later.page_no}")
    # Stage 2 correction: the joined-notes-blob check lets an unrelated
    # marked note supply "not read" and an unrelated word elsewhere supply
    # "chart". The specification asks for ONE marked note naming the
    # chartsheet itself.
    marked = [n for n in doc.notes if ex.has_evidence_marker(n)]
    hit = next((n for n in marked if "Chart1" in n and "not read" in n), None)
    assert hit is not None, (
        f"expected ONE evidence-marked note naming 'Chart1' and saying it "
        f"was not read; evidence-marked notes were {marked!r}; all notes "
        f"were {doc.notes!r}")


def test_e7_row_cap_note_names_what_was_lost(monkeypatch):
    """E7 (unverified by the sweep; CONFIRMED here): the truncation note
    names neither the sheet that was cut off nor the sheet(s) never opened
    -- an operator sees a row count and nothing about which evidence it
    cost. ``_XLSX_MAX_ROWS`` is monkeypatched small so the cap bites inside
    Register, the fixture's first sheet."""
    monkeypatch.setattr(ex, "_XLSX_MAX_ROWS", 3)
    doc = _pages("18_workbook_constructs.xlsx")
    # Stage 2 correction: checking the joined notes for two bare words lets
    # 'Register' come from one note and 'Later' from an unrelated one, and
    # never checks that either note is actually marked. The specification
    # asks for ONE marked note naming both the truncated sheet and the
    # sheet(s) never opened.
    marked = [n for n in doc.notes if ex.has_evidence_marker(n)]
    hit = next((n for n in marked if "Register" in n and "Later" in n), None)
    assert hit is not None, (
        f"expected ONE evidence-marked note naming both 'Register' "
        f"(truncated) and 'Later' (never opened); evidence-marked notes "
        f"were {marked!r}; all notes were {doc.notes!r}")


def test_e10_percentage_cell_reads_as_a_percentage():
    """E10 (unverified by the sweep; CONFIRMED here): ``iter_rows(values_only=True)``
    discards ``number_format`` entirely, so C2 (0.15, formatted '0%') and C3
    (the same 0.15, formatted '0.00%') both read as the bare decimal '0.15'
    today -- indistinguishable from each other and from a fraction."""
    doc = _pages("18_workbook_constructs.xlsx")
    reg = _page_with(doc.pages, "[sheet: Register]")
    assert reg is not None, "no page starts with '[sheet: Register]'"
    _assert_found(reg.text, "15%", "the '0%'-formatted cell. ")
    _assert_found(reg.text, "15.00%", "the '0.00%'-formatted cell. ")


def test_e11_cell_comment_is_read_after_the_sheets_rows():
    """E11 (unverified by the sweep; CONFIRMED here): cell comments are
    never read at all -- ``iter_rows(values_only=True)`` cannot see them and
    nothing else in ``_extract_xlsx`` looks for them -- so B2's comment
    ('MAGPIE' by 'NIGHTJAR') is absent from the page today."""
    doc = _pages("18_workbook_constructs.xlsx")
    reg = _page_with(doc.pages, "[sheet: Register]")
    assert reg is not None, "no page starts with '[sheet: Register]'"
    _assert_found(reg.text, "[comment on B2 by NIGHTJAR] MAGPIE",
                  "the marked comment line. ")
    lines = reg.text.split("\n")
    comment_idx = next((i for i, ln in enumerate(lines) if "MAGPIE" in ln), -1)
    kestrel_idx = next((i for i, ln in enumerate(lines) if "KESTREL" in ln), -1)
    assert comment_idx != -1 and kestrel_idx != -1, (
        f"expected both the comment line and 'KESTREL' present; lines "
        f"were {lines!r}")
    assert comment_idx > kestrel_idx, (
        f"the comment line (index {comment_idx}) must come AFTER the "
        f"sheet's own rows (KESTREL at index {kestrel_idx})")


def test_e12_print_header_and_footer_bound_the_sheets_page():
    """E12 (unverified by the sweep; CONFIRMED here): print header/footer
    text lives only on the non-read-only worksheet object, which
    ``_extract_xlsx`` (``read_only=True``) never opens -- 'OSPREY' and
    'PUFFIN' appear nowhere in the page today."""
    doc = _pages("18_workbook_constructs.xlsx")
    reg = _page_with(doc.pages, "[sheet: Register]")
    assert reg is not None, "no page starts with '[sheet: Register]'"
    header_pos = reg.text.find("OSPREY")
    footer_pos = reg.text.rfind("PUFFIN")
    assert header_pos != -1, f"'OSPREY' not found; text was {reg.text!r}"
    assert footer_pos != -1, f"'PUFFIN' not found; text was {reg.text!r}"
    ref_pos = reg.text.find("Ref")
    assert ref_pos != -1 and header_pos < ref_pos, (
        f"the header text (pos {header_pos}) must precede the sheet's own "
        f"first data ('Ref' at {ref_pos})")
    kestrel_pos = reg.text.find("KESTREL")
    assert kestrel_pos != -1 and footer_pos > kestrel_pos, (
        f"the footer text (pos {footer_pos}) must follow the sheet's own "
        f"rows ('KESTREL' at {kestrel_pos})")


def test_e13_hyperlink_target_follows_the_cell_text():
    """E13 (unverified by the sweep; CONFIRMED here): a hyperlink target
    lives in the worksheet's relationship part, which the ``read_only=True``
    streaming reader never touches -- KESTREL's link is dropped without a
    trace today."""
    doc = _pages("18_workbook_constructs.xlsx")
    reg = _page_with(doc.pages, "[sheet: Register]")
    assert reg is not None, "no page starts with '[sheet: Register]'"
    # Stage 2 correction: the specification (stage 1 brief) is ` <URL>`,
    # matching the Word package's own hyperlink rendering -- angle brackets
    # around the target, not a bare space-separated URL.
    _assert_found(reg.text, "KESTREL <https://example.invalid/LARK>",
                  "the cell text followed by its bracketed hyperlink target. ")


def test_e15_xls_whole_number_reads_as_an_integer():
    """E15 (unverified by the sweep; CONFIRMED here): ``xlrd`` always
    returns a NUMBER cell as a Python float, so E1 (the whole number 5)
    prints as '5.0' today."""
    doc = _pages("19_legacy_workbook.xls")
    sheet = _page_with(doc.pages, "[sheet: Legacy]")
    assert sheet is not None, "no page starts with '[sheet: Legacy]'"
    row0 = next((ln for ln in sheet.text.split("\n") if "RAVENXLS" in ln), None)
    assert row0 is not None, f"no line contains 'RAVENXLS'; text was {sheet.text!r}"
    cells = row0.split("\t")
    assert cells[-1] == "5", (
        f"expected the last cell of the RAVENXLS row to read '5', got "
        f"{cells[-1]!r} (full row: {cells!r})")


# ---------------------------------------------------------------------------
# Derived class test: no cell's own literal text may be lost, whatever else
# happens to it. Not tied to one finding id -- it is expected to PASS today,
# because none of E1-E15 touches an ordinary text cell's own value.
# ---------------------------------------------------------------------------


def test_every_shared_or_inline_string_cell_survives_extraction():
    """Every string in ``xl/sharedStrings.xml`` (each ``si``'s text, its
    runs joined) and every inline-string cell of every WORKSHEET part (never
    a chartsheet part) must appear, whitespace normalized with dociq's own
    ``normalize``, in the extracted text of ``18_workbook_constructs.xlsx``.

    A cell's plain text surviving is the property every other finding in
    this file takes for granted -- this is the backstop that would catch a
    regression in THAT, independent of any one finding's fix."""
    path = FIXTURES / "18_workbook_constructs.xlsx"
    doc = _pages("18_workbook_constructs.xlsx")
    full_text = normalize("\n".join(p.text for p in doc.pages))

    strings: list[str] = []
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if "xl/sharedStrings.xml" in names:
            sst = z.read("xl/sharedStrings.xml").decode("utf-8")
            for si in re.findall(r"<si>(.*?)</si>", sst, re.S):
                runs = re.findall(r"<t[^>]*>(.*?)</t>", si, re.S)
                strings.append(html.unescape("".join(runs)))
        for name in names:
            if not re.match(r"xl/worksheets/sheet\d+\.xml$", name):
                continue
            xml = z.read(name).decode("utf-8")
            for is_block in re.findall(r"<is>(.*?)</is>", xml, re.S):
                runs = re.findall(r"<t[^>]*>(.*?)</t>", is_block, re.S)
                strings.append(html.unescape("".join(runs)))

    assert strings, "expected at least one shared or inline string in the fixture"
    for s in strings:
        # Stage 2 correction (a fifth, beyond the brief's named four -- see
        # the stage 2 report): A4 ('HOLLY\nIVY') and D4 ('TAB\tSTOP') are
        # stored with a REAL newline/tab inside their <is> text (confirmed by
        # inspecting the built fixture directly -- there is no
        # xl/sharedStrings.xml at all; every string here is inline). Today
        # those raw control characters happen to survive extraction by
        # accident, because nothing yet touches a cell's own characters. E5
        # is an EXACT, disclosed transformation of that same whitespace
        # (newline -> ' ¶ ', tab -> one space) -- not a loss -- so a
        # backstop for "no cell's own text is lost" must recognise the text
        # through that one named lens, the same way test_e5 already does,
        # rather than demand the pre-E5 control characters verbatim.
        e5 = (s.replace("\r\n", "\n").replace("\r", "\n")
               .replace("\n", " ¶ ").replace("\t", " "))
        needle = normalize(e5)
        pos = full_text.find(needle)
        assert pos != -1, (
            f"cell text {s!r} (E5-transformed {e5!r}, normalized {needle!r}) "
            f"not found anywhere in the extracted text (str.find -> {pos})")
