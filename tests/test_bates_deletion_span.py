"""D-59: the Bates zone skips the Word deletion list by position, never by what a
line says.

D-56 lists each passage deleted under Word's tracked changes after the page's
body, ``[deleted by <author>] <text>``. Word fix round 2 kept that list out of the
Bates zone by skipping every line starting ``[deleted by `` on every page of every
format; Word review round 3 confirmed that a PDF, email, OCR'd or text page that
types such a line lost it from its zone. D-59 rules a contract field instead:
``PageRecord.deletion_line_span``, set by the Word reader only, and the zone skips
exactly the lines it names.

Every test here was watched failing on ``347df32`` (the text match), by full node
id, or, for those Word review round 4 asked for (from the ``slice_lines`` section
on), failing on ``85efb95`` or under the named round-4 mutant applied to it. All
text is invented (D-12).
"""

from __future__ import annotations

import ast
import html
import json
import pathlib

import pytest

from dociq.contracts import ContractViolation, PageKind, PageRecord
from dociq.identify.bates import (BatesDecision, BatesFormat, BatesZone, DecisionStatus,
                                  apply_bates, apply_bates_reported, detect_candidates,
                                  zone_has_candidate)
from dociq.ingest import extract as ex
from dociq.ingest import walker

from .fixtures import document, page
from .test_word_fidelity import _del, _dt, _p, _part, _raw_docx, _t

ROOT = pathlib.Path(__file__).resolve().parents[1]

TYPED = "[deleted by agreement of the parties] the next paragraph"
"""A line a document types itself. It is that document's text, not a list DocIQ
wrote, and it must stay in the zone."""

# 22 lines: the head is the first three; the eight-line tail is the typed line
# and the seven closing lines. The stamp-shaped line above the tail is NOT in the
# zone; skipping the typed line would pull it in, and the page would propose a
# locator it does not carry.
SHIFT = (["Invented memorandum, page one", "To: file", "From: author"]
         + [f"narrative line {i}" for i in range(10)]
         + ["iiCON900100", TYPED]
         + [f"closing line {i}" for i in range(7)])

# The page's own stamp stands on a line that starts with the label.
ON_LINE = (["Invented memorandum, page one", "To: file", "From: author", "narrative"]
           + ["[deleted by nobody; the memo's own footer] iiCON900123"])

FMT = BatesFormat(prefix="iiCON", separator="", digit_widths=(6,), suffix=None,
                  suffix_sep="")
CONFIRMED = BatesDecision(DecisionStatus.CONFIRMED, FMT)
OPT = ex.ExtractOptions(ocr_enabled=False)


def _pdf(lines: list[str]) -> bytes:
    import fitz

    doc = fitz.open()
    pg = doc.new_page(width=612, height=792)
    for i, line in enumerate(lines):
        pg.insert_text((72, 72 + 14 * i), line, fontsize=10)
    raw = doc.tobytes()
    doc.close()
    return raw


def _eml(lines: list[str]) -> bytes:
    return ("From: a@example.com\r\nTo: b@example.com\r\nSubject: Invented memo\r\n"
            "Date: Mon, 1 Jan 2018 10:00:00 +0000\r\n\r\n"
            + "\r\n".join(lines) + "\r\n").encode("utf-8")


def _txt(lines: list[str]) -> bytes:
    return ("\n".join(lines) + "\n").encode("utf-8")


BUILDERS = {"memo.pdf": _pdf, "memo.eml": _eml, "memo.txt": _txt}


def _one_page(name: str, lines: list[str]) -> PageRecord:
    got = ex.extract(name, BUILDERS[name](lines), OPT)
    assert len(got.pages) == 1, got.pages
    p = got.pages[0]
    assert TYPED in p.text or lines is not SHIFT, p.text
    return p


def _ocr(n: int, text: str) -> PageRecord:
    return page(n, text).evolve(kind=PageKind.OCR, ocr_conf=0.9)


# ---------------------------------------------------------------------------
# Every other format keeps a typed "[deleted by " line in its zone
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(BUILDERS))
def test_a_typed_deletion_label_line_stays_in_the_zone_and_the_tail_does_not_move(name):
    """detect_candidates: the zone's tail is the page's own last eight lines, the
    typed line among them, so the stamp-shaped line above the tail stays outside
    it and proposes nothing."""
    p = _one_page(name, SHIFT)
    got = detect_candidates((document(name, (p,)),))
    assert got == (), (
        f"a line above the tail zone became a candidate ({[c.raw for c in got]}): "
        "the typed line was skipped")
    assert p.deletion_line_span is None, "only the Word reader writes a deletion list"
    lines = p.text.split("\n")
    zone = [ln for _, ln in BatesZone().page_lines(p)]
    assert zone == lines[:3] + lines[-8:], zone
    assert TYPED in zone


@pytest.mark.parametrize("name", sorted(BUILDERS))
def test_a_stamp_on_a_typed_deletion_label_line_is_the_pages_locator(name):
    """_zone_stamp, through apply_bates: the page's own stamp on a line starting
    with the label is read."""
    p = _one_page(name, ON_LINE)
    out = apply_bates((document(name, (p,)),), CONFIRMED)
    assert out[0].pages[0].bates == "iiCON900123", p.text


def test_an_ocr_pages_typed_deletion_label_line_moves_neither_the_trigger_nor_detection():
    """An OCR'd page: the footer re-OCR trigger (zone_has_candidate, over the OCR
    text before a record exists) and detection read the typed line, so the tail
    does not move up onto the stamp-shaped line above it."""
    shift = "\n".join(SHIFT)
    assert zone_has_candidate(shift) is False, (
        "the re-OCR trigger saw a candidate above the tail: the typed line was skipped")
    assert detect_candidates((document("prod/scan.pdf", (_ocr(1, shift),)),)) == ()


def test_an_ocr_pages_stamp_on_a_typed_deletion_label_line_is_applied():
    exact = _ocr(1, "\n".join(ON_LINE))
    assert apply_bates((document("prod/scan.pdf", (exact,)),), CONFIRMED)[0].pages[0].bates \
        == "iiCON900123", "the exact stamp on the typed line was not read"


def test_near_miss_repair_reads_a_typed_deletion_label_line():
    """_zone_near_miss (D-28, gated on kind OCR) over a single-prefix matter."""
    clean = document("prod/clean.pdf", tuple(_ocr(i, f"body\niiCON{900000 + i:06d}")
                                             for i in range(1, 7)))
    misread = document("prod/x.pdf", (_ocr(1, "photo\n[deleted by fax header] jiCON900123"),))
    app = apply_bates_reported((clean, misread), CONFIRMED)
    assert app.matter_prefixes == ("iiCON",)
    assert app.documents[1].pages[0].bates == "iiCON900123", (
        "near-miss repair skipped the typed line")


# ---------------------------------------------------------------------------
# The Word reader: the span names its list, and only its list
# ---------------------------------------------------------------------------


def _footer(*paras: str) -> dict:
    return dict(sect='<w:footerReference w:type="default" r:id="rIdF"/>',
                parts={"word/footer1.xml": _part("ftr", "".join(_p(_t(x)) for x in paras))},
                rels=[("rIdF", "footer", "footer1.xml")])


def test_the_word_reader_marks_its_deletion_list_in_the_last_eight_lines():
    body = (_p(_t("Letter of 3 March 2019, invented."))
            + _p(_t("Superseded text:"), _del("Ann", _dt("issued as MNFV 000999")))
            + _p(_t("More text."), _del("Bob", _dt("MNFV 000998"))))
    got = ex.extract("stamped.docx", _raw_docx(body, **_footer("MNFV 000777")))
    p = got.pages[0]
    lines = p.text.split("\n")
    assert lines == ["Letter of 3 March 2019, invented.", "Superseded text:", "More text.",
                     "[deleted by Ann] issued as MNFV 000999", "[deleted by Bob] MNFV 000998",
                     "MNFV 000777"], lines
    assert p.deletion_line_span == (3, 2)
    assert [ln for _, ln in BatesZone().page_lines(p)] == [lines[0], lines[1], lines[2],
                                                           "MNFV 000777"]
    doc = document("stamped.docx", got.pages)
    assert [c.raw for c in detect_candidates((doc,))] == ["MNFV 000777"]
    assert apply_bates((doc,), CONFIRMED_MNFV)[0].pages[0].bates == "MNFV 000777"


CONFIRMED_MNFV = BatesDecision(DecisionStatus.CONFIRMED, BatesFormat(
    prefix="MNFV", separator=" ", digit_widths=(6,), suffix=None, suffix_sep=""))


def test_a_word_page_shorter_than_the_zone_leaves_its_list_out_after_collapsed_blanks():
    """Two lines of body, blank paragraphs that normalization collapses, and the
    list: every line is inside the 3 + 8 zone by position, so only the span keeps
    the deleted stamp out."""
    body = (_p(_t("Short note.")) + _p() + _p() + _p() + _p()
            + _p(_del("Ann", _dt("MNFV 000999"))))
    p = ex.extract("bare.docx", _raw_docx(body)).pages[0]
    lines = p.text.split("\n")
    assert lines == ["Short note.", "", "[deleted by Ann] MNFV 000999"], lines
    assert p.deletion_line_span == (2, 1)
    assert [ln for _, ln in BatesZone().page_lines(p)] == ["Short note."]
    doc = document("bare.docx", (p,))
    assert detect_candidates((doc,)) == ()
    assert apply_bates((doc,), CONFIRMED_MNFV)[0].pages[0].bates is None


def test_a_word_body_line_typed_as_a_deletion_is_the_documents_text():
    """The class inside Word itself: a body paragraph that TYPES the label is not
    a tracked deletion. It stays in the zone and its stamp is the locator; the
    real list after it is skipped."""
    body = (_p(_t("Invented cover note."))
            + _p(_t("[deleted by the registry] MNFV 000555"))
            + _p(_t("Kept."), _del("Ann", _dt("MNFV 000999"))))
    p = ex.extract("typed.docx", _raw_docx(body)).pages[0]
    lines = p.text.split("\n")
    assert lines == ["Invented cover note.", "[deleted by the registry] MNFV 000555", "Kept.",
                     "[deleted by Ann] MNFV 000999"], lines
    doc = document("typed.docx", (p,))
    assert apply_bates((doc,), CONFIRMED_MNFV)[0].pages[0].bates == "MNFV 000555", (
        "the typed body line was skipped as if it were a listed deletion")
    assert p.deletion_line_span == (3, 1), "the span must name the listed passage only"


def test_a_word_page_with_no_tracked_deletion_carries_no_span():
    p = ex.extract("plain.docx", _raw_docx(_p(_t("Nothing deleted.")))).pages[0]
    assert p.deletion_line_span is None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

WORD_TEXT = "Body.\n[deleted by Ann] gone\nfooter"


def test_a_deletion_line_span_is_refused_on_every_kind_but_synthetic():
    """Derived over every PageKind, so a kind added later is held to it too."""
    ok = PageRecord(page_no=1, text=WORD_TEXT, kind=PageKind.SYNTHETIC,
                    deletion_line_span=(1, 1))
    ok.validate()
    for kind in PageKind:
        if kind is PageKind.SYNTHETIC:
            continue
        for kw in (dict(ocr_conf=0.9, ocr_line_count=1, image_line_span=(0, 1)),
                   dict(ocr_conf=0.9, ocr_line_count=1), dict()):
            base = PageRecord(page_no=1, text=WORD_TEXT, kind=kind, **kw)  # type: ignore[arg-type]
            try:
                base.validate()
            except ContractViolation:
                continue
            break
        else:
            base = PageRecord(page_no=1, text="", kind=kind)
        with pytest.raises(ContractViolation, match="deletion_line_span"):
            base.evolve(deletion_line_span=(1, 1)).validate()


def test_a_deletion_line_span_must_lie_inside_the_page_and_name_listed_lines():
    for bad in ((-1, 1), (0, 0), (1, 0), (2, 2), (3, 1), (1,), (1, 1, 1), [1, 1],
                (True, 1), (1.0, 1)):
        p = PageRecord(page_no=1, text=WORD_TEXT, kind=PageKind.SYNTHETIC,
                       deletion_line_span=bad)  # type: ignore[arg-type]
        with pytest.raises(ContractViolation, match="deletion_line_span"):
            p.validate()
    # In range, but pointing at lines the reader did not list: a stale span.
    for stale in ((0, 1), (0, 2), (1, 2), (2, 1)):
        p = PageRecord(page_no=1, text=WORD_TEXT, kind=PageKind.SYNTHETIC,
                       deletion_line_span=stale)
        with pytest.raises(ContractViolation, match="not deletion-list lines"):
            p.validate()


def test_make_page_refuses_a_deletion_span_over_text_normalization_changes():
    from dociq.ingest.pagemodel import make_page

    with pytest.raises(ContractViolation, match="deletion_line_span"):
        make_page(1, "\n\nBody.\n[deleted by Ann] gone", PageKind.SYNTHETIC,
                  deletion_line_span=(1, 1))
    got = make_page(1, "Body.\n[deleted by Ann] gone", PageKind.SYNTHETIC,
                    deletion_line_span=(1, 1))
    assert got.deletion_line_span == (1, 1)


def test_page_lines_refuses_a_record_carrying_both_spans():
    both = PageRecord(page_no=1, text="a\n[deleted by Ann] b\nc", kind=PageKind.MIXED,
                      ocr_conf=0.9, ocr_line_count=1, image_line_span=(2, 1),
                      deletion_line_span=(1, 1))
    with pytest.raises(ContractViolation):
        both.validate()
    with pytest.raises(ContractViolation, match="both"):
        BatesZone().page_lines(both)


# ---------------------------------------------------------------------------
# Resume, the acceptance harness, and the parse tree
# ---------------------------------------------------------------------------


def test_a_journal_older_than_the_field_is_not_replayed():
    """A page journaled before D-59 has no key; its deletion list, if any, could
    not be located, so the journal is refused (KeyError, which _load_resume
    answers by re-extracting) rather than replayed with the list in the zone."""
    from dociq.contracts import to_jsonable

    p = PageRecord(page_no=1, text=WORD_TEXT, kind=PageKind.SYNTHETIC,
                   deletion_line_span=(1, 1))
    d = json.loads(json.dumps(to_jsonable(p)))
    assert walker._page_from_jsonable(d) == p
    del d["deletion_line_span"]
    with pytest.raises(KeyError):
        walker._page_from_jsonable(d)


def test_the_acceptance_harness_zone_cut_leaves_a_word_pages_list_out():
    import sys

    sys.path.insert(0, str(ROOT / "tools"))
    import bates_acceptance as BA

    lines = ["Letter.", "More.", "[deleted by Ann] MNFV 000999", "MNFV 000777"]
    p = PageRecord(page_no=1, text="\n".join(lines), kind=PageKind.SYNTHETIC,
                   deletion_line_span=(2, 1))
    p.validate()
    reduced = BA.zone_only(document("w.docx", (p,)))
    q = reduced.pages[0]
    q.validate()
    assert q.deletion_line_span is None
    assert q.text.split("\n") == ["Letter.", "More.", "MNFV 000777"]
    assert [c.raw for c in detect_candidates((reduced,))] == ["MNFV 000777"]


def _calls_with_keyword(path: pathlib.Path, keyword: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(fn):
                if isinstance(node, ast.Call) and any(k.arg == keyword for k in node.keywords):
                    found.add(fn.name)
    return found


def test_only_the_word_reader_sets_a_deletion_line_span():
    """Pinned by the parse tree: across src/, the only calls passing
    ``deletion_line_span=`` are the Word reader's, make_page's own construction
    and the resume journal's rebuild. A second setter -- say, one deriving the
    span from lines that start with the label -- is the defect D-59 removed."""
    setters = {}
    for path in sorted((ROOT / "src" / "dociq").rglob("*.py")):
        for fn in _calls_with_keyword(path, "deletion_line_span"):
            setters.setdefault(path.relative_to(ROOT).as_posix(), set()).add(fn)
    assert setters == {"src/dociq/ingest/extract.py": {"_extract_docx"},
                       "src/dociq/ingest/pagemodel.py": {"make_page"},
                       "src/dociq/ingest/walker.py": {"_page_from_jsonable"}}, setters


def test_no_zone_read_matches_the_deletion_label():
    """identify/bates.py imports the label only to re-export it; it never reads
    it, so no zone line is chosen or skipped by what it says. And every zone read
    of a page goes through BatesZone.page_lines."""
    src = ROOT / "src" / "dociq" / "identify" / "bates.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    loads = [n.lineno for n in ast.walk(tree)
             if isinstance(n, ast.Name) and n.id == "TRACKED_DELETION_LABEL"
             and isinstance(n.ctx, ast.Load)]
    assert not loads, f"bates.py reads the deletion label at line(s) {loads}"
    slicers = set()
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(fn):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "slice_lines"):
                    slicers.add(fn.name)
    assert slicers == {"page_lines", "zone_has_candidate"}, slicers


# ---------------------------------------------------------------------------
# slice_lines, sentence by sentence (Word review round 4, finding 1)
#
# Every property BatesZone.slice_lines' docstring asserts, and the test a
# mutant of it fails (word_fix5 mutation run):
#   (a) indices stay positions in ``text``, even past a span
#         -> test_slice_lines_indices_count_the_whole_text_past_a_span
#   (b) head first then tail, in text order
#         -> test_slice_lines_indices_count_the_whole_text_past_a_span (exact order)
#   (c) no line repeated when the page is shorter than the zone
#         -> test_slice_lines_repeats_no_line_on_a_page_shorter_than_the_zone
#   (d) the span is not part of the zone
#         -> test_slice_lines_leaves_the_span_out_of_a_short_page
#   (e) the span counts toward neither bound: head
#         -> test_slice_lines_head_is_chosen_from_the_lines_outside_the_span,
#            test_a_word_stamp_just_below_a_list_in_the_head_stays_in_the_zone
#   (f) ... nor tail
#         -> test_slice_lines_tail_is_chosen_from_the_lines_outside_the_span,
#            test_a_word_stamp_just_above_a_list_in_the_tail_stays_in_the_zone
#   (g) no line skipped for what it says
#         -> test_slice_lines_with_no_span_skips_no_line_for_what_it_says
#            (and the format tests at the top of this file)
#   (h) lines stripped; a blank line takes its place in a bound, unreturned
#         -> test_slice_lines_strips_lines_and_a_blank_line_holds_its_place
#   (i) a span outside the text is refused, never clipped
#         -> test_slice_lines_refuses_a_span_outside_the_text
# ---------------------------------------------------------------------------

Z = BatesZone()


def _numbered(n: int) -> str:
    return "\n".join(f"L{i}" for i in range(n))


def _zone(text: str, skip=None) -> list[tuple[int, str]]:
    return list(Z.slice_lines(text, skip=skip))


def test_slice_lines_indices_count_the_whole_text_past_a_span():
    got = _zone(_numbered(20), skip=(8, 3))
    assert got == [(0, "L0"), (1, "L1"), (2, "L2")] + [(i, f"L{i}") for i in range(12, 20)]


def test_slice_lines_repeats_no_line_on_a_page_shorter_than_the_zone():
    assert _zone(_numbered(5)) == [(i, f"L{i}") for i in range(5)]
    assert _zone(_numbered(11)) == [(i, f"L{i}") for i in range(11)]


def test_slice_lines_leaves_the_span_out_of_a_short_page():
    assert _zone(_numbered(6), skip=(2, 2)) == [(0, "L0"), (1, "L1"), (4, "L4"), (5, "L5")]


def test_slice_lines_head_is_chosen_from_the_lines_outside_the_span():
    """A span at the top pushes the head down past it: three lines, none of
    them the span's, and the tail is still the text's own last eight."""
    got = _zone(_numbered(20), skip=(1, 4))
    assert got == [(0, "L0"), (5, "L5"), (6, "L6")] + [(i, f"L{i}") for i in range(12, 20)]


def test_slice_lines_tail_is_chosen_from_the_lines_outside_the_span():
    """A span near the end pushes the tail up past it: eight lines, none of
    them the span's."""
    got = _zone(_numbered(20), skip=(15, 3))
    assert got == ([(0, "L0"), (1, "L1"), (2, "L2")]
                   + [(i, f"L{i}") for i in (9, 10, 11, 12, 13, 14, 18, 19)])


def test_slice_lines_with_no_span_skips_no_line_for_what_it_says():
    lines = ["[deleted by Ann] head"] + [f"L{i}" for i in range(1, 12)] + [
        "[deleted by Bob] tail"]
    assert _zone("\n".join(lines)) == [(0, lines[0]), (1, "L1"), (2, "L2")] + [
        (i, lines[i]) for i in range(5, 13)]


def test_slice_lines_strips_lines_and_a_blank_line_holds_its_place():
    lines = ["  A  ", "", "B", "C"] + [f"L{i}" for i in range(4, 20)]
    got = _zone("\n".join(lines))
    assert got == [(0, "A"), (2, "B")] + [(i, f"L{i}") for i in range(12, 20)]
    lines[15] = "   "
    got = _zone("\n".join(lines))
    assert got == [(0, "A"), (2, "B")] + [(i, f"L{i}") for i in range(12, 20) if i != 15]


def test_slice_lines_refuses_a_span_outside_the_text():
    for bad in ((-1, 1), (0, 0), (18, 3), (20, 1), (3, -1)):
        with pytest.raises(ValueError, match="does not lie inside"):
            Z.slice_lines(_numbered(20), skip=bad)


# The same two bounds on a real Word page, with the stamp on either side of the
# list, read end to end: extract, page_lines, detect_candidates and
# apply_bates_reported. Twelve footer paragraphs make the page long enough that
# the head and the tail are different lines.

_TWELVE_FOOTER = [f"Footer line {i}" for i in range(1, 12)]


def _read_word(body: str, **kw) -> PageRecord:
    got = ex.extract("zone.docx", _raw_docx(body, **kw))
    assert got.status.value == "full", (got.status, got.error, got.notes)
    return got.pages[0]


def _applied(p: PageRecord) -> tuple[list[str], str | None]:
    doc = document("zone.docx", (p,))
    cands = [c.raw for c in detect_candidates((doc,))]
    app = apply_bates_reported((doc,), CONFIRMED_MNFV)
    return cands, app.documents[0].pages[0].bates


def test_a_word_stamp_just_above_a_list_in_the_tail_stays_in_the_zone():
    """Round 4's reproduction: the stamp is the last body line but one clause
    block, and six deletion-list lines follow. With the list counted toward the
    tail, the tail would be clauses and list, and the stamp would go unread."""
    body = ("".join(_p(_t(f"Paragraph {i}.")) for i in range(6)) + _p(_t("MNFV 000321"))
            + "".join(_p(_t(f"Clause {j}"), _del("Ann", _dt(f"struck wording {j}")))
                      for j in range(6)))
    p = _read_word(body)
    lines = p.text.split("\n")
    assert lines[13:] == [f"[deleted by Ann] struck wording {j}" for j in range(6)], lines
    assert p.deletion_line_span == (13, 6)
    assert list(BatesZone().page_lines(p)) == (
        [(0, "Paragraph 0."), (1, "Paragraph 1."), (2, "Paragraph 2."), (5, "Paragraph 5."),
         (6, "MNFV 000321")] + [(7 + j, f"Clause {j}") for j in range(6)])
    assert _applied(p) == (["MNFV 000321"], "MNFV 000321")


def test_a_word_stamp_below_a_list_in_the_tail_stays_in_the_zone():
    """The list above the stamp: the footer's last line is the stamp, and the
    tail above it is footer and body, never the list."""
    body = ("".join(_p(_t(f"Paragraph {i}.")) for i in range(4))
            + "".join(_p(_t(f"Clause {j}"), _del("Ann", _dt(f"struck wording {j}")))
                      for j in range(3)))
    p = _read_word(body, **_footer("Footer note", "MNFV 000321"))
    lines = p.text.split("\n")
    assert lines == ([f"Paragraph {i}." for i in range(4)] + [f"Clause {j}" for j in range(3)]
                     + [f"[deleted by Ann] struck wording {j}" for j in range(3)]
                     + ["Footer note", "MNFV 000321"]), lines
    assert p.deletion_line_span == (7, 3)
    assert list(BatesZone().page_lines(p)) == (
        [(i, f"Paragraph {i}.") for i in range(4)] + [(4 + j, f"Clause {j}") for j in range(3)]
        + [(10, "Footer note"), (11, "MNFV 000321")])
    assert _applied(p) == (["MNFV 000321"], "MNFV 000321")


def test_a_word_stamp_just_below_a_list_in_the_head_stays_in_the_zone():
    """One body line, then the list, then a footer whose FIRST line is the
    stamp: the head is the body line and the two lines after the list. With the
    list counted toward the head, the head would be the body line and the list,
    and the stamp -- above the eight-line tail -- would go unread."""
    body = _p(_t("Cover. "), _del("Ann", _dt("struck one")), _t("and "),
              _del("Bob", _dt("struck two")))
    p = _read_word(body, **_footer("MNFV 000321", *_TWELVE_FOOTER))
    lines = p.text.split("\n")
    assert lines[:4] == ["Cover. and", "[deleted by Ann] struck one",
                         "[deleted by Bob] struck two", "MNFV 000321"], lines
    assert len(lines) == 15 and p.deletion_line_span == (1, 2)
    assert list(BatesZone().page_lines(p)) == (
        [(0, "Cover. and"), (3, "MNFV 000321"), (4, "Footer line 1")]
        + [(7 + k, f"Footer line {4 + k}") for k in range(8)])
    assert _applied(p) == (["MNFV 000321"], "MNFV 000321")


def test_a_word_stamp_above_a_list_in_the_head_stays_in_the_zone():
    """The stamp heads the page (a header line) and the list follows the body:
    the head is the header and the first two body lines, and the list is in
    neither bound."""
    body = (_p(_t("Cover. "), _del("Ann", _dt("struck one")))
            + _p(_t("Second."), _del("Bob", _dt("struck two"))))
    p = _read_word(body, sect='<w:headerReference w:type="default" r:id="rIdH"/>'
                   '<w:footerReference w:type="default" r:id="rIdF"/>',
                   parts={"word/header1.xml": _part("hdr", _p(_t("MNFV 000321"))),
                          "word/footer1.xml": _part("ftr", "".join(
                              _p(_t(x)) for x in _TWELVE_FOOTER))},
                   rels=[("rIdH", "header", "header1.xml"), ("rIdF", "footer", "footer1.xml")])
    lines = p.text.split("\n")
    assert lines[:5] == ["MNFV 000321", "Cover.", "Second.", "[deleted by Ann] struck one",
                         "[deleted by Bob] struck two"], lines
    assert p.deletion_line_span == (3, 2)
    assert list(BatesZone().page_lines(p)) == (
        [(0, "MNFV 000321"), (1, "Cover."), (2, "Second.")]
        + [(8 + k, f"Footer line {4 + k}") for k in range(8)])
    assert _applied(p) == (["MNFV 000321"], "MNFV 000321")


# ---------------------------------------------------------------------------
# The span's precondition, by construction (round 4, mutants R4M1 and R4M8)
# ---------------------------------------------------------------------------

_BREAKS = [chr(c) for c in range(0x110000)
           if not 0xD800 <= c <= 0xDFFF and len(f"a{chr(c)}b".splitlines()) == 2]
"""Every character str.splitlines breaks a line at, derived from splitlines
itself rather than listed."""

# (deleted text as the XML writes it, the text as it reads, the listed line)
SHAPES = [
    ("the old wording ", "trailing space", "[deleted by Ann] the old wording"),
    ("5\u00a0days", "NBSP", "[deleted by Ann] 5 days"),
    ("cafe\u0301 terms", "decomposed accent", "[deleted by Ann] caf\u00e9 terms"),
    ("line one&#13;line two", "CR", "[deleted by Ann] line one line two"),
    ("line one&#13;&#10;line two", "CRLF", "[deleted by Ann] line one line two"),
    ("line one&#10;&#10;line two", "LF LF", "[deleted by Ann] line one line two"),
    ("line one\u0085line two", "NEL", "[deleted by Ann] line one line two"),
    ("line one\u2028line two", "LS", "[deleted by Ann] line one line two"),
    ("line one\u2029line two", "PS", "[deleted by Ann] line one line two"),
    ("zero\u200bwidth\ufeff", "ZWSP and BOM", "[deleted by Ann] zerowidth"),
    ("tail \u00a0\u200b", "NBSP and ZWSP at the end", "[deleted by Ann] tail"),
]


def test_every_deletion_list_line_is_one_normalized_line_whatever_it_holds():
    """The reader's own line builder: for every break splitlines knows and every
    shape normalize changes, the line is one line, starts with the label, and
    is left as it is by normalize."""
    from dociq.ingest.pagemodel import normalize

    assert {"\n", "\r", "\x85", "\u2028", "\u2029"} <= set(_BREAKS)
    cases = [("Ann", f"x{b}y") for b in _BREAKS] + [
        ("Ann", f"x{a}{b}y") for a in _BREAKS for b in _BREAKS]
    cases += [("Ann", html.unescape(xml)) for xml, _why, _line in SHAPES]
    cases += [(author, "text") for author in ("Ann\u00a0", "An\u0301n", "A\u2028B",
                                              "A\r\nB ", "\u200bAnn")]
    for author, text in cases:
        line = ex._deletion_list_line(author, text)
        assert line.startswith("[deleted by "), (author, text, line)
        assert len(line.splitlines()) == 1 and not any(b in line for b in _BREAKS), (
            author, text, line)
        assert normalize(line) == line, (author, text, line)
    for b in _BREAKS:
        assert ex._deletion_list_line("Ann", f"x{b}{b}y") == "[deleted by Ann] x y", repr(b)


@pytest.mark.parametrize("xml,why,listed", SHAPES, ids=[s[1] for s in SHAPES])
def test_a_word_file_whose_deleted_text_normalize_changes_reads_in_full(xml, why, listed):
    """The shapes that failed an ordinary Word file outright under R4M1 and
    R4M8 (pages=0): each reads FULL, its list line exact, its span on it."""
    body = (_p(_t("Invented notice, kept text."))
            + _p(_t("Clause."), _del("Ann", _dt(xml))))
    got = ex.extract("shapes.docx", _raw_docx(body, **_footer("MNFV 000777")))
    assert got.status.value == "full", (why, got.status, got.error)
    p = got.pages[0]
    assert p.text.split("\n") == ["Invented notice, kept text.", "Clause.", listed,
                                  "MNFV 000777"], (why, p.text)
    assert p.deletion_line_span == (2, 1), why


@pytest.mark.parametrize("filler", ["   ", "\u200b", "\u00a0\u00a0", "\ufeff "],
                         ids=["spaces", "ZWSP", "NBSPs", "BOM and space"])
def test_a_body_line_that_normalizes_blank_does_not_move_the_span(filler):
    """Round 4's R4M2 (the lead counted over the lines as written): a body
    paragraph holding only characters normalization removes is a line before
    the list that the page text does not carry as a non-blank line. The span
    is counted after normalization, so it still names the list exactly."""
    body = (_p(_t("Invented opening.")) + _p(_t(filler)) + _p(_t("Kept clause."))
            + _p(_t("More."), _del("Ann", _dt("struck wording"))))
    got = ex.extract("filler.docx", _raw_docx(body, **_footer("MNFV 000777")))
    assert got.status.value == "full", (filler, got.status, got.error)
    p = got.pages[0]
    lines = p.text.split("\n")
    assert lines == ["Invented opening.", "", "Kept clause.", "More.",
                     "[deleted by Ann] struck wording", "MNFV 000777"], lines
    assert p.deletion_line_span == (4, 1)


# ---------------------------------------------------------------------------
# Identity (round 4, R4M5): the span is hashed like every other page field
# ---------------------------------------------------------------------------


def test_every_page_field_but_ocr_conf_is_part_of_a_pages_identity():
    """The contract says every PageRecord field is serialized and hashed, the
    float ocr_conf alone excepted; two pages that differ only in which lines
    their span names are two identities."""
    from dociq.contracts import _IDENTITY_EXCLUDED, content_hash

    assert {f for f in PageRecord.__dataclass_fields__ if f in _IDENTITY_EXCLUDED} == {
        "ocr_conf"}
    text = "Body.\n[deleted by Ann] a\n[deleted by Bob] b"
    spans = [None, (1, 1), (1, 2), (2, 1)]
    pages = [PageRecord(page_no=1, text=text, kind=PageKind.SYNTHETIC, deletion_line_span=s)
             for s in spans]
    for p in pages:
        p.validate()
    assert len({content_hash(p) for p in pages}) == len(spans)
