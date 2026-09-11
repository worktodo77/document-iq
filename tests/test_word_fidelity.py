"""D-50 Word fidelity package, stage 1: fixture and RED tests only.

Every test in this file is written against the CONTRACT the D-50 build spec
makes true (every character in a Word document's text-bearing parts reaches
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
