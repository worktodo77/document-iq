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
id. All text is invented (D-12).
"""

from __future__ import annotations

import ast
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
