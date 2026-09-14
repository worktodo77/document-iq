"""The Word fidelity package, stage 1: fixture and RED tests only.

Every test in this file is written against the CONTRACT the Word spec makes
true (every character in a Word document's text-bearing parts reaches
the page text, or is named in a note carrying an evidence marker) and MUST
fail against today's ``_extract_docx`` -- which emits paragraphs, then every
table, then one boilerplate note claiming the file has no page boundaries.
This file is the target for the next package, not a regression suite: do not
"fix" a failure here by loosening an assertion, and do not touch
``src/dociq/ingest/extract.py`` from this file.

One behaviour per test, per the spec. Every assertion uses ``str.find``/
``str.count`` and reports every position it computed, so a failure here is
always an assertion with a diagnostic message -- never a bare ``ValueError``
or ``IndexError`` from a sentinel that was never found.
"""

from __future__ import annotations

import io
import re
import zipfile

import pytest
from lxml import etree

from dociq.identify.bates import detect_candidates
from dociq.ingest import extract as ex
from dociq.ingest.pagemodel import normalize

from .conftest import FIXTURES
from .fixtures import document


def _pages(name: str, opt: ex.ExtractOptions | None = None):
    path = FIXTURES / name
    return ex.extract(path.name, path.read_bytes(), opt)


def _page_text(name: str) -> str:
    got = _pages(name)
    assert got.pages, f"{name} produced no pages at all: notes={got.notes!r}"
    return got.pages[0].text


def _positions(text: str, *sentinels: str) -> dict:
    return {s: text.find(s) for s in sentinels}


# ---------------------------------------------------------------------------
# 1. Body order
# ---------------------------------------------------------------------------


def test_body_order_follows_document_order_not_paragraphs_then_tables():
    text = _page_text("16_word_constructs.docx")
    order = ["AARDVARK", "BADGER", "CARIBOU", "DINGO", "ELAND", "FERRET",
             "GAZELLE", "HERON", "IBEX", "JACKAL", "LYNX", "MARMOT", "OCELOT",
             "QUAIL", "RAVEN", "TAPIR", "ALPACA"]
    pos = _positions(text, *order)
    found = [pos[s] for s in order]
    assert all(p != -1 for p in found) and found == sorted(found), (
        f"expected this sentinel order (document order, not "
        f"paragraphs-then-tables): {order}; positions found: {pos}")


# ---------------------------------------------------------------------------
# 2. Merged cells are not copied
# ---------------------------------------------------------------------------


def test_merged_cells_are_not_copied():
    text = _page_text("16_word_constructs.docx")
    n_badger, n_caribou = text.count("BADGER"), text.count("CARIBOU")
    assert n_badger == 1 and n_caribou == 1, (
        f"BADGER (gridSpan=2 cell) occurred {n_badger} time(s); CARIBOU "
        f"(vMerge restart cell) occurred {n_caribou} time(s); a spanned or "
        f"continuation grid slot must not repeat its neighbour's text")


# ---------------------------------------------------------------------------
# 3. Nested table read
# ---------------------------------------------------------------------------


def test_nested_table_is_read():
    text = _page_text("16_word_constructs.docx")
    i = text.find("FERRET")
    assert i != -1, (
        f"FERRET (the nested table's only cell) is not in the page text "
        f"(position {i}): {text!r}")


# ---------------------------------------------------------------------------
# 4. Content controls read
# ---------------------------------------------------------------------------


def test_content_controls_are_read():
    text = _page_text("16_word_constructs.docx")
    i_heron, i_ibex = text.find("HERON"), text.find("IBEX")
    assert i_heron != -1 and i_ibex != -1, (
        f"HERON (block content control) at {i_heron}; IBEX (inline content "
        f"control) at {i_ibex}; both must be read")


# ---------------------------------------------------------------------------
# 5. Tracked changes, DEFAULT view
# ---------------------------------------------------------------------------


def test_tracked_changes_default_view_keeps_insertions_and_discloses_omitted_deletions():
    got = _pages("16_word_constructs.docx")
    text = got.pages[0].text
    i_jackal, i_koala = text.find("JACKAL"), text.find("KOALA")
    marker_note = next(
        (n for n in got.notes if ex.has_evidence_marker(n) and "delet" in n.lower()),
        None,
    )
    assert i_jackal != -1 and i_koala == -1 and marker_note is not None, (
        f"JACKAL (tracked insertion) must be present at a real position, got "
        f"{i_jackal}; KOALA (tracked deletion) must be absent from the "
        f"default view, got {i_koala}; a marked note must say deleted text "
        f"was not shown, got {marker_note!r}; all notes={got.notes!r}")


# ---------------------------------------------------------------------------
# 6. Fields
# ---------------------------------------------------------------------------


def test_fields_show_cached_and_complex_results_not_instruction_codes():
    text = _page_text("16_word_constructs.docx")
    i_lynx = text.find("LYNX")
    i_marmot = text.find("MARMOT")
    i_narwhal = text.find("NARWHAL")
    assert i_lynx != -1 and i_marmot != -1 and i_narwhal == -1, (
        f"LYNX (fldSimple cached result) at {i_lynx}; MARMOT (complex field "
        f"result run) at {i_marmot}; NARWHAL (the field's own instruction "
        f"code, must be excluded) at {i_narwhal}")


# ---------------------------------------------------------------------------
# 7. Hyperlink
# ---------------------------------------------------------------------------


def test_external_hyperlink_target_follows_its_display_text():
    text = _page_text("16_word_constructs.docx")
    i_ocelot, i_pelican = text.find("OCELOT"), text.find("PELICAN")
    assert i_ocelot != -1 and i_pelican != -1 and i_pelican > i_ocelot, (
        f"OCELOT (hyperlink display text) at {i_ocelot}; PELICAN (from the "
        f"hyperlink's external target, https://example.invalid/PELICAN) at "
        f"{i_pelican}; PELICAN must follow OCELOT")


# ---------------------------------------------------------------------------
# 8. Text box
# ---------------------------------------------------------------------------


def test_text_box_is_read_once_between_its_anchor_and_the_next_paragraph():
    text = _page_text("16_word_constructs.docx")
    n_raven = text.count("RAVEN")
    i_quail, i_raven, i_tapir = (text.find("QUAIL"), text.find("RAVEN"),
                                 text.find("TAPIR"))
    assert (n_raven == 1 and i_quail != -1 and i_raven != -1 and i_tapir != -1
            and i_quail < i_raven < i_tapir), (
        f"RAVEN occurred {n_raven} time(s) (it is stored twice in the XML, "
        f"once as mc:Choice and once as mc:Fallback, and must be read only "
        f"once); QUAIL at {i_quail}, RAVEN at {i_raven}, TAPIR at {i_tapir} "
        f"-- expected QUAIL < RAVEN < TAPIR")


# ---------------------------------------------------------------------------
# 9. Notes and comments
# ---------------------------------------------------------------------------


def test_notes_and_comments_are_disclosed_after_the_body_without_dates():
    got = _pages("16_word_constructs.docx")
    text = got.pages[0].text
    i_alpaca = text.find("ALPACA")
    positions = {
        "SALAMANDER": text.find("SALAMANDER"),
        "URCHIN": text.find("URCHIN"),
        "VULTURE": text.find("VULTURE"),
        "WALRUS Reviewer": text.find("WALRUS Reviewer"),
    }
    assert i_alpaca != -1 and all(p != -1 and p > i_alpaca for p in positions.values()), (
        f"ALPACA (last body paragraph) at {i_alpaca}; the footnote, endnote "
        f"and comment sentinels must all follow it: {positions}")
    assert "2019" not in text, (
        "the comment's own w:date (2019-01-02) must never reach the page "
        f"text; found '2019' in: {text!r}")


# ---------------------------------------------------------------------------
# 10. Headers lead
# ---------------------------------------------------------------------------


def test_headers_lead_the_page_text():
    text = _page_text("16_word_constructs.docx")
    first_lines = text.split("\n")[:3]
    head = "\n".join(first_lines)
    i_yak, i_xerus = head.find("YAK"), head.find("XERUS")
    assert i_yak != -1 and i_xerus != -1, (
        f"YAK (first-page header) and XERUS (default header) must both "
        f"be within the page text's first 3 lines; first 3 lines: "
        f"{first_lines!r}")


# ---------------------------------------------------------------------------
# 11. Footer trails, and is a Bates zone candidate
# ---------------------------------------------------------------------------


def test_footer_trails_the_page_text_and_is_a_bates_zone_candidate():
    got = _pages("16_word_constructs.docx")
    text = got.pages[0].text
    last_lines = text.split("\n")[-8:]
    tail = "\n".join(last_lines)
    i_zebu, i_stamp = tail.find("ZEBU"), tail.find("MNFV 000777")

    doc = document("16_word_constructs.docx", (got.pages[0],))
    candidates = detect_candidates((doc,))
    found_stamp = any(c.raw == "MNFV 000777" for c in candidates)

    assert i_zebu != -1 and i_stamp != -1 and found_stamp, (
        f"ZEBU (default footer, first paragraph) at tail-relative {i_zebu}; "
        f"MNFV 000777 (default footer, second paragraph) at tail-relative "
        f"{i_stamp}, within last 8 lines {last_lines!r}; Bates candidates "
        f"the existing detect_candidates() API found on this page: "
        f"{candidates!r}")


# ---------------------------------------------------------------------------
# 12. Unread parts disclosed
# ---------------------------------------------------------------------------


def test_altchunk_and_chart_are_disclosed_as_unread():
    got = _pages("16_word_constructs.docx")
    marked = [n for n in got.notes if ex.has_evidence_marker(n)]
    has_altchunk = any(re.search(r"\baltchunk\b", n, re.IGNORECASE) for n in marked)
    has_chart = any(re.search(r"\bchart\b", n, re.IGNORECASE) for n in marked)
    assert has_altchunk and has_chart, (
        f"expected marked (has_evidence_marker) notes naming an altChunk and "
        f"a chart, as whole words; marked notes: {marked!r}; all notes: "
        f"{got.notes!r}")


# ---------------------------------------------------------------------------
# 12b. A large body picture is disclosed as unread; a small one is not
# ---------------------------------------------------------------------------


def test_a_large_picture_is_disclosed_and_a_small_one_is_not():
    got = _pages("16_word_constructs.docx")
    picture_notes = [n for n in got.notes
                      if ex.has_evidence_marker(n) and "picture" in n.lower()]
    assert len(picture_notes) == 1, (
        f"expected exactly one marked note naming pictures -- the 6in x 8in "
        f"body picture covers ~51% of the page and must be disclosed; the "
        f"1in x 1in body picture covers ~1% and must not be: "
        f"{picture_notes!r}; all notes: {got.notes!r}")
    count = re.search(r"(\d+)\s+picture", picture_notes[0], re.IGNORECASE)
    assert count is not None and count.group(1) == "1", (
        f"expected the picture note's own count to be 1 (only the 6in x 8in "
        f"picture qualifies): {picture_notes[0]!r}")


# ---------------------------------------------------------------------------
# 13. The page note is true
# ---------------------------------------------------------------------------


def test_the_page_note_does_not_deny_the_rendered_page_break_it_carries():
    got = _pages("16_word_constructs.docx")
    denying = [n for n in got.notes if "no page boundaries" in n.lower()]
    assert not denying, (
        f"this fixture carries a rendered page break (GAZELLE's run holds "
        f"w:lastRenderedPageBreak); no note may claim the file carries none: "
        f"{denying!r}; all notes: {got.notes!r}")


# ---------------------------------------------------------------------------
# 14. DERIVED class test: every w:t reaches the page text, or is excluded by
#     name (mc:Fallback).
# ---------------------------------------------------------------------------

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_WT_TAG = "{%s}t" % _W_NS
_WT_FIXED_PARTS = ("word/document.xml", "word/footnotes.xml",
                    "word/endnotes.xml", "word/comments.xml")


def _wt_fragments(raw: bytes) -> list:
    """Every non-blank ``w:t`` text fragment reachable from this DOCX's
    text-bearing parts (document, headers, footers, footnotes, endnotes,
    comments), skipping anything nested under ``mc:Fallback``.

    An independent, from-the-XML reading of "what this file's text-bearing
    parts actually hold", deliberately not sharing a code path with
    ``_extract_docx`` -- so it can catch what that extractor drops rather
    than agreeing with it by construction.
    """
    fragments: list = []
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names = z.namelist()
        wanted = [
            n for n in names
            if n in _WT_FIXED_PARTS
            or (n.startswith("word/header") and n.endswith(".xml"))
            or (n.startswith("word/footer") and n.endswith(".xml"))
        ]
        for name in wanted:
            root = etree.fromstring(z.read(name))
            for t in root.iter(_WT_TAG):
                under_fallback = False
                ancestor = t.getparent()
                while ancestor is not None:
                    if etree.QName(ancestor).localname == "Fallback":
                        under_fallback = True
                        break
                    ancestor = ancestor.getparent()
                if under_fallback:
                    continue
                if t.text and t.text.strip():
                    fragments.append(t.text)
    return fragments


@pytest.mark.parametrize("name", ["16_word_constructs.docx", "05_letter.docx"])
def test_every_w_t_fragment_reaches_the_page_text_or_is_excluded_by_name(name):
    raw = (FIXTURES / name).read_bytes()
    fragments = _wt_fragments(raw)
    page_text = normalize(_page_text(name))
    missing = [f for f in fragments if normalize(f) not in page_text]
    assert not missing, (
        f"{name}: {len(missing)} of {len(fragments)} w:t fragment(s) from "
        f"document.xml/header*.xml/footer*.xml/footnotes.xml/endnotes.xml/"
        f"comments.xml do not appear in the normalized page text: {missing!r}")


# ---------------------------------------------------------------------------
# 15/16. The two unmarked OCR notes in _extract_pdf (D-49 gap)
# ---------------------------------------------------------------------------


def test_ocr_disabled_note_carries_an_evidence_marker():
    got = _pages("02_scanned_instruction.pdf", ex.ExtractOptions(ocr_enabled=False))
    disabled_note = next((n for n in got.notes if "OCR disabled" in n), None)
    assert disabled_note is not None and ex.has_evidence_marker(disabled_note), (
        f"the 'OCR disabled' note must carry an evidence marker: "
        f"{disabled_note!r}; all notes: {got.notes!r}")


def test_ocr_unavailable_note_carries_an_evidence_marker(monkeypatch):
    monkeypatch.setattr(ex, "ocr_available", lambda: False)
    monkeypatch.setattr(
        ex, "ocr_models_present", lambda: (False, "stub: models not present"))
    got = _pages("02_scanned_instruction.pdf")
    unavailable_note = next((n for n in got.notes if "OCR is unavailable" in n), None)
    assert unavailable_note is not None and ex.has_evidence_marker(unavailable_note), (
        f"the 'OCR is unavailable' note must carry an evidence marker: "
        f"{unavailable_note!r}; all notes: {got.notes!r}")


# ---------------------------------------------------------------------------
# Stage 2b: three gaps carried in stage 2a's disclosure (Word spec,
# "4. Carried from stage 2a"). Every input here is built in ``tmp_path``, not
# by changing fixture 16, so no existing position test above moves.
# ---------------------------------------------------------------------------


def _extract_bytes(name: str, raw: bytes):
    return ex.extract(name, raw)


def test_chartex_chart_is_disclosed_as_unread(tmp_path):
    """Carried gap 1: chart detection matched only a graphicData URI ending
    '/chart'. An Office 2016+ chartEx chart (waterfall, funnel, ...) uses the
    namespace ``.../office/drawing/2014/chartex`` verbatim instead and must
    be detected too."""
    import docx as _docx
    from docx.oxml import parse_xml
    from docx.oxml.ns import qn

    d = _docx.Document()
    d.add_paragraph("BEFORE the chartEx chart.")
    sect_pr = d.element.body.find(qn("w:sectPr"))
    ns = (
        'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:cx="http://schemas.microsoft.com/office/drawing/2014/chartex"'
    )
    sect_pr.addprevious(parse_xml(
        '<w:p ' + ns + '><w:r><w:drawing>'
        '<wp:inline distT="0" distB="0" distL="0" distR="0">'
        '<wp:extent cx="1828800" cy="1828800"/>'
        '<wp:docPr id="1" name="ChartEx1"/>'
        '<a:graphic><a:graphicData '
        'uri="http://schemas.microsoft.com/office/drawing/2014/chartex">'
        '<cx:chart/>'
        '</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>'))
    d.add_paragraph("AFTER the chartEx chart.")

    path = tmp_path / "chartex.docx"
    d.save(str(path))
    got = _extract_bytes(path.name, path.read_bytes())

    marked = [n for n in got.notes if ex.has_evidence_marker(n)]
    has_chart = any(re.search(r"\bchart\b", n, re.IGNORECASE) for n in marked)
    assert has_chart, (
        f"expected a marked note naming a chart for an Office 2016+ chartEx "
        f"graphic (graphicData uri is the chartex namespace, not a URI "
        f"ending '/chart'): marked={marked!r}; all notes={got.notes!r}")


def test_unread_constructs_are_disclosed_outside_the_body_too(tmp_path):
    """Carried gap 2: altChunk, chart, SmartArt and large-picture disclosure
    used to gate on ``in_body`` -- so one in a header, a footer, a footnote,
    an endnote, a comment or a text box was lost without a word. This proves
    three of those locations at once: a chart in a HEADER, an altChunk in a
    FOOTNOTE, and a chart PLUS a large picture inside a TEXT BOX (itself in
    the body).

    Critic finding: a presence-only check ("some marked chart/altChunk/
    picture note exists") cannot see a construct counted MORE than once.
    ``_note_drawing`` is called on the outer text-box ``<w:drawing>`` (whose
    own ``.iter()`` already walks down into anything nested inside it,
    including a chart or picture placed in the box) and is then called
    AGAIN when the walk separately reaches that nested ``<w:drawing>`` on
    its own -- so a single chart or picture inside a text box was measured
    to come out as 2 in the draft (critic probe p4_gap_probes.py, cases (a)
    and (b)). Asserting the exact counts a CORRECT read produces (2 chart(s)
    -- header + text box, each real construct appearing once; 1 altChunk(s);
    1 picture(s)) pins the spec regardless of whether this particular
    double-count is what is fixed to satisfy it.
    """
    import docx as _docx
    from docx.oxml import parse_xml
    from docx.oxml.ns import qn

    ns_all = (
        'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
        'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"'
    )
    w_only = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    r_only = ('xmlns:r="http://schemas.openxmlformats.org/officeDocument/'
             '2006/relationships"')

    d = _docx.Document()
    d.add_paragraph("BODY paragraph, nothing special.")
    sect_pr = d.element.body.find(qn("w:sectPr"))

    # A large picture (6in x 8in on an 8.5in x 11in page, ~51%) inside a
    # text box -- same size word_constructs_docx uses for its own body
    # picture disclosure test, so the >=25% threshold is comfortably cleared.
    picture_drawing = (
        '<w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
        '<wp:extent cx="5486400" cy="7315200"/>'
        '<wp:docPr id="9" name="BoxPic"/>'
        '<a:graphic><a:graphicData '
        'uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<pic:pic><pic:blipFill><a:blip r:embed="rIdBoxImg"/></pic:blipFill></pic:pic>'
        '</a:graphicData></a:graphic></wp:inline></w:drawing>'
    )
    # A chart alongside the picture, both inside the same text box -- the
    # second location this test's critic finding needs (case (a) above).
    box_chart_drawing = (
        '<w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
        '<wp:extent cx="1828800" cy="1828800"/>'
        '<wp:docPr id="10" name="BoxChart"/>'
        '<a:graphic><a:graphicData '
        'uri="http://schemas.openxmlformats.org/drawingml/2006/chart">'
        '<c:chart xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
        'r:id="rIdBoxChart"/>'
        '</a:graphicData></a:graphic></wp:inline></w:drawing>'
    )
    sect_pr.addprevious(parse_xml(
        '<w:p ' + ns_all + '>'
        '<w:r><w:t>Text box anchor.</w:t></w:r>'
        '<w:r><mc:AlternateContent><mc:Choice Requires="wps">'
        '<w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
        '<wp:extent cx="914400" cy="914400"/>'
        '<wp:docPr id="2" name="TextBox1"/>'
        '<a:graphic><a:graphicData '
        'uri="http://schemas.microsoft.com/office/word/2010/wordprocessingShape">'
        '<wps:wsp><wps:cNvSpPr txBox="1"/>'
        '<wps:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="914400" cy="914400"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></wps:spPr>'
        '<wps:txbx><w:txbxContent>'
        '<w:p><w:r><w:t>box text</w:t></w:r></w:p>'
        '<w:p><w:r>' + picture_drawing + '</w:r></w:p>'
        '<w:p><w:r>' + box_chart_drawing + '</w:r></w:p>'
        '</w:txbxContent></wps:txbx>'
        '<wps:bodyPr/></wps:wsp>'
        '</a:graphicData></a:graphic></wp:inline></w:drawing>'
        '</mc:Choice><mc:Fallback><w:pict/></mc:Fallback></mc:AlternateContent></w:r>'
        '</w:p>'))
    tapir_p = d.add_paragraph("Paragraph carrying a footnote reference.")
    tapir_p._p.append(parse_xml(
        '<w:r ' + w_only + '><w:footnoteReference w:id="1"/></w:r>'))
    # wire the header part into the body's own (final) sectPr
    sect_pr.insert(0, parse_xml(
        '<w:headerReference w:type="default" r:id="rIdHeader1" '
        + w_only + " " + r_only + "/>"))

    buf = io.BytesIO()
    d.save(buf)
    base = buf.getvalue()

    zin = zipfile.ZipFile(io.BytesIO(base))
    names = zin.namelist()
    ct = zin.read("[Content_Types].xml").decode("utf-8")
    rels = zin.read("word/_rels/document.xml.rels").decode("utf-8")
    pfx = "application/vnd.openxmlformats-officedocument.wordprocessingml."
    add_ct = (
        '<Override PartName="/word/footnotes.xml" ContentType="' + pfx + 'footnotes+xml"/>'
        '<Override PartName="/word/header1.xml" ContentType="' + pfx + 'header+xml"/>'
        '<Override PartName="/word/charts/chart1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.drawingml.chart+xml"/>'
        '<Override PartName="/word/charts/chart2.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.drawingml.chart+xml"/>'
        '<Default Extension="html" ContentType="text/html"/>'
    )
    assert "</Types>" in ct
    ct = ct.replace("</Types>", add_ct + "</Types>")
    rpfx = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
    add_rel = (
        '<Relationship Id="rIdFootnotes" Type="' + rpfx + 'footnotes" Target="footnotes.xml"/>'
        '<Relationship Id="rIdHeader1" Type="' + rpfx + 'header" Target="header1.xml"/>'
        # The text-box chart lives in the BODY (word/document.xml), so its
        # own r:id is resolved through document.xml's OWN relationships,
        # unlike the header chart below (resolved through header1.xml.rels).
        '<Relationship Id="rIdBoxChart" Type="' + rpfx + 'chart" Target="charts/chart2.xml"/>'
    )
    assert "</Relationships>" in rels
    rels = rels.replace("</Relationships>", add_rel + "</Relationships>")

    footnotes_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:footnotes ' + w_only + '>'
        '<w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>'
        '<w:footnote w:type="continuationSeparator" w:id="0">'
        '<w:p><w:r><w:continuationSeparator/></w:r></w:p></w:footnote>'
        '<w:footnote w:id="1">'
        '<w:p><w:r><w:footnoteRef/></w:r>'
        '<w:r><w:t xml:space="preserve"> footnote text.</w:t></w:r></w:p>'
        '<w:altChunk r:id="rIdAltChunkFN" ' + r_only + '/>'
        '</w:footnote></w:footnotes>'
    )
    footnotes_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rIdAltChunkFN" Type="' + rpfx
        + 'aFChunk" Target="altchunk_fn.html"/></Relationships>'
    )
    header_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:hdr ' + ns_all + '>'
        '<w:p><w:r><w:t>Header text.</w:t></w:r>'
        '<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
        '<wp:extent cx="1828800" cy="1828800"/>'
        '<wp:docPr id="3" name="HeaderChart1"/>'
        '<a:graphic><a:graphicData '
        'uri="http://schemas.openxmlformats.org/drawingml/2006/chart">'
        '<c:chart xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
        'r:id="rIdHeaderChart"/>'
        '</a:graphicData></a:graphic></wp:inline></w:drawing></w:r>'
        '</w:p></w:hdr>'
    )
    header_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rIdHeaderChart" Type="' + rpfx
        + 'chart" Target="charts/chart1.xml"/></Relationships>'
    )
    chart_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart">'
        '<c:chart/></c:chartSpace>'
    )

    path = tmp_path / "unread_outside_body.docx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zout:
        for n in names:
            if n == "[Content_Types].xml":
                zout.writestr(n, ct)
            elif n == "word/_rels/document.xml.rels":
                zout.writestr(n, rels)
            else:
                zout.writestr(n, zin.read(n))
        zout.writestr("word/footnotes.xml", footnotes_xml)
        zout.writestr("word/_rels/footnotes.xml.rels", footnotes_rels_xml)
        zout.writestr("word/altchunk_fn.html",
                      b"<html><body>ALTCHUNK IN FOOTNOTE</body></html>")
        zout.writestr("word/header1.xml", header_xml)
        zout.writestr("word/_rels/header1.xml.rels", header_rels_xml)
        zout.writestr("word/charts/chart1.xml", chart_xml)
        zout.writestr("word/charts/chart2.xml", chart_xml)
    zin.close()

    got = _extract_bytes(path.name, path.read_bytes())
    marked = [n for n in got.notes if ex.has_evidence_marker(n)]
    altchunk_notes = [n for n in marked if re.search(r"\baltchunk\b", n, re.IGNORECASE)]
    chart_notes = [n for n in marked if re.search(r"\bchart\b", n, re.IGNORECASE)]
    picture_notes = [n for n in marked if "picture" in n.lower()]
    assert altchunk_notes and chart_notes and picture_notes, (
        f"expected marked notes for an altChunk (in a footnote), a chart "
        f"(in a header AND a text box) and a large picture (in a text box) "
        f"-- none of these live in the body: marked={marked!r}; all "
        f"notes={got.notes!r}")

    def _count(notes: list[str], word: str) -> int:
        m = re.search(r"(\d+)\s+" + word, notes[0], re.IGNORECASE) if notes else None
        assert m is not None, f"no count found in {notes!r}"
        return int(m.group(1))

    assert _count(chart_notes, "chart") == 2, (
        f"expected exactly 2 chart(s) -- one in the header, one in the text "
        f"box, each counted once: {chart_notes!r}")
    assert _count(altchunk_notes, "altChunk") == 1, (
        f"expected exactly 1 altChunk(s) (the footnote's): {altchunk_notes!r}")
    assert _count(picture_notes, "picture") == 1, (
        f"expected exactly 1 picture(s) (the text box's large picture): "
        f"{picture_notes!r}")


def test_unread_constructs_in_footer_endnote_and_comment(tmp_path):
    """Carried gap 2, the three Word-spec-named locations the test above does
    not reach: a SmartArt drawing in a FOOTER, a chart in an ENDNOTE, and an
    altChunk in a COMMENT."""
    import docx as _docx
    from docx.oxml import parse_xml
    from docx.oxml.ns import qn

    ns_all = (
        'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:dgm="http://schemas.openxmlformats.org/drawingml/2006/diagram" '
        'xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"'
    )
    w_only = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    r_only = ('xmlns:r="http://schemas.openxmlformats.org/officeDocument/'
             '2006/relationships"')

    d = _docx.Document()
    d.add_paragraph("BODY paragraph, nothing special.")
    sect_pr = d.element.body.find(qn("w:sectPr"))
    sect_pr.insert(0, parse_xml(
        '<w:footerReference w:type="default" r:id="rIdFooter1" '
        + w_only + " " + r_only + "/>"))

    buf = io.BytesIO()
    d.save(buf)
    base = buf.getvalue()

    zin = zipfile.ZipFile(io.BytesIO(base))
    names = zin.namelist()
    ct = zin.read("[Content_Types].xml").decode("utf-8")
    rels = zin.read("word/_rels/document.xml.rels").decode("utf-8")
    pfx = "application/vnd.openxmlformats-officedocument.wordprocessingml."
    add_ct = (
        '<Override PartName="/word/footer1.xml" ContentType="' + pfx + 'footer+xml"/>'
        '<Override PartName="/word/endnotes.xml" ContentType="' + pfx + 'endnotes+xml"/>'
        '<Override PartName="/word/comments.xml" ContentType="' + pfx + 'comments+xml"/>'
    )
    assert "</Types>" in ct
    ct = ct.replace("</Types>", add_ct + "</Types>")
    rpfx = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
    add_rel = '<Relationship Id="rIdFooter1" Type="' + rpfx + 'footer" Target="footer1.xml"/>'
    assert "</Relationships>" in rels
    rels = rels.replace("</Relationships>", add_rel + "</Relationships>")

    smartart_drawing = (
        '<w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
        '<wp:extent cx="1828800" cy="1828800"/>'
        '<wp:docPr id="30" name="FooterSmartArt"/>'
        '<a:graphic><a:graphicData '
        'uri="http://schemas.openxmlformats.org/drawingml/2006/diagram">'
        '<dgm:relIds r:dm="rIdDm" r:lo="rIdLo" r:qs="rIdQs" r:cs="rIdCs"/>'
        '</a:graphicData></a:graphic></wp:inline></w:drawing>'
    )
    chart_drawing = (
        '<w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
        '<wp:extent cx="1828800" cy="1828800"/>'
        '<wp:docPr id="31" name="EndnoteChart"/>'
        '<a:graphic><a:graphicData '
        'uri="http://schemas.openxmlformats.org/drawingml/2006/chart">'
        '<c:chart r:id="rIdEndChart"/>'
        '</a:graphicData></a:graphic></wp:inline></w:drawing>'
    )
    footer_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:ftr ' + ns_all + '><w:p><w:r>' + smartart_drawing
        + '</w:r></w:p></w:ftr>'
    )
    endnotes_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:endnotes ' + ns_all + '>'
        '<w:endnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:endnote>'
        '<w:endnote w:type="continuationSeparator" w:id="0">'
        '<w:p><w:r><w:continuationSeparator/></w:r></w:p></w:endnote>'
        '<w:endnote w:id="1"><w:p><w:r>' + chart_drawing
        + '</w:r></w:p></w:endnote></w:endnotes>'
    )
    # w:altChunk is a BLOCK-level construct -- a direct child of its
    # container (comment/footnote/endnote/body), a SIBLING of w:p, never
    # nested inside a w:r -- mirroring the existing footnote altChunk case
    # above (`_block_lines` only recognizes it as a direct child).
    comments_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:comments ' + ns_all + '>'
        '<w:comment w:id="1" w:author="Reviewer">'
        '<w:p><w:r><w:t>reviewer comment text.</w:t></w:r></w:p>'
        '<w:altChunk r:id="rIdCommentChunk"/>'
        '</w:comment>'
        '</w:comments>'
    )

    path = tmp_path / "unread_footer_endnote_comment.docx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zout:
        for n in names:
            if n == "[Content_Types].xml":
                zout.writestr(n, ct)
            elif n == "word/_rels/document.xml.rels":
                zout.writestr(n, rels)
            else:
                zout.writestr(n, zin.read(n))
        zout.writestr("word/footer1.xml", footer_xml)
        zout.writestr("word/endnotes.xml", endnotes_xml)
        zout.writestr("word/comments.xml", comments_xml)
    zin.close()

    got = _extract_bytes(path.name, path.read_bytes())
    marked = [n for n in got.notes if ex.has_evidence_marker(n)]
    has_smartart = any(re.search(r"\bsmartart\b", n, re.IGNORECASE) for n in marked)
    has_chart = any(re.search(r"\bchart\b", n, re.IGNORECASE) for n in marked)
    has_altchunk = any(re.search(r"\baltchunk\b", n, re.IGNORECASE) for n in marked)
    assert has_smartart and has_chart and has_altchunk, (
        f"expected marked notes for a SmartArt drawing (in a footer), a "
        f"chart (in an endnote) and an altChunk (in a comment): "
        f"marked={marked!r}; all notes={got.notes!r}")


def test_chartex_in_real_alternatecontent_shape_with_picture_fallback_is_one_chart(tmp_path):
    """A chartEx graphic is never a bare ``graphicData`` in real Word output
    -- it is the ``mc:Choice`` of an ``mc:AlternateContent`` whose
    ``mc:Fallback`` is a plain picture (for a reader that does not
    understand chartEx). The draft's chartEx detection (carried gap 1)
    happens to handle this real shape (critic probe p4_gap_probes.py, case
    (c)), but nothing pins it: this guards against a fix that only matches
    a bare ``cx:chart`` graphicData and never looks inside
    ``mc:AlternateContent`` at all, which would silently double-count via
    the Fallback picture or miss the chart entirely."""
    import docx as _docx
    from docx.oxml import parse_xml
    from docx.oxml.ns import qn

    ns_all = (
        'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture" '
        'xmlns:cx1="http://schemas.microsoft.com/office/drawing/2015/9/8/chartex"'
    )
    fallback_picture = (
        '<w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
        '<wp:extent cx="5486400" cy="7315200"/>'
        '<wp:docPr id="40" name="ChartExFallbackPic"/>'
        '<a:graphic><a:graphicData '
        'uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<pic:pic><pic:blipFill><a:blip r:embed="rIdFallbackImg"/></pic:blipFill></pic:pic>'
        '</a:graphicData></a:graphic></wp:inline></w:drawing>'
    )
    d = _docx.Document()
    sect_pr = d.element.body.find(qn("w:sectPr"))
    sect_pr.addprevious(parse_xml(
        '<w:p ' + ns_all + '><w:r><mc:AlternateContent>'
        '<mc:Choice Requires="cx1">'
        '<w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
        '<wp:extent cx="1828800" cy="1828800"/>'
        '<wp:docPr id="41" name="ChartEx1"/>'
        '<a:graphic><a:graphicData '
        'uri="http://schemas.microsoft.com/office/drawing/2014/chartex">'
        '<cx:chart xmlns:cx="http://schemas.microsoft.com/office/drawing/2014/chartex" '
        'r:id="rIdChartEx"/>'
        '</a:graphicData></a:graphic></wp:inline></w:drawing>'
        '</mc:Choice>'
        '<mc:Fallback>' + fallback_picture + '</mc:Fallback>'
        '</mc:AlternateContent></w:r></w:p>'))

    path = tmp_path / "chartex_real_shape.docx"
    d.save(str(path))
    got = _extract_bytes(path.name, path.read_bytes())

    marked = [n for n in got.notes if ex.has_evidence_marker(n)]
    chart_notes = [n for n in marked if re.search(r"\bchart\b", n, re.IGNORECASE)]
    picture_notes = [n for n in marked if "picture" in n.lower()]
    assert len(chart_notes) == 1, (
        f"expected exactly 1 marked chart note (the chartEx, once): "
        f"{chart_notes!r}; all notes={got.notes!r}")
    count = re.search(r"(\d+)\s+chart", chart_notes[0], re.IGNORECASE)
    assert count is not None and count.group(1) == "1", (
        f"expected the chart note's own count to be 1: {chart_notes[0]!r}")
    assert not picture_notes, (
        f"the Fallback picture must not ALSO be disclosed as an unread "
        f"picture -- a reader that understands chartEx takes mc:Choice, "
        f"never mc:Fallback: {picture_notes!r}")


@pytest.mark.parametrize("mutate_sect,case_id", [
    (lambda sect_pr, qn_: sect_pr.remove(sect_pr.find(qn_("w:pgSz"))), "removed"),
    (lambda sect_pr, qn_: sect_pr.find(qn_("w:pgSz")).set(qn_("w:w"), "0"), "w_zero"),
    (lambda sect_pr, qn_: sect_pr.find(qn_("w:pgSz")).attrib.pop(qn_("w:h")), "h_missing"),
], ids=["pgsz_removed", "pgsz_w_zero", "pgsz_h_missing"])
def test_unusable_pgsz_variants_all_disclose_unmeasurable_pictures(
        tmp_path, mutate_sect, case_id):
    """The existing carried-gap-3 test covers only a REMOVED w:pgSz. An
    unusable one (w:w="0", or a missing w:h) must be treated the same way,
    not silently pass the "has a pgSz element" check and then divide by
    zero or KeyError -- or, worse, silently skip disclosure."""
    import docx as _docx
    from docx.oxml.ns import qn
    from docx.shared import Inches
    from lxml import etree

    d = _docx.Document()
    d.add_paragraph("BEFORE the unmeasurable picture.")
    pixel_buf = io.BytesIO()
    from PIL import Image
    Image.new("RGB", (1, 1), (0, 0, 0)).save(pixel_buf, format="PNG")
    d.add_picture(io.BytesIO(pixel_buf.getvalue()), width=Inches(6), height=Inches(8))
    d.add_paragraph("AFTER the unmeasurable picture.")

    buf = io.BytesIO()
    d.save(buf)
    base = buf.getvalue()

    zin = zipfile.ZipFile(io.BytesIO(base))
    names = zin.namelist()
    root = etree.fromstring(zin.read("word/document.xml"))
    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    body = next(c for c in root if c.tag == w + "body")
    sect_pr = next(c for c in reversed(list(body)) if c.tag == w + "sectPr")
    mutate_sect(sect_pr, qn)
    new_doc_xml = etree.tostring(root, xml_declaration=True, encoding="UTF-8",
                                 standalone=True)

    path = tmp_path / f"unusable_pgsz_{case_id}.docx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zout:
        for n in names:
            zout.writestr(n, new_doc_xml if n == "word/document.xml" else zin.read(n))
    zin.close()

    got = _extract_bytes(path.name, path.read_bytes())
    marked = [n for n in got.notes if ex.has_evidence_marker(n)]
    unmeasurable = [n for n in marked if "could not be measured" in n.lower()]
    assert unmeasurable, (
        f"expected a marked note saying pictures could not be measured "
        f"against the page ({case_id}): marked={marked!r}; all "
        f"notes={got.notes!r}")


def test_no_usable_pgsz_and_no_picture_discloses_nothing_unmeasurable(tmp_path):
    """The negative case the parametrized test above cannot cover: no usable
    ``w:pgSz`` AND no picture at all must not, by itself, produce an
    "unmeasurable" note -- there is nothing to measure. Guards against a fix
    that notes the page's own unmeasurability regardless of whether any
    picture exists."""
    import docx as _docx
    from docx.oxml.ns import qn
    from lxml import etree

    d = _docx.Document()
    d.add_paragraph("No pictures anywhere in this document.")

    buf = io.BytesIO()
    d.save(buf)
    base = buf.getvalue()

    zin = zipfile.ZipFile(io.BytesIO(base))
    names = zin.namelist()
    root = etree.fromstring(zin.read("word/document.xml"))
    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    body = next(c for c in root if c.tag == w + "body")
    sect_pr = next(c for c in reversed(list(body)) if c.tag == w + "sectPr")
    sect_pr.remove(sect_pr.find(qn("w:pgSz")))
    new_doc_xml = etree.tostring(root, xml_declaration=True, encoding="UTF-8",
                                 standalone=True)

    path = tmp_path / "no_pgsz_no_picture.docx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zout:
        for n in names:
            zout.writestr(n, new_doc_xml if n == "word/document.xml" else zin.read(n))
    zin.close()

    got = _extract_bytes(path.name, path.read_bytes())
    marked = [n for n in got.notes if ex.has_evidence_marker(n)]
    unmeasurable = [n for n in marked if "could not be measured" in n.lower()]
    assert not unmeasurable, (
        f"a document with no picture at all owes no 'could not be "
        f"measured' note, usable page size or not: {unmeasurable!r}; all "
        f"notes={got.notes!r}")


def test_a_final_section_with_no_usable_pgsz_discloses_unmeasurable_pictures(tmp_path):
    """Carried gap 3: when the final section has no usable ``w:pgSz``, large-
    picture disclosure used to be skipped SILENTLY. A marked note must say
    pictures could not be measured against the page, mirroring the PDF
    path's own wording when image geometry cannot be measured."""
    import docx as _docx
    from docx.shared import Inches
    from lxml import etree
    from PIL import Image

    d = _docx.Document()
    d.add_paragraph("BEFORE the unmeasurable picture.")
    pixel_buf = io.BytesIO()
    Image.new("RGB", (1, 1), (0, 0, 0)).save(pixel_buf, format="PNG")
    d.add_picture(io.BytesIO(pixel_buf.getvalue()), width=Inches(6), height=Inches(8))
    d.add_paragraph("AFTER the unmeasurable picture.")

    buf = io.BytesIO()
    d.save(buf)
    base = buf.getvalue()

    zin = zipfile.ZipFile(io.BytesIO(base))
    names = zin.namelist()
    root = etree.fromstring(zin.read("word/document.xml"))
    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    body = next(c for c in root if c.tag == w + "body")
    sect_pr = next(c for c in reversed(list(body)) if c.tag == w + "sectPr")
    pg_sz = next((c for c in sect_pr if c.tag == w + "pgSz"), None)
    assert pg_sz is not None, "python-docx's own sectPr must carry a w:pgSz to remove"
    sect_pr.remove(pg_sz)
    new_doc_xml = etree.tostring(root, xml_declaration=True, encoding="UTF-8",
                                 standalone=True)

    path = tmp_path / "no_pgsz.docx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zout:
        for n in names:
            zout.writestr(n, new_doc_xml if n == "word/document.xml" else zin.read(n))
    zin.close()

    got = _extract_bytes(path.name, path.read_bytes())
    marked = [n for n in got.notes if ex.has_evidence_marker(n)]
    unmeasurable = [n for n in marked if "could not be measured" in n.lower()]
    assert unmeasurable, (
        f"expected a marked note saying pictures could not be measured "
        f"against the page (no usable w:pgSz on the final section): "
        f"marked={marked!r}; all notes={got.notes!r}")
