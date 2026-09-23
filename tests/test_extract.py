"""The vendored extractor, per format and per guard."""

from __future__ import annotations

import io
import zipfile

import pytest

from dociq.contracts import PageKind, ProcessingStatus
from dociq.ingest import extract as ex

from .conftest import FIXTURES, REPO_ROOT


def _pages(name: str, opt: ex.ExtractOptions | None = None):
    path = FIXTURES / name
    return ex.extract(path.name, path.read_bytes(), opt)


def _reading() -> ex.ExtractOptions:
    """A-24's image reading, opted into. The default skips it (A-25, D-51), so a
    test ABOUT reading a mixed page's image says so rather than inheriting a
    default that no longer reads one."""
    return ex.ExtractOptions(skip_images_on_text_pages=False)


def test_native_pdf_pages_are_native():
    got = _pages("01_native_report.pdf")
    assert [p.kind for p in got.pages] == [PageKind.NATIVE, PageKind.NATIVE]
    assert all(p.ocr_conf is None for p in got.pages)


def test_scanned_pdf_pages_are_ocr_with_confidence():
    got = _pages("02_scanned_instruction.pdf")
    assert {p.kind for p in got.pages} == {PageKind.OCR}
    assert all(0.0 <= p.ocr_conf <= 1.0 for p in got.pages)
    assert all(p.ocr_line_count > 0 for p in got.pages)


def test_mixed_pdf_routes_page_by_page():
    """The case the §11 audit flagged as untested in the original."""
    got = _pages("03_mixed_transmittal.pdf")
    assert [p.kind for p in got.pages] == [PageKind.NATIVE, PageKind.OCR,
                                           PageKind.NATIVE]
    assert got.pages[1].ocr_conf is not None
    assert got.pages[0].ocr_conf is None and got.pages[2].ocr_conf is None


# ---------------------------------------------------------------------------
# A-24 (D-48): a page that is BOTH native text and image, not mixed PAGES.
# ``test_mixed_pdf_routes_page_by_page`` above never touches this case -- it
# interleaves a native page, a scanned page and a native page, so every page
# still answers only one question. These assert the page whose native text
# layer AND embedded image live on the SAME page.
# ---------------------------------------------------------------------------


def test_image_text_never_moves_a_text_layer_line_out_of_the_bates_zone():
    """A MIXED page's image text must not push the page's own text out of the
    Bates zone, however many image lines there are.

    Measured before this placement existed: image text APPENDED after the text
    layer moved a text-layer zone line out of the zone in 13,081 of 15,000
    randomized layouts. Derived over randomized layouts -- header counts, blank
    runs, leading whitespace, the stamp at the head, middle, end, or above a
    page-number line -- rather than three hand-picked pages, because
    normalization removes and collapses lines, and a hand-picked page would not
    find the layout where that shifts the head boundary.
    """
    import random

    from dociq.identify.bates import BatesZone
    from dociq.ingest.pagemodel import normalize

    zone = BatesZone()

    def zone_lines(text):
        return {ln for _, ln in zone.slice_lines(normalize(text))}

    rng = random.Random(20260910)
    for _ in range(300):
        lines = []
        for i in range(rng.randint(0, 5)):
            lines.append(" " * rng.randint(0, 3) + f"HEADER {i}")
            if rng.random() < 0.3:
                lines.append("")
        for i in range(rng.randint(0, 16)):
            lines.append(f"body {i}")
            if rng.random() < 0.2:
                lines.extend([""] * rng.randint(1, 3))
        where = rng.choice(["last", "above-page-number", "head", "middle"])
        if where == "head" and lines:
            lines.insert(rng.randint(0, min(2, len(lines))), "ABC-0001234")
        elif where == "middle" and lines:
            lines.insert(len(lines) // 2, "ABC-0001234")
        elif where == "above-page-number":
            lines.extend(["ABC-0001234", "Page 7 of 31"])
        else:
            lines.append("ABC-0001234")
        native = "\n".join(lines)
        before = zone_lines(native)
        for k in range(1, 25):
            image = "\n".join(f"chart region {j} 50,00%" for j in range(k))
            after = zone_lines(ex._merge_image_text(native, image))
            assert before <= after, (
                f"{k} image line(s) moved text-layer line(s) {before - after!r} "
                f"out of the Bates zone of {native!r}")


def test_an_embedded_image_cannot_supply_a_stamp_in_place_of_the_pages_own():
    """The consequence the placement exists to prevent, asserted directly.

    With image text appended, eight image lines pushed the page's own footer
    stamp out of the tail zone; a last image line carrying a different stamp (a
    copy of another exhibit embedded in the page) was then the only stamp in the
    zone, and it was returned as this page's locator. Measured before the fix:
    the foreign stamp came back for every k from 8 to 24. A locator that points
    at a different document is the failure criterion 4 forbids outright.

    With the page's own stamp still in the zone, a foreign one beside it can
    only make the zone ambiguous, and an ambiguous zone is refused, never
    guessed.
    """
    from dociq.identify.bates import (BatesFormat, BatesZone,
                                      _confirmed_token_re, _zone_stamp)
    from dociq.ingest.pagemodel import normalize

    zone = BatesZone()
    token = _confirmed_token_re(BatesFormat(prefix="ABC", separator="-",
                                            digit_widths=(7,), suffix=None,
                                            suffix_sep=""))
    native = "\n".join(["WEEKLY PROGRESS REPORT", "Report No.7 (01-Jan-2020 to 07-Jan-2020)",
                        "narrative one", "narrative two", "ABC-0001234"])
    for k in range(1, 25):
        image = "\n".join([f"chart {j} 50,00% 1234" for j in range(k - 1)]
                          + ["Embedded exhibit copy ABC-0009999"])
        got = _zone_stamp(normalize(ex._merge_image_text(native, image)),
                          zone, token)
        assert got != "ABC-0009999", (
            f"with {k} image line(s) the page's locator became the embedded "
            "exhibit's stamp")


def test_a_mixed_pages_image_lines_are_marked_and_kept_out_of_its_locator_text():
    """D-49 at the extractor: the lines read from the embedded image are marked,
    and the locator text is the page's text layer alone."""
    got = _pages("15_mixed_content_page.pdf", _reading())
    page = got.pages[0]
    assert page.kind is PageKind.MIXED
    assert page.image_line_span is not None, "a MIXED page must mark its image lines"
    start, count = page.image_line_span
    image_part = "\n".join(page.text.split("\n")[start:start + count])
    assert "NOTICEOFDELAYNo14" in image_part
    assert "NOTICEOFDELAYNo14" not in page.locator_text
    assert "SYNTHETIC CONTRACTOR LTD MONTHLY REPORT LETTERHEAD" in page.locator_text

    off = _pages("15_mixed_content_page.pdf", ex.ExtractOptions(ocr_enabled=False))
    assert off.pages[0].image_line_span is None


def test_a_page_that_is_both_text_and_image_reads_both():
    """The point of the whole exercise: assert CONTENT, not shape.

    Measured directly against ``tests/fixtures/generated/matter/
    15_mixed_content_page.pdf``: the recovered words of one drawn line come back
    fused, with no inter-word spaces -- "NOTICE OF DELAY No 14" reads back as
    "NOTICEOFDELAYNo14" (the case of "No" preserved). The strings below are
    asserted space-free to match what actually comes back.

    **This is a FIXTURE artifact, not engine behaviour, and the distinction
    matters enough to record.** The same fusing appears in the existing,
    already-green ``02_scanned_instruction.pdf`` ("SITE INSTRUCTION 014" ->
    "SITEINSTRUCTION014"), and the tempting conclusion is that the shipped OCR
    path loses word boundaries. It does not. Both fixtures draw their text
    through ``_image_page``, whose own docstring records the compromise: a
    default PIL bitmap font, rendered per line into a small tile and scaled up
    with LANCZOS "legible to OCR without shipping a TTF". That rendering is what
    defeats word segmentation.

    The counter-evidence is a real page. The same engine, through the same
    ``_ocr_array``, reading a genuine scanned chart from the acceptance corpus
    (``CER-1-462.pdf`` page 11) returns a chart title and its axis values with
    every word correctly spaced. So an assertion here should never be
    read as a statement about production documents, and this fixture must not
    be used to measure OCR word accuracy.

    Left as-is rather than "fixed" with a bundled font: the fixture's job is to
    prove image-region text REACHES the record, and it does that. Changing the
    rendering would move the fixture corpus hash for a property this test does
    not test.
    """
    got = _pages("15_mixed_content_page.pdf", _reading())
    assert len(got.pages) == 1
    page = got.pages[0]

    # 1. The mixed-content page's kind is PageKind.MIXED.
    assert page.kind is PageKind.MIXED

    # 2. The image's text actually reaches the page record.
    assert "NOTICEOFDELAYNo14" in page.text, (
        "the image region's OCR text never reached the page record: "
        + repr(page.text))
    assert "APPROVED12MARCH2019" in page.text, (
        "the image region's OCR text never reached the page record: "
        + repr(page.text))

    # 3. The native text layer is still present on that page.
    letterhead = "SYNTHETIC CONTRACTOR LTD MONTHLY REPORT LETTERHEAD"
    assert "CONFIDENTIALITY LEGEND: FOR INTERNAL USE ONLY" in page.text
    assert letterhead in page.text

    # 4. The letterhead lies BESIDE the image, so the region merge does not
    # repeat it. (Typed text lying ON an image is read again, D-58; that case
    # is held further down.)
    assert page.text.count(letterhead) == 1, (
        "the native letterhead was duplicated by the image-region merge: "
        + repr(page.text))

    # 5. A MIXED page must carry ocr_conf (the contract's own invariant).
    assert page.ocr_conf is not None

    # 6. A document note discloses the mixed handling.
    assert any("mixed" in n for n in got.notes), (
        "no document note discloses the mixed page/image handling: "
        + repr(got.notes))


def test_a_mixed_page_with_ocr_disabled_stays_native_and_discloses_the_unread_image():
    """Disclosure instead of silence (§ B-3's rule, applied to A-24): with OCR
    off the image region is never read, so the page must NOT become MIXED and
    the gap must be named rather than swallowed."""
    got = _pages("15_mixed_content_page.pdf", ex.ExtractOptions(ocr_enabled=False))
    assert len(got.pages) == 1
    page = got.pages[0]

    # 7. The page does not become MIXED, and a note naming M_IMAGE_UNREAD
    # is emitted.
    assert page.kind is not PageKind.MIXED
    assert "SYNTHETIC CONTRACTOR LTD MONTHLY REPORT LETTERHEAD" in page.text
    assert "NOTICEOFDELAYNo14" not in page.text

    joined = " ".join(got.notes)
    assert ex.M_IMAGE_UNREAD in joined, (
        "the unread image was not disclosed at all: " + repr(got.notes))

    # 8. M_IMAGE_UNREAD is correctly classified in the marker vocabulary.
    assert ex.has_evidence_marker(joined), (
        "M_IMAGE_UNREAD note does not register as an evidence marker: "
        + repr(got.notes))


# ---------------------------------------------------------------------------
# A-25 (D-51): the run that leaves those images unread ON PURPOSE
# ---------------------------------------------------------------------------


def test_a_skipped_image_is_disclosed_apart_from_a_disabled_or_missing_engine(
        monkeypatch):
    """Three reasons a mixed page's image goes unread, and three remedies: untick
    the quick-pass box, turn OCR on, install the models. One sentence for all
    three would send the operator to the wrong one.

    All three keep ``M_IMAGE_UNREAD``, because all three leave an image's words
    out of the corpus. Only the skip marks the PAGE, which stays NATIVE -- nothing
    on it was read by OCR -- and the document note names the page, because page
    notes do not reach the processing log and the setup screen promises that
    every page skipped is listed.
    """
    def unread(got):
        return [n for n in got.notes if n.startswith(ex.M_IMAGE_UNREAD)]

    skipped = _pages("15_mixed_content_page.pdf",
                     ex.ExtractOptions(skip_images_on_text_pages=True))
    disabled = _pages("15_mixed_content_page.pdf",
                      ex.ExtractOptions(ocr_enabled=False,
                                        skip_images_on_text_pages=False))
    monkeypatch.setattr(ex, "ocr_available", lambda: False)
    unavailable = _pages("15_mixed_content_page.pdf", _reading())
    # The skip is decided BEFORE the engine check: a run that reads nothing
    # needs no engine to say so. Gated on the engine instead, an install
    # without its models would report "unavailable" for a page the operator
    # chose to skip, and point them at the wrong remedy (D-51 review, M11).
    skipped_no_engine = _pages("15_mixed_content_page.pdf",
                               ex.ExtractOptions(skip_images_on_text_pages=True))

    sentences = [unread(g) for g in (skipped, disabled, unavailable)]
    assert all(len(s) == 1 for s in sentences), sentences
    assert len({s[0] for s in sentences}) == 3, (
        "a skipped image, OCR disabled and a missing engine read as one "
        f"disclosure: {sentences}")
    assert "page(s) 1" in sentences[0][0], sentences[0][0]
    assert unread(skipped_no_engine) == sentences[0], (
        "with no OCR engine, a skipping run did not say it skipped: "
        f"{skipped_no_engine.notes}")
    assert skipped_no_engine.pages[0].notes == (ex.M_IMAGE_SKIPPED,)
    # `_page_image_share` SUMS every draw, overlaps and repeats included, so the
    # sentences say the drawn areas ADD UP to the share. "an image covering"
    # claimed one image did (D-51 review), and "image content covering" claimed
    # the page showed that much, which a picture drawn twice does not (D-51
    # round-3 review).
    for sentence in (sentences[0][0], sentences[1][0]):
        assert "images whose drawn areas add up to 25% or more" in sentence, sentence
        assert "an image covering" not in sentence, sentence
        assert "content covering" not in sentence, sentence

    page = skipped.pages[0]
    assert page.kind is PageKind.NATIVE
    assert page.notes == (ex.M_IMAGE_SKIPPED,)
    assert "NOTICEOFDELAYNo14" not in page.text
    for other in (disabled, unavailable):
        assert ex.M_IMAGE_SKIPPED not in other.pages[0].notes


def _mixed_page(c, words: str) -> None:
    """A letterhead in the text layer beside a chart-sized image (~32% of the
    page) carrying ``words`` -- fixture 15's page, as one page of a longer file."""
    from PIL import Image, ImageDraw
    from reportlab.lib.utils import ImageReader

    y = 760
    for line in ["CONFIDENTIALITY LEGEND: FOR INTERNAL USE ONLY",
                 "SYNTHETIC CONTRACTOR LTD MONTHLY REPORT LETTERHEAD"]:
        c.drawString(60, y, line)
        y -= 18
    img = Image.new("L", (1240, 700), 255)
    tile = Image.new("L", (620, 40), 255)
    ImageDraw.Draw(tile).text((4, 8), words, fill=0)
    img.paste(tile.resize((1116, 72), Image.LANCZOS), (60, 80))
    c.drawImage(ImageReader(img), 40, 40, width=530, height=300)
    c.showPage()


def test_the_skip_notes_name_exactly_the_skipped_pages_of_a_longer_pdf(tmp_path):
    """D-51 review finding 4. Every skip-note test used fixture 15, one page, so
    a note stamped on every page of the document and a page list cut to its first
    entry both survived the whole suite. Four pages, in the order a production
    has them: a typed transmittal, a letterhead over a chart, a scan, another
    letterhead over a chart.
    """
    import make_fixtures as mf

    path = tmp_path / "four_pages.pdf"
    c = mf._pdf_canvas(path)
    mf._text_page(c, ["TRANSMITTAL 2024-07-16",
                      "Attached: one scanned instruction sheet and two reports."])
    _mixed_page(c, "NOTICE OF DELAY No 14")
    mf._image_page(c, ["SITE INSTRUCTION 015", "DATED 2024-07-17"])
    _mixed_page(c, "NOTICE OF DELAY No 15")
    c.save()

    got = ex.extract(path.name, path.read_bytes())
    assert [p.kind for p in got.pages] == [
        PageKind.NATIVE, PageKind.NATIVE, PageKind.OCR, PageKind.NATIVE]
    marked = [p.page_no for p in got.pages if ex.M_IMAGE_SKIPPED in p.notes]
    assert marked == [2, 4], marked
    (note,) = [n for n in got.notes if n.startswith(ex.M_IMAGE_UNREAD)]
    assert note.startswith(f"{ex.M_IMAGE_UNREAD}: 2 page(s) "), note
    assert note.endswith("page(s) 2, 4"), note


# ---------------------------------------------------------------------------
# D-54: a scan that carries a typed stamp is still read in the quick pass
# ---------------------------------------------------------------------------

_STAMP = "HIGHLY CONFIDENTIAL - ATTORNEYS EYES ONLY  ABC 000123"
"""An electronic endorsement longer than ``_NATIVE_TEXT_FLOOR`` (its length is
asserted where it matters, not copied into prose), so the page is not OCR'd
whole, which is what made a stamp decide whether it was read."""

_A4 = (595.0, 842.0)


def _flat(text: str) -> str:
    """Page text with every space and line break removed, upper-cased: OCR of
    the fixtures' bitmap words fuses them, so content is matched without them."""
    return "".join(text.split()).upper()


def _words_picture(lines, size=(1240, 1754)):
    """A PIL image carrying ``lines`` in the fixtures' bitmap font."""
    from PIL import Image, ImageDraw

    img = Image.new("L", size, 255)
    y = 60
    for line in lines:
        tile = Image.new("L", (620, 40), 255)
        ImageDraw.Draw(tile).text((4, 8), line, fill=0)
        img.paste(tile.resize((1116, 72), Image.LANCZOS), (40, y))
        y += 120
    return img


def _stamped_pdf(path, pages, text_layer=(_STAMP,)) -> None:
    """One PDF page per entry of ``pages``: each entry is a list of image rects
    ``(x, y, w, h)`` in points, every image carrying its OWN words (identical
    image bytes are stored once), and ``text_layer``'s lines typed at the foot
    of the page, top line first."""
    import make_fixtures as mf
    from reportlab.lib.utils import ImageReader

    c = mf._pdf_canvas(path)
    n = 0
    for rects in pages:
        c.setPageSize(_A4)
        for x, y, w, h in rects:
            img = _words_picture([f"SITE INSTRUCTION 0{n:02d}"],
                                 size=(1240, max(200, int(1240 * h / w))))
            c.drawImage(ImageReader(img), x, y, width=w, height=h)
            n += 1
        for j, line in enumerate(text_layer):
            c.drawString(40, 20 + 14 * (len(text_layer) - 1 - j), line)
        c.showPage()
    c.save()


def _text_layer_len(raw: bytes, index: int = 0) -> int:
    """The measure the routing uses: the page's pypdf text, stripped."""
    from pypdf import PdfReader

    return len((PdfReader(io.BytesIO(raw)).pages[index].extract_text() or "").strip())


def _share(raw: bytes, index: int = 0) -> float:
    import fitz  # pymupdf

    with fitz.open(stream=raw, filetype="pdf") as doc:
        return ex._page_image_share(doc[index])[0]


def _read_as_a_reading_run(name: str, raw: bytes):
    """Extract with the default (skipping) options, and assert the result is
    byte-for-byte what a reading run produces: same pages, same notes. D-54
    reads the page "exactly as a reading run reads it", D-49's locator rule
    included, so anything short of equality is a second reading path."""
    default = ex.extract(name, raw)
    reading = ex.extract(name, raw, _reading())
    assert default.pages == reading.pages
    assert default.notes == reading.notes
    return default


def test_the_scan_threshold_is_named_and_is_d54s():
    """D-54 proposed 90% and required it be stated in code; the MIXED threshold
    it sits above is Tier 3's 25%."""
    assert ex._SCAN_MIN_IMAGE_SHARE == 0.90
    assert ex._SCAN_MIN_IMAGE_SHARE > ex.PHOTO_MIN_IMAGE_AREA_SHARE


def test_a_full_page_scan_with_a_typed_stamp_is_read_on_a_default_run(tmp_path):
    """The review's ``endorsed_long.pdf`` shape. A full-page scan with an
    endorsement over ``_NATIVE_TEXT_FLOOR`` in its text layer came out NATIVE on
    a default run, its text the endorsement alone -- while the same scan with no
    stamp, or a stamp under the floor, is OCR'd whole. D-54: read it.

    FAIL-BEFORE: NATIVE, the skip note, and none of the scan's words.
    """
    path = tmp_path / "endorsed_long.pdf"
    _stamped_pdf(path, [[(0, 0, *_A4)]])
    raw = path.read_bytes()
    assert _share(raw) == 1.0
    assert _text_layer_len(raw) == len(_STAMP) >= ex._NATIVE_TEXT_FLOOR

    got = _read_as_a_reading_run(path.name, raw)
    page = got.pages[0]
    assert page.kind is PageKind.MIXED, (page.kind, page.notes, got.notes)
    assert "SITEINSTRUCTION" in page.text.replace(" ", ""), page.text
    assert ex.M_IMAGE_SKIPPED not in page.notes
    # D-49, unchanged: the locator comes from the text layer alone.
    assert page.image_line_span is not None
    assert page.locator_text.strip() == _STAMP
    assert not any("skips images" in n for n in got.notes), got.notes


@pytest.mark.parametrize("offset, read", [(-0.005, False), (0.0, True)],
                         ids=["just-below", "at"])
def test_the_scan_threshold_boundary(tmp_path, offset, read):
    """Built from the constant, so each page sits where its name says; the share
    is measured before the extraction is asserted, so a builder that misses the
    boundary fails as a builder rather than passing as a test."""
    share_wanted = ex._SCAN_MIN_IMAGE_SHARE + offset
    path = tmp_path / "boundary.pdf"
    _stamped_pdf(path, [[(0, 0, _A4[0] * share_wanted, _A4[1])]])
    raw = path.read_bytes()
    share = _share(raw)
    assert share == pytest.approx(share_wanted)
    assert (share >= ex._SCAN_MIN_IMAGE_SHARE) is read
    assert share >= ex.PHOTO_MIN_IMAGE_AREA_SHARE

    if read:
        page = _read_as_a_reading_run(path.name, raw).pages[0]
        assert page.kind is PageKind.MIXED, (page.kind, page.notes)
    else:
        got = ex.extract(path.name, raw)
        page = got.pages[0]
        assert page.kind is PageKind.NATIVE
        assert page.notes == (ex.M_IMAGE_SKIPPED,)
        assert page.text.strip() == _STAMP


def test_image_content_summed_from_several_images_counts_as_a_scan(tmp_path):
    """``_page_image_share`` SUMS the page's images. Four images, none covering
    even the 25% that makes a page MIXED on its own, together cover 95% of the
    page -- a scan assembled from tiles -- and the page is read.

    FAIL-BEFORE: skipped, NATIVE.
    """
    import fitz  # pymupdf

    w, h = _A4[0] / 2 * 0.975, _A4[1] / 2 * 0.975
    tiles = [(0, 0, w, h), (_A4[0] / 2, 0, w, h),
             (0, _A4[1] / 2, w, h), (_A4[0] / 2, _A4[1] / 2, w, h)]
    path = tmp_path / "tiled_scan.pdf"
    _stamped_pdf(path, [tiles])
    raw = path.read_bytes()
    with fitz.open(stream=raw, filetype="pdf") as doc:
        page0 = doc[0]
        own = [abs(page0.get_image_bbox(info).get_area()) / abs(page0.rect.get_area())
               for info in page0.get_images(full=True)]
    assert len(own) == 4 and all(s < ex.PHOTO_MIN_IMAGE_AREA_SHARE for s in own), own
    assert _share(raw) >= ex._SCAN_MIN_IMAGE_SHARE

    page = _read_as_a_reading_run(path.name, raw).pages[0]
    assert page.kind is PageKind.MIXED, (page.kind, page.notes)


def test_in_one_document_the_scan_is_read_and_only_the_chart_page_is_skipped(
        tmp_path):
    """The rule is per page. A stamped scan and a letterhead over a chart in one
    file: the scan is read, the chart page is skipped, and the document note
    counts and names the chart page alone."""
    path = tmp_path / "scan_and_chart.pdf"
    _stamped_pdf(path, [[(0, 0, *_A4)], [(40, 40, 530, 300)]])
    got = ex.extract(path.name, path.read_bytes())
    assert [p.kind for p in got.pages] == [PageKind.MIXED, PageKind.NATIVE]
    assert [p.page_no for p in got.pages if ex.M_IMAGE_SKIPPED in p.notes] == [2]
    (note,) = [n for n in got.notes if n.startswith(ex.M_IMAGE_UNREAD)]
    assert note.startswith(f"{ex.M_IMAGE_UNREAD}: 1 page(s) "), note
    assert note.endswith("page(s) 2"), note


_ENDORSEMENT = tuple(f"PRODUCED UNDER PROTECTIVE ORDER - CONFIDENTIAL - ABC 00{k:04d}"
                     for k in range(120, 125))
"""Five typed lines: a production endorsement several times the text floor."""


@pytest.mark.parametrize("text_layer, length, kind", [
    (("CONFIDENTIAL PRODUCTION ABC-000123 SUBJECT"[:39],), 39, PageKind.OCR),
    (("CONFIDENTIAL PRODUCTION ABC-000123 SUBJECT"[:40],), 40, PageKind.MIXED),
    (_ENDORSEMENT, None, PageKind.MIXED),
], ids=["just-under-the-text-floor", "at-the-text-floor", "long-endorsement"])
def test_a_scan_is_read_on_a_default_run_however_long_its_text_layer(
        tmp_path, text_layer, length, kind):
    """D-51 round-2 review, finding 2. Every D-54 test used the one stamp, so a
    scan branch that let a LONGER text layer decide again (the review's O6,
    ``and len(native[i].strip()) < 120``) survived. Under the floor the page is
    OCR'd whole; at it and far over it, it is read as a scan. The length is
    measured the way the routing measures it before anything is asserted.
    """
    path = tmp_path / "scan.pdf"
    _stamped_pdf(path, [[(0, 0, *_A4)]], text_layer=text_layer)
    raw = path.read_bytes()
    measured = _text_layer_len(raw)
    if length is None:
        assert measured >= 5 * ex._NATIVE_TEXT_FLOOR, measured
    else:
        assert measured == length, measured
    assert (measured >= ex._NATIVE_TEXT_FLOOR) is (kind is PageKind.MIXED)

    got = ex.extract(path.name, raw)
    page = got.pages[0]
    assert page.kind is kind, (page.kind, page.notes, got.notes)
    assert "SITEINSTRUCTION" in _flat(page.text), page.text
    assert ex.M_IMAGE_SKIPPED not in page.notes
    assert not any(n.startswith(ex.M_IMAGE_UNREAD) for n in got.notes), got.notes
    if kind is PageKind.MIXED:
        for line in text_layer:
            assert line in page.locator_text, (line, page.locator_text)


def test_a_quick_pass_with_no_engine_counts_only_the_pages_it_meant_to_read(
        tmp_path, monkeypatch):
    """D-51 round-2 review, finding 2. A quick pass over a stamped scan and a
    chart page, with the engine unavailable. The scan was to be read and could
    not be; the chart page was skipped by choice. Each note counts its own
    page: a note counting both (the review's O4) overstates the loss, and an
    engine check skipped for quick passes (O7) reads the scan anyway.
    """
    path = tmp_path / "scan_and_chart.pdf"
    _stamped_pdf(path, [[(0, 0, *_A4)], [(40, 40, 530, 300)]])
    monkeypatch.setattr(ex, "ocr_available", lambda: False)
    got = ex.extract(path.name, path.read_bytes())

    assert [p.kind for p in got.pages] == [PageKind.NATIVE, PageKind.NATIVE]
    assert "SITEINSTRUCTION" not in _flat(got.pages[0].text)
    assert got.pages[0].notes == ()
    assert got.pages[1].notes == (ex.M_IMAGE_SKIPPED,)
    unread = [n for n in got.notes if n.startswith(ex.M_IMAGE_UNREAD)]
    assert len(unread) == 2, got.notes
    (missing,) = [n for n in unread if "OCR is unavailable" in n]
    (skip,) = [n for n in unread if n is not missing]
    assert missing.startswith(f"{ex.M_IMAGE_UNREAD}: 1 page(s) "), missing
    assert skip.startswith(f"{ex.M_IMAGE_UNREAD}: 1 page(s) "), skip
    assert skip.endswith("page(s) 2"), skip


def test_the_skip_note_lists_its_pages_in_page_order(tmp_path):
    """D-51 round-2 review. The skipped pages are a frozenset and the note joins
    them sorted; every earlier test's pages ({1}, {2, 4}, {2}) iterate in order
    anyway, so dropping ``sorted`` (W13) survived. Pages 2 and 9 of ten do not:
    as a set, {1, 8} iterates 8 first.
    """
    import make_fixtures as mf

    path = tmp_path / "ten_pages.pdf"
    c = mf._pdf_canvas(path)
    for n in range(1, 11):
        if n in (2, 9):
            _mixed_page(c, f"NOTICE OF DELAY No {n}")
        else:
            mf._text_page(c, [f"TRANSMITTAL PAGE {n} OF 10",
                              "Typed correspondence with no picture on the page."])
    c.save()
    assert list(frozenset({1, 8})) != [1, 8], "the set no longer iterates out of order"

    got = ex.extract(path.name, path.read_bytes())
    assert [p.page_no for p in got.pages if ex.M_IMAGE_SKIPPED in p.notes] == [2, 9]
    (note,) = [n for n in got.notes if n.startswith(ex.M_IMAGE_UNREAD)]
    assert note.endswith("page(s) 2, 9"), note


def test_a_small_picture_beside_typed_text_is_not_read_on_either_setting(tmp_path):
    """Pinned, because the help text now says it: a picture covering less than
    ``PHOTO_MIN_IMAGE_AREA_SHARE`` of a page with a text layer is below A-24's
    threshold, so neither setting reads it and neither lists it."""
    import make_fixtures as mf
    from reportlab.lib.utils import ImageReader

    path = tmp_path / "small_picture.pdf"
    c = mf._pdf_canvas(path)
    c.setPageSize(_A4)
    c.drawString(60, 780, "SYNTHETIC CONTRACTOR LTD MONTHLY REPORT LETTERHEAD")
    c.drawImage(ImageReader(_words_picture(["SITE INSTRUCTION 016"], size=(1240, 700))),
                60, 400, width=_A4[0] * 0.5, height=_A4[1] * 0.35)
    c.showPage()
    c.save()
    raw = path.read_bytes()
    assert 0 < _share(raw) < ex.PHOTO_MIN_IMAGE_AREA_SHARE

    for opt in (ex.ExtractOptions(), _reading()):
        got = ex.extract(path.name, raw, opt)
        page = got.pages[0]
        assert page.kind is PageKind.NATIVE and page.notes == (), page
        assert "SITEINSTRUCTION" not in _flat(page.text)
        assert not any(n.startswith(ex.M_IMAGE_UNREAD) for n in got.notes), got.notes


# ---------------------------------------------------------------------------
# Every placement of every image is measured and read (D-51 round-2 review)
# ---------------------------------------------------------------------------


def test_a_scan_first_drawn_as_a_thumbnail_is_measured_at_every_placement(tmp_path):
    """One image object drawn twice: a thumbnail, then the full page. The
    measure took the first placement only, so the page measured as a 2% picture
    and the scan went unread on both settings with no note at all.

    FAIL-BEFORE: a share of about 0.016, NATIVE, none of the scan's words.
    """
    import fitz  # pymupdf
    import make_fixtures as mf
    from reportlab.lib.utils import ImageReader

    path = tmp_path / "thumbnail_first.pdf"
    c = mf._pdf_canvas(path)
    c.setPageSize(_A4)
    scan = ImageReader(_words_picture(["SITE INSTRUCTION 015", "DATED 2024-07-17"]))
    c.drawImage(scan, 480, 700, width=119, height=67.4)
    c.drawImage(scan, 0, 0, width=_A4[0], height=_A4[1])
    c.drawString(40, 20, _STAMP)
    c.showPage()
    c.save()
    raw = path.read_bytes()
    with fitz.open(stream=raw, filetype="pdf") as doc:
        assert len(doc[0].get_images(full=True)) == 1
        assert doc[0].read_contents().count(b" Do") == 2, "not one image drawn twice"
    assert _share(raw) >= ex._SCAN_MIN_IMAGE_SHARE, _share(raw)

    for opt in (ex.ExtractOptions(), _reading()):
        page = ex.extract(path.name, raw, opt).pages[0]
        assert page.kind is PageKind.MIXED, (page.kind, page.notes)
        # The scan's words, matched without its digit 0: with the stamp no
        # longer masked out of the crop (D-58) the engine reads this rendering
        # of "015" as "O15", which says nothing about the placement measured.
        flat = _flat(page.text)
        assert "SITEINSTRUCTION" in flat and "2024-07-17" in flat, page.text


def test_every_placement_of_a_reused_image_is_read(tmp_path):
    """A letterhead over a chart, plus one image object drawn small and then
    large. The crop took the first placement only, so the large copy's words
    were never read on a reading run, under a clean MIXED status.

    FAIL-BEFORE: MIXED with the chart's words and without the large copy's.
    """
    import make_fixtures as mf
    from reportlab.lib.utils import ImageReader

    path = tmp_path / "reused_image.pdf"
    c = mf._pdf_canvas(path)
    c.setPageSize(_A4)
    c.drawString(60, 810, "SYNTHETIC CONTRACTOR LTD MONTHLY REPORT LETTERHEAD")
    c.drawString(60, 792, "Chart and the signed instruction are attached below.")
    c.drawImage(ImageReader(_words_picture(["PROGRESS CHART"], size=(1240, 700))),
                40, 520, width=_A4[0] * 0.9, height=_A4[1] * 0.3)
    rep = ImageReader(_words_picture(["SITE INSTRUCTION 017"]))
    c.drawImage(rep, 480, 470, width=_A4[0] * 0.15, height=_A4[1] * 0.05)
    c.drawImage(rep, 40, 20, width=_A4[0] * 0.8, height=_A4[1] * 0.55)
    c.showPage()
    c.save()

    page = ex.extract(path.name, path.read_bytes(), _reading()).pages[0]
    assert page.kind is PageKind.MIXED, (page.kind, page.notes)
    assert "PROGRESSCHART" in _flat(page.text), page.text
    assert "SITEINSTRUCTION017" in _flat(page.text), page.text


def test_a_scan_stored_as_an_inline_image_is_read(tmp_path):
    """A full-page scan written as a PDF inline image (BI ... ID ... EI) is not
    in the page's image resources, so it measured as no image at all: with a
    typed stamp over the floor it came out NATIVE, unread, with no note, on
    both settings (since A-24's routing; the same page with no stamp is OCR'd
    whole and was always read).

    FAIL-BEFORE: NATIVE, no note, none of the scan's words.
    """
    import fitz  # pymupdf
    import make_fixtures as mf

    path = tmp_path / "inline_scan.pdf"
    c = mf._pdf_canvas(path)
    c.setPageSize(_A4)
    c.drawInlineImage(_words_picture(["SITE INSTRUCTION 303"]), 0, 0, *_A4)
    c.drawString(40, 20, _STAMP)
    c.showPage()
    c.save()
    raw = path.read_bytes()
    with fitz.open(stream=raw, filetype="pdf") as doc:
        assert doc[0].get_images(full=True) == [], "an image XObject exists"
        assert b"BI" in doc[0].read_contents(), "not an inline image"
    assert _share(raw) >= ex._SCAN_MIN_IMAGE_SHARE, _share(raw)

    for opt in (ex.ExtractOptions(), _reading()):
        page = ex.extract(path.name, raw, opt).pages[0]
        assert page.kind is PageKind.MIXED, (page.kind, page.notes)
        assert "SITEINSTRUCTION303" in _flat(page.text), page.text


# ---------------------------------------------------------------------------
# A-24 / D-58: region OCR reads every image region whole. Typed text lying on
# an image may be read twice; nothing under it may be lost.
# ---------------------------------------------------------------------------
#
# D-48's "What shipped" said that reading only the image regions made
# duplication impossible by construction. It did not: an image lying under the
# text layer holds the layer's own glyphs inside its crop, and they were read
# twice. D-51's second fix round painted the text layer's word boxes out of the
# rendering, and its third review found that mask erasing scan content with no
# marker (a diagonal watermark's word boxes; a wrong OCR layer). Alex ruled
# D-58: remove the mask, accept the duplication, disclose it. These tests hold
# both halves: every word of such a page is there at least once, including the
# scan under a wrong text layer, and the page is named in the document note.
#
# The scans are made the way a scanner makes one: a page of real type is
# rendered to pixels, and the pixels become the page's image, so a text layer
# typed at the same points lies exactly over the scanned glyphs.

_BODY = ((60, 140, "SITE INSTRUCTION 303"), (60, 200, "DATED 17 JULY 2024"),
         (60, 260, "EXTEND THE PILING WORKS TO GRID LINE 7"))
_BODY_TOKENS = ("SITEINSTRUCTION", "DATED", "PILINGWORKS")
_LETTER = tuple((60, 330 + 30 * k,
                 f"PARAGRAPH {k:02d} THE CONTRACTOR GIVES NOTICE OF EVENT {k:02d}")
                for k in range(1, 9))


def _typeset_png(lines, size: float = 20, rule: bool = False) -> bytes:
    """An A4 page carrying ``lines`` in real type, rendered at 200 dpi (with a
    ruled border when ``rule``): the pixels a scanner would store."""
    import fitz  # pymupdf

    doc = fitz.open()
    page = doc.new_page(width=_A4[0], height=_A4[1])
    for x, y, text in lines:
        page.insert_text((x, y), text, fontsize=size)
    if rule:
        page.draw_rect(fitz.Rect(30, 30, 565, 812), color=(0, 0, 0), width=3)
    return page.get_pixmap(dpi=200).tobytes("png")


def _image_lines(page) -> str:
    if page.image_line_span is None:
        return ""
    start, count = page.image_line_span
    return "\n".join(page.text.split("\n")[start:start + count])


def _repeat_notes(got) -> list[str]:
    """The document note naming pages whose text may repeat typed words (D-58)."""
    return [n for n in got.notes if ex.IMAGE_TEXT_MAY_REPEAT in n]


def _assert_named_as_repeating(got, pages: str) -> None:
    (note,) = _repeat_notes(got)
    assert note.endswith(f"page(s) {pages}"), note
    # Disclosed, not marked: nothing is missing from the page (D-58).
    assert not ex.has_evidence_marker(note), note


@pytest.mark.parametrize("layout", ["invisible-text-layer", "text-under-the-image"])
def test_a_searchable_scan_keeps_every_word_and_is_named_as_possibly_repeating(layout):
    """A searchable scan: the scan as a full-page image, its OCR text layer
    either invisible over it (render mode 3) or drawn first and hidden under it.
    The layer transcribes the scan, so reading the scan reads the same words a
    second time (D-58 accepts that). Every word is there at least once, and the
    document note names the page as one whose text may repeat.

    FAIL-BEFORE (the mask, 61baefd): NATIVE, the note "read and contained no
    text", and no page named as repeating.
    """
    import fitz  # pymupdf

    doc = fitz.open()
    page = doc.new_page(width=_A4[0], height=_A4[1])
    full = fitz.Rect(0, 0, *_A4)
    if layout == "invisible-text-layer":
        page.insert_image(full, stream=_typeset_png(_BODY))
        for x, y, text in _BODY:
            page.insert_text((x, y), text, fontsize=20, render_mode=3)
    else:
        for x, y, text in _BODY:
            page.insert_text((x, y), text, fontsize=20)
        page.insert_image(full, stream=_typeset_png(_BODY))
    raw = doc.tobytes()
    assert _share(raw) >= ex._SCAN_MIN_IMAGE_SHARE  # D-54 reads it by default
    assert _text_layer_len(raw) >= ex._NATIVE_TEXT_FLOOR

    got = ex.extract("searchable.pdf", raw)
    page = got.pages[0]
    flat = _flat(page.text)
    assert all(flat.count(t) >= 1 for t in _BODY_TOKENS), (page.kind, page.text)
    assert page.kind is PageKind.MIXED, (page.kind, page.text, got.notes)
    assert not any("read and contained no text" in n for n in got.notes), got.notes
    _assert_named_as_repeating(got, "1")


def test_a_searchable_scan_whose_ocr_layer_is_wrong_still_has_its_scan_read():
    """The loss D-58 rules out. An invisible OCR layer at the scan's positions
    whose words are wrong: the scan beneath it is still read, so its words are
    in the page. The mask erased the scan wherever the layer lay and reported
    the page as an image that "contained no text" (D-51 round-3 review).

    FAIL-BEFORE (61baefd): NATIVE, none of the scan's words.
    """
    import fitz  # pymupdf

    doc = fitz.open()
    page = doc.new_page(width=_A4[0], height=_A4[1])
    page.insert_image(fitz.Rect(0, 0, *_A4), stream=_typeset_png(_BODY))
    for k, (x, y, _text) in enumerate(_BODY):
        page.insert_text((x, y), f"LOREM IPSUM DOLOR SIT AMET {k:02d}", fontsize=20,
                         render_mode=3)
    raw = doc.tobytes()
    assert "SITEINSTRUCTION" not in _flat(_text_layer_text(raw))

    got = ex.extract("wrong_layer.pdf", raw)
    page = got.pages[0]
    flat = _flat(page.text)
    assert page.kind is PageKind.MIXED, (page.kind, page.text, got.notes)
    assert all(t in flat for t in _BODY_TOKENS), page.text
    assert "LOREMIPSUM" in flat, page.text  # the text layer is kept as it is
    _assert_named_as_repeating(got, "1")


@pytest.mark.parametrize("letterhead", [True, False],
                         ids=["letterhead-in-the-picture", "blank-stationery"])
def test_a_letter_typed_on_full_page_stationery_loses_nothing_and_is_named(letterhead):
    """A typed letter on stationery exported as one page-sized background
    image. The typed paragraphs are the text layer, and the stationery is read
    whole, so the paragraphs may come back a second time from the image (D-58).
    Every paragraph is there at least once, the letterhead printed on the
    stationery is read, it stays out of the locator (D-49), and the page is
    named as one whose text may repeat.

    FAIL-BEFORE (61baefd): no page named as repeating (and, on blank
    stationery, NATIVE).
    """
    import fitz  # pymupdf

    doc = fitz.open()
    page = doc.new_page(width=_A4[0], height=_A4[1])
    printed = [(60, 70, "NORTHWIND FABRICATORS LTD")] if letterhead else []
    page.insert_image(fitz.Rect(0, 0, *_A4), stream=_typeset_png(printed, size=30, rule=True))
    for x, y, text in _LETTER:
        page.insert_text((x, y), text, fontsize=11)
    raw = doc.tobytes()
    assert _share(raw) >= ex._SCAN_MIN_IMAGE_SHARE

    got = ex.extract("letter.pdf", raw)
    page = got.pages[0]
    flat = _flat(page.text)
    assert all(flat.count(f"PARAGRAPH{k:02d}") >= 1 for k in range(1, 9)), (
        page.kind, page.text)
    assert page.kind is PageKind.MIXED, (page.kind, page.text)
    for k in range(1, 9):
        assert f"PARAGRAPH {k:02d}" in page.locator_text  # the text layer, whole
    if letterhead:
        assert "NORTHWINDFABRICATORS" in flat, page.text
        assert "NORTHWIND" not in _flat(page.locator_text)
    _assert_named_as_repeating(got, "1")


def test_a_stamped_scan_adds_its_body_and_keeps_its_stamp_as_its_locator(tmp_path):
    """A scan whose only text layer is a visible typed endorsement over it: the
    scan's body is read, the endorsement is in the page at least once, D-49
    takes the locator from the text layer alone, and the page is named as one
    whose text may repeat (the endorsement lies on the scan).

    FAIL-BEFORE (61baefd): no page named as repeating.
    """
    import fitz  # pymupdf

    doc = fitz.open()
    page = doc.new_page(width=_A4[0], height=_A4[1])
    page.insert_image(fitz.Rect(0, 0, *_A4), stream=_typeset_png(_BODY))
    page.insert_text((40, 822), _STAMP, fontsize=16)
    raw = doc.tobytes()

    got = ex.extract("stamped.pdf", raw)
    page = got.pages[0]
    flat = _flat(page.text)
    assert page.kind is PageKind.MIXED, (page.kind, page.text)
    # EXACTLY once: the body is in the scan alone, never in the text layer, so
    # D-58's accepted repeat cannot reach it. A picture read twice would
    # (D-51 round-4 review: loosened to "at least once", this let a mutant
    # that reads every region twice pass every test).
    assert {t: flat.count(t) for t in _BODY_TOKENS} == dict.fromkeys(_BODY_TOKENS, 1), page.text
    assert flat.count("ATTORNEYSEYESONLY") >= 1, page.text  # typed, so may repeat
    assert page.locator_text.strip() == _STAMP
    _assert_named_as_repeating(got, "1")


def test_a_picture_beside_the_text_layer_is_not_named_as_repeating():
    """The control. Fixture 15's chart lies beside its letterhead, not under
    it, so no word of the text layer is inside the chart's region: the page is
    MIXED, its letterhead appears once, and no note says its text may repeat. A
    note that fired on every MIXED page would say nothing."""
    got = _pages("15_mixed_content_page.pdf", _reading())
    page = got.pages[0]
    assert page.kind is PageKind.MIXED
    assert page.text.count("SYNTHETIC CONTRACTOR LTD MONTHLY REPORT LETTERHEAD") == 1
    assert _repeat_notes(got) == [], got.notes


def test_only_the_pages_whose_text_lies_on_a_read_image_are_named(tmp_path):
    """Per page, in page order, in one document: a stamped scan (named), a
    letterhead beside a chart (not named), and another stamped scan (named).
    Counts only pages whose image regions were read into the text."""
    import make_fixtures as mf
    from reportlab.lib.utils import ImageReader

    path = tmp_path / "scan_chart_scan.pdf"
    c = mf._pdf_canvas(path)
    for words in ("SITE INSTRUCTION 021", None, "SITE INSTRUCTION 022"):
        if words is None:
            _mixed_page(c, "NOTICE OF DELAY No 21")
            continue
        c.setPageSize(_A4)
        c.drawImage(ImageReader(_words_picture([words])), 0, 0, width=_A4[0], height=_A4[1])
        c.drawString(40, 20, _STAMP)
        c.showPage()
    c.save()

    got = ex.extract(path.name, path.read_bytes(), _reading())
    assert [p.kind for p in got.pages] == [PageKind.MIXED] * 3, got.notes
    (note,) = _repeat_notes(got)
    assert note.startswith("2 page(s) "), note
    _assert_named_as_repeating(got, "1, 3")


def test_an_annotations_words_over_a_scan_are_read():
    """The text layer is the page's content stream, which is what the routing's
    text layer (pypdf) reads. An annotation's words are rendered onto the page
    but are in no text layer, so they reach the page only by being read from
    the rendering, and they do."""
    import fitz  # pymupdf

    doc = fitz.open()
    page = doc.new_page(width=_A4[0], height=_A4[1])
    page.insert_image(fitz.Rect(0, 0, *_A4), stream=_typeset_png(_BODY))
    page.insert_text((40, 822), _STAMP, fontsize=16)
    annot = page.add_freetext_annot(fitz.Rect(300, 420, 560, 460), "RECEIVED 19 JULY 2024",
                                    fontsize=16)
    annot.update()
    raw = doc.tobytes()
    assert "19JULY" not in _flat(_text_layer_text(raw))

    page = ex.extract("annotated.pdf", raw).pages[0]
    # The date alone: the engine reads this rendering of RECEIVED as "RECEVED".
    # Exactly once: an annotation is in no text layer, so only a picture read
    # twice could repeat it (D-51 round-4 review).
    assert _flat(page.text).count("19JULY2024") == 1, page.text


def _text_layer_text(raw: bytes) -> str:
    from pypdf import PdfReader

    return PdfReader(io.BytesIO(raw)).pages[0].extract_text() or ""


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_image_placements_are_boxes_on_the_rendered_page(rotation):
    """The crops are cut from the rendering, so the placements must be in its
    coordinates: each equals PyMuPDF's own rendered-page box for that draw, on
    a rotated page with a CropBox away from the MediaBox origin."""
    import fitz  # pymupdf

    doc = fitz.open()
    page = doc.new_page(width=700, height=900)
    page.insert_image(fitz.Rect(100, 150, 300, 450), stream=_typeset_png(_BODY),
                      keep_proportion=False)
    page.set_cropbox(fitz.Rect(40, 20, 660, 880))
    page.set_rotation(rotation)
    with fitz.open(stream=doc.tobytes(), filetype="pdf") as d:
        pg = d[0]
        (item,) = pg.get_images(full=True)
        want = tuple(fitz.Rect(pg.get_image_bbox(item)).normalize())
        (got,) = ex._image_placements(pg)
        assert tuple(got) == pytest.approx(want, abs=0.5), (got, want)
        assert ex._pdf_image_rects(pg) == [tuple(got)]


# ---------------------------------------------------------------------------
# What a draw is, and every image region not read is marked (D-51 round 3)
# ---------------------------------------------------------------------------

_TYPED = b"BT /F1 12 Tf 40 20 Td (" + _STAMP.encode() + b") Tj ET\n"
"""A text layer over ``_NATIVE_TEXT_FLOOR`` at the foot of the page."""


def _pdf_stream(dict_body: bytes, data: bytes) -> bytes:
    import zlib

    data = zlib.compress(data)
    return (b"<< " + dict_body + b" /Filter /FlateDecode /Length %d >>\nstream\n" % len(data)
            + data + b"\nendstream")


def _raw_pdf(content: bytes, resources: bytes, extra: dict[int, bytes] | None = None) -> bytes:
    """One A4 page with an EXACT content stream, for constructs no PDF library
    here writes on request (soft masks, tiling patterns, deep nesting).
    Objects: 1 catalog, 2 pages, 3 page, 4 contents, 5 Helvetica, 6 up extra."""
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources "
           + resources + b" /Contents 4 0 R >>",
        4: _pdf_stream(b"", content),
        5: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        **(extra or {}),
    }
    out = bytearray(b"%PDF-1.7\n")
    offsets = {}
    for num in sorted(objects):
        offsets[num] = len(out)
        out += b"%d 0 obj\n" % num + objects[num] + b"\nendobj\n"
    xref = len(out)
    top = max(objects)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (top + 1)
    for num in range(1, top + 1):
        out += (b"%010d 00000 n \n" % offsets[num]) if num in offsets else b"0000000000 65535 f \n"
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (top + 1, xref)
    return bytes(out)


def _gray_image(fill: int | None = None, size: int = 16) -> bytes:
    """An image XObject: a checkerboard, or one flat gray."""
    samples = (bytes([fill]) * (size * size) if fill is not None else
               bytes(((x // 2 + y // 2) % 2) * 255 for y in range(size) for x in range(size)))
    return _pdf_stream(b"/Type /XObject /Subtype /Image /Width %d /Height %d "
                       b"/ColorSpace /DeviceGray /BitsPerComponent 8" % (size, size), samples)


_IM0 = b"<< /Font << /F1 5 0 R >> /XObject << /Im0 6 0 R >> >>"


def _page_notes_and_doc_notes(raw: bytes, opt):
    got = ex.extract("probe.pdf", raw, opt)
    return got, got.pages[0]


def test_a_page_whose_image_geometry_cannot_be_interpreted_is_marked():
    """An image drawn inside 2,047 nested graphics states: MuPDF refuses to
    interpret the page (2,046 is its limit), so its image draws cannot be
    measured. The share swallowed the error and answered "no image", and the
    page came out NATIVE with no note on either setting; before the every-draw
    count it had carried ``M_IMAGE_UNREAD`` (D-51 round-3 review). The page is
    marked and named in the document note.

    D-60: a page that cannot be measured is read whole on either setting. This
    one cannot be rendered either (the same interpreter limit), so it keeps the
    unmeasured marker and gains the transient page-failure marker a scan that
    will not rasterize carries; that failure is an OCR attempt, as it is for a
    scan (``test_a_page_ocr_could_not_even_rasterize_counts_as_an_attempt``).

    FAIL-BEFORE (61baefd): NATIVE, no page note, no document note.
    """
    def nested(depth: int) -> bytes:
        return _raw_pdf(b"q " * depth + b"400 0 0 400 100 200 cm /Im0 Do " + b"Q " * depth
                        + b"\n" + _TYPED, _IM0, {6: _gray_image()})

    import fitz  # pymupdf

    with fitz.open(stream=nested(2046), filetype="pdf") as doc:
        assert ex._page_image_share(doc[0]) == (pytest.approx(400 * 400 / (595 * 842)), True)
    raw = nested(2047)
    with fitz.open(stream=raw, filetype="pdf") as doc, pytest.raises(Exception):
        ex._page_image_share(doc[0])

    for opt in (ex.ExtractOptions(), _reading()):
        got, page = _page_notes_and_doc_notes(raw, opt)
        assert ex.has_evidence_marker(" ".join(page.notes)), (page.notes, got.notes)
        assert page.kind is PageKind.NATIVE
        assert page.notes == (
            ex.M_IMAGE_UNMEASURED,
            f"{ex.M_OCR_PAGE} to rasterize or read the page whole (unmeasurable)")
        (note,) = [n for n in got.notes if n.startswith(ex.M_IMAGE_UNREAD)]
        assert note.startswith(ex.M_IMAGE_UNMEASURED) and note.endswith("page(s) 1"), note
        (failed,) = [n for n in got.notes if n.startswith(ex.M_OCR_PAGE)]
        assert failed.endswith("page(s) 1"), failed
        assert ex.ocr_yield([got]) == (1, 0)
    # With OCR off nothing is attempted, and the page is only unmeasured.
    got, page = _page_notes_and_doc_notes(raw, ex.ExtractOptions(ocr_enabled=False))
    assert page.notes == (ex.M_IMAGE_UNMEASURED,)
    assert ex.ocr_yield([got]) == (0, 0)


def test_one_unmeasurable_page_leaves_the_other_pages_measured(tmp_path, monkeypatch):
    """Per page: the second of three pages cannot be measured, and the third, a
    letterhead over a chart, is still measured and skip-noted by the quick pass.
    Every exception the measure can raise reaches the same branch; the review's
    nested graphics states are one, a page MuPDF cannot load is another.

    D-60: page 2 renders, so it is read whole, as a scan is, on the default
    run too, and named with its cause; page 3 is still skipped, because a
    fallback never brings a skipped page back.

    FAIL-BEFORE (61baefd): page 2 silent, NATIVE, no note.
    """
    import make_fixtures as mf

    path = tmp_path / "three_pages.pdf"
    c = mf._pdf_canvas(path)
    mf._text_page(c, ["TRANSMITTAL 2024-07-16",
                      "Attached: two reports with charts, for the record."])
    _mixed_page(c, "NOTICE OF DELAY No 31")
    _mixed_page(c, "NOTICE OF DELAY No 32")
    c.save()

    original = ex._image_placements

    def placements(page):
        if page.number == 1:
            raise RuntimeError("cannot interpret this page")
        return original(page)

    monkeypatch.setattr(ex, "_image_placements", placements)
    got = ex.extract(path.name, path.read_bytes())
    assert [p.kind for p in got.pages] == [PageKind.NATIVE, PageKind.MIXED, PageKind.NATIVE]
    assert [p.notes for p in got.pages] == [
        (), (f"page {ex.IMAGE_READ_WHOLE}: unmeasurable",), (ex.M_IMAGE_SKIPPED,)]
    assert "NOTICEOFDELAYNO31" in _flat(got.pages[1].text), got.pages[1].text
    assert not ex.has_evidence_marker(" ".join(got.pages[1].notes))
    (whole,) = [n for n in got.notes if ex.IMAGE_READ_WHOLE in n]
    assert whole.endswith("page(s) 2 (unmeasurable)"), whole
    assert not [n for n in got.notes if n.startswith(ex.M_IMAGE_UNMEASURED)], got.notes
    (skip,) = [n for n in got.notes if "left unread because" in n]
    assert skip.endswith("page(s) 3"), skip
    # OCR off: nothing is read, so the page is unmeasured and says so.
    off = ex.extract(path.name, path.read_bytes(), ex.ExtractOptions(ocr_enabled=False))
    assert off.pages[1].notes == (ex.M_IMAGE_UNMEASURED,)
    (unmeasured,) = [n for n in off.notes if n.startswith(ex.M_IMAGE_UNMEASURED)]
    assert unmeasured.endswith("page(s) 2"), unmeasured


def test_region_ocr_that_cannot_measure_the_page_again_marks_it(monkeypatch):
    """Region OCR measures the page a second time to cut its crops. When that
    raised, the crop list came back empty and the page was dropped from region
    OCR: NATIVE, no note, although the routing had found its chart. Since D-60
    the page is read whole instead, and named with the cause."""
    calls: dict[int, int] = {}
    original = ex._image_placements

    def placements(page):
        calls[page.number] = calls.get(page.number, 0) + 1
        if calls[page.number] > 1:
            raise RuntimeError("the second reading failed")
        return original(page)

    monkeypatch.setattr(ex, "_image_placements", placements)
    got = _pages("15_mixed_content_page.pdf", _reading())
    page = got.pages[0]
    assert page.kind is PageKind.MIXED, (page.kind, page.notes, got.notes)
    assert "NOTICEOFDELAY" in _flat(_image_lines(page)), page.text
    assert page.notes == (f"page {ex.IMAGE_READ_WHOLE}: unmeasurable",)
    (whole,) = [n for n in got.notes if ex.IMAGE_READ_WHOLE in n]
    assert whole.endswith("page(s) 1 (unmeasurable)"), whole


def _two_pictures_page(path) -> bytes:
    """A text layer at the foot and two separate pictures, each carrying words,
    adding up to about 62% of the page: MIXED, read only by a reading run."""
    _stamped_pdf(path, [[(40, 460, 515, 300), (40, 100, 515, 300)]])
    raw = path.read_bytes()
    assert ex.PHOTO_MIN_IMAGE_AREA_SHARE <= _share(raw) < ex._SCAN_MIN_IMAGE_SHARE
    return raw


def _o0(flat: str) -> str:
    """The fixtures' bitmap font's zero reads as the letter O on a whole-page
    reading, so text is compared with every O taken as a zero."""
    return flat.replace("O", "0")


def _whole_note(got) -> str:
    """The document note naming the pages read whole (D-60)."""
    (note,) = [n for n in got.notes if ex.IMAGE_READ_WHOLE in n]
    assert not ex.has_evidence_marker(note), note  # a cost in time, not a loss
    return note


@pytest.mark.parametrize("pictures, cap, count", [(2, 1, "1 of 2"), (3, 1, "2 of 3")],
                         ids=["2-pictures", "3-pictures"])
def test_image_regions_past_the_region_cap_send_the_page_to_be_read_whole(
        tmp_path, monkeypatch, pictures, cap, count):
    """The cap at one region: the first picture's region is read, the others are
    not. Round 3 marked the page "k of n image region(s)" and the log called
    them a failure to rasterize or read, naming no page, when they were never
    tried (D-51 round-4 review). D-60: the uncovered pictures send the page to
    be read whole, every picture's words are in its text, nothing is marked
    lost, and the page and its cause, with the count, are named. Three
    pictures, because with two a count of one is right by accident (the
    review's X14 mutant).

    FAIL-BEFORE (4105aac): the later pictures' words missing, an
    ``M_IMAGE_UNREAD`` page note.
    """
    rects = [(40, 560, 515, 200), (40, 320, 515, 200), (40, 80, 515, 200)][:pictures]
    path = tmp_path / "pictures.pdf"
    _stamped_pdf(path, [rects])
    raw = path.read_bytes()
    assert ex.PHOTO_MIN_IMAGE_AREA_SHARE <= _share(raw) < ex._SCAN_MIN_IMAGE_SHARE
    monkeypatch.setattr(ex, "_MIXED_MAX_REGIONS", cap)
    got = ex.extract(path.name, raw, _reading())
    page = got.pages[0]
    flat = _o0(_flat(page.text))
    assert all(flat.count(_o0(f"SITEINSTRUCTION0{k:02d}")) == 1 for k in range(pictures)), page.text
    assert page.kind is PageKind.MIXED, (page.kind, page.notes, got.notes)
    assert not ex.has_evidence_marker(" ".join(page.notes)), page.notes
    assert page.notes == (f"page {ex.IMAGE_READ_WHOLE}: cap ({count} image region(s))",)
    assert _whole_note(got).endswith("page(s) 1 (cap)")
    assert not any("could not be rasterized or read" in n for n in got.notes), got.notes
    assert page.locator_text.strip() == _STAMP  # D-49: the text layer alone


def _raise_once(monkeypatch):
    """Make the first OCR call of the process raise, and every later one work."""
    import threading

    lock = threading.Lock()
    seen = []
    original = ex._ocr_array

    def ocr(arr):
        with lock:
            seen.append(1)
            first = len(seen) == 1
        if first:
            raise RuntimeError("the engine failed on this region")
        return original(arr)

    monkeypatch.setattr(ex, "_ocr_array", ocr)
    return seen


def test_one_regions_ocr_raising_reads_the_page_whole_and_marks_it_for_retry(
        tmp_path, monkeypatch):
    """OCR raises on one of the page's two regions and reads the other. Round
    3 filed the lost region under the FINAL ``M_IMAGE_UNREAD``, so the walker
    never re-read it, where the same exception on a scan is retried (D-51
    round-4 review). D-60: the page is read whole, so both pictures are in it,
    and it carries the TRANSIENT page-failure marker, so the walker re-reads
    the document alone and a calm run's reading is the one kept.

    FAIL-BEFORE (4105aac): the second picture's words missing, and a FINAL
    marker only.
    """
    raw = _two_pictures_page(tmp_path / "two_pictures.pdf")
    _raise_once(monkeypatch)
    got = ex.extract("two_pictures.pdf", raw, _reading())
    page = got.pages[0]
    flat = _o0(_flat(page.text))
    assert all(flat.count(_o0(f"SITEINSTRUCTION0{k:02d}")) == 1 for k in range(2)), page.text
    assert page.kind is PageKind.MIXED, (page.kind, page.notes, got.notes)
    assert ex.has_transient_marker(" ".join(page.notes)), page.notes
    assert not ex.has_final_marker(" ".join(page.notes)), page.notes
    assert page.notes == (
        f"page {ex.IMAGE_READ_WHOLE}: ocr-error (1 of 2 image region(s))",
        f"{ex.M_OCR_PAGE} on 1 image region(s); the page was read whole instead")
    assert _whole_note(got).endswith("page(s) 1 (ocr-error)")


def test_a_region_ocr_exception_is_retried_and_the_run_matches_a_calm_one(
        tmp_path, monkeypatch):
    """The Principle-5 claim ``test_load_dependent`` holds for whole-page OCR,
    held for region OCR: one region's OCR raises once, on a chart page and on
    a page of two pictures, and the finished run is the calm run, byte for
    byte (D-51 round-4 review, finding 3).

    FAIL-BEFORE (4105aac): the pages differ from the calm run's.
    """
    from dociq.contracts import RunConfig, to_jsonable
    from dociq.ingest import walker

    src = tmp_path / "src"
    src.mkdir()
    (src / "chart.pdf").write_bytes((FIXTURES / "15_mixed_content_page.pdf").read_bytes())
    (src / "two.pdf").write_bytes(_two_pictures_page(tmp_path / "two_pictures.pdf"))

    def run(out):
        cfg = RunConfig(source_root=str(src), output_root=str(tmp_path / out),
                        skip_images_on_text_pages=False)
        return walker.run(cfg, walker.WalkOptions(resume=False, workers=1))

    calm = run("calm")
    for target in ("chart.pdf", "two.pdf"):
        real = walker.ex.extract
        fired: list[str] = []

        def flaky(filename, raw, opt=None, _t=target, _real=real, _fired=fired):
            if filename == _t and not _fired:
                _fired.append(filename)
                _raise_once(monkeypatch)
                try:
                    return _real(filename, raw, opt)
                finally:
                    monkeypatch.setattr(ex, "_ocr_array", _ORIGINAL_OCR_ARRAY)
            return _real(filename, raw, opt)

        monkeypatch.setattr(walker.ex, "extract", flaky)
        notes = walker.RunNotes()
        cfg = RunConfig(source_root=str(src), output_root=str(tmp_path / f"flaky_{target}"),
                        skip_images_on_text_pages=False)
        flaky_run = walker.run(cfg, walker.WalkOptions(resume=False, workers=1), notes)
        monkeypatch.setattr(walker.ex, "extract", real)
        assert fired, "the injection never fired"
        assert [to_jsonable(d) for d in flaky_run.documents] == \
            [to_jsonable(d) for d in calm.documents], target
        assert any(target in d for d in notes.load_dependent), notes.load_dependent


_ORIGINAL_OCR_ARRAY = ex._ocr_array


# ---------------------------------------------------------------------------
# D-60: region OCR only when every picture is accounted for, by construction
# ---------------------------------------------------------------------------


def test_the_uncovered_area_is_computed_from_draws_and_the_regions_read():
    """The computation D-60 turns on, alone: the area of each draw outside
    every region read, and the causes that follow from it. A draw half inside
    a read region leaves half its area; a region past the cap or under the
    floor leaves all of its draws; within the tolerance nothing is uncovered;
    a draw no region contains is ``uncovered`` whatever the statuses say."""
    draws = [(0, 0, 10, 10), (20, 0, 30, 10), (40, 0, 44, 2)]
    regions = [(0, 0, 10, 10), (20, 0, 30, 10), (40, 0, 44, 2)]
    left = ex._uncovered_draw_area(draws, [(0, 0, 10, 10), (20, 0, 25, 10)])
    assert list(left) == pytest.approx([0.0, 50.0, 8.0])
    assert ex._coverage_causes(draws, regions, [None, None, None], regions, 0.13) == ()
    assert ex._coverage_causes(draws, regions, [None, "cap", "floor"], regions[:1], 0.13) == (
        "cap (1 of 3 image region(s))", "floor (1 of 3 image region(s))")
    assert ex._coverage_causes(draws, regions, [None, "ocr-error", None],
                               [regions[0], regions[2]], 0.13) == (
        "ocr-error (1 of 3 image region(s))",)
    # A draw left out of every region, as a merge that lost one would leave it.
    assert ex._coverage_causes(draws, regions[:2], [None, None], regions[:2], 0.13) == (
        "uncovered",)
    # Within the tolerance: a sliver the size of one pixel is not a picture.
    assert ex._coverage_causes([(0, 0, 10, 10.01)], [(0, 0, 10, 10.01)], [None],
                               [(0, 0, 10, 10)], 0.13) == ()


def _hairline_beside_chart(path) -> bytes:
    """Fixture 15's letterhead and chart, plus a 2-point rule drawn as an image:
    its region is under the 8-pixel floor."""
    import fitz  # pymupdf

    doc = fitz.open(FIXTURES / "15_mixed_content_page.pdf")
    page = doc[0]
    png = _typeset_png([(60, 140, "RULE")])
    page.insert_image(fitz.Rect(40, 380, 555, 382), stream=png, keep_proportion=False)
    raw = doc.tobytes()
    path.write_bytes(raw)
    return raw


def _hairline_page(chart: bool) -> bytes:
    """A typed letter with a 2-point rule stored as an image (its region is
    under the 8-pixel floor), and optionally fixture 15's kind of chart beside
    it. The rule alone is drawn 400 times at one place, as the round-4 review's
    page drew it, so its drawn areas add up to a full-page scan (D-54) and a
    DEFAULT run reads it."""
    content = b"".join(b"BT /F1 11 Tf 60 %d Td (PARAGRAPH %02d THE CONTRACTOR GIVES NOTICE) Tj ET\n"
                       % (760 - 20 * k, k) for k in range(12))
    rule = b"q 515 0 0 2 40 450 cm /Im0 Do Q\n" if chart else b"q 595 0 0 2 0 450 cm /Im0 Do Q\n"
    objects = {6: _gray_image(0)}
    resources = _IM0
    if chart:
        content += rule + b"q 515 0 0 300 40 60 cm /Im1 Do Q\n"
        objects[7] = _image_xobject(_words_picture(["NOTICE OF DELAY No 14"], size=(1240, 722)))
        resources = b"<< /Font << /F1 5 0 R >> /XObject << /Im0 6 0 R /Im1 7 0 R >> >>"
    else:
        content += rule * 400
    return _raw_pdf(content, resources, objects)


def _image_xobject(img) -> bytes:
    """A grayscale image XObject holding a PIL image's pixels."""
    img = img.convert("L")
    return _pdf_stream(b"/Type /XObject /Subtype /Image /Width %d /Height %d "
                       b"/ColorSpace /DeviceGray /BitsPerComponent 8" % img.size, img.tobytes())


def _png(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _halved_regions(monkeypatch):
    """A way of cutting regions that loses part of every picture, standing in
    for a defect nobody has found yet: each region keeps its lower half."""
    original = ex._merge_boxes

    def halved(boxes, page_rect):
        return [(x0, (y0 + y1) / 2, x1, y1) for x0, y0, x1, y1 in original(boxes, page_rect)]

    monkeypatch.setattr(ex, "_merge_boxes", halved)


@pytest.mark.parametrize("case", ["floor-beside-a-chart", "floor-only", "cap",
                                  "unlisted-reason"])
def test_pictures_no_region_read_covers_send_the_page_to_be_read_whole(
        tmp_path, monkeypatch, case):
    """D-60, the derived guard. Each page draws pictures the counter measures
    and no region that was read covers: a rule under the size floor beside a
    chart, that rule alone, pictures past the region cap, and regions cut by a
    stand-in defect that loses half of every picture (a reason the code does
    not list: it is caught because coverage is COMPUTED). Every one is read
    whole, loses no picture's words, carries no evidence marker, and is named
    with its cause. The floor-only page raises no dead-engine alarm: OCR ran on
    it (round 4: no OCR call was made and ``ocr_yield`` said (1, 0)).

    FAIL-BEFORE (4105aac): each page carries ``M_IMAGE_UNREAD`` or, for the
    stand-in defect, loses the upper half of its chart with no note at all.
    """
    opt = _reading()
    want_words: tuple[str, ...] = ()
    if case == "floor-beside-a-chart":
        raw, cause, want_words = _hairline_page(chart=True), "floor", (_o0("NOTICEOFDELAY"),)
    elif case == "floor-only":
        raw, cause, opt = _hairline_page(chart=False), "floor", ex.ExtractOptions()
        assert _share(raw) >= ex._SCAN_MIN_IMAGE_SHARE
    elif case == "cap":
        raw, cause = _two_pictures_page(tmp_path / "two.pdf"), "cap"
        want_words = (_o0("SITEINSTRUCTION000"), _o0("SITEINSTRUCTION001"))
        monkeypatch.setattr(ex, "_MIXED_MAX_REGIONS", 1)
    else:
        raw, cause = (FIXTURES / "15_mixed_content_page.pdf").read_bytes(), "uncovered"
        want_words = (_o0("NOTICEOFDELAY"),)
        _halved_regions(monkeypatch)
    got = ex.extract("probe.pdf", raw, opt)
    page = got.pages[0]
    flat = _o0(_flat(page.text))
    assert all(w in flat for w in want_words), (want_words, page.text)
    assert page.kind is PageKind.MIXED, (page.kind, page.notes, got.notes)
    assert not ex.has_evidence_marker(" ".join(page.notes) + " ".join(got.notes)), got.notes
    (note,) = page.notes
    assert note.startswith(f"page {ex.IMAGE_READ_WHOLE}: {cause}"), note
    assert _whole_note(got).endswith(f"page(s) 1 ({cause})")
    # D-49, unchanged: the whole-page reading is image text, kept out of the
    # locator, which is the text layer alone.
    assert page.image_line_span is not None
    assert not any(w in _o0(_flat(page.locator_text)) for w in want_words), page.locator_text
    assert ex.ocr_yield([got]) == (1, 1)
    assert ex.ocr_yield_warning([got]) is None


_BAND_WORDS = ("ALPHA", "BRAVO", "CHARLIE", "DELTA", "ECHO", "FOXTROT", "GULF", "HOTEL",
               "INDIA", "JULIET", "KILO", "LIMA")


def _banded_scan(bands: int) -> bytes:
    """A scan stored as ``bands`` touching horizontal strips, as some scanners
    and PDF producers store one, with a typed stamp as its only text layer.
    The lines of type are spaced so that band edges cut through some of them."""
    import fitz  # pymupdf
    from PIL import Image

    lines = tuple((60, 90 + 62 * k, f"LINE {w} OF THE SCAN BODY") for k, w in enumerate(_BAND_WORDS))
    full = Image.open(io.BytesIO(_typeset_png(lines, size=22))).convert("L")
    doc = fitz.open()
    page = doc.new_page(width=_A4[0], height=_A4[1])
    for b in range(bands):
        y0, y1 = round(b * full.height / bands), round((b + 1) * full.height / bands)
        buf = io.BytesIO()
        full.crop((0, y0, full.width, y1)).save(buf, "PNG")
        page.insert_image(fitz.Rect(0, _A4[1] * y0 / full.height, _A4[0], _A4[1] * y1 / full.height),
                          stream=buf.getvalue(), keep_proportion=False)
    page.insert_text((40, 822), _STAMP, fontsize=16)
    return doc.tobytes()


@pytest.mark.parametrize("bands", [4, 8])
def test_a_scan_stored_as_touching_bands_loses_no_line_at_a_band_edge(bands):
    """A stamped scan stored as touching horizontal bands. Only overlapping
    draws were merged, so each band was cropped alone, and a line of type a
    band edge cut through was read as two halves or not at all, with no mark,
    while the D-58 note said "nothing is left out" (D-51 round-4 review). The
    bands merge into one region, every line is read exactly once, and the note
    claims nothing it does not know.

    FAIL-BEFORE (4105aac): lines missing (2 of 12 on 4 bands), and the note.
    """
    import fitz  # pymupdf

    raw = _banded_scan(bands)
    with fitz.open(stream=raw, filetype="pdf") as d:
        assert len(ex._image_placements(d[0])) == bands
    got = ex.extract("banded.pdf", raw)
    page = got.pages[0]
    flat = _flat(page.text)
    assert {w: flat.count(f"LINE{w}") for w in _BAND_WORDS} == dict.fromkeys(
        _BAND_WORDS, 1), page.text
    assert page.kind is PageKind.MIXED
    assert page.notes == (), page.notes
    assert page.locator_text.strip() == _STAMP
    assert not any("nothing is left out" in n for n in got.notes), got.notes
    with fitz.open(stream=raw, filetype="pdf") as d:
        assert len(ex._pdf_image_rects(d[0])) == 1


def test_a_caption_set_just_under_a_chart_is_not_named_as_repeating():
    """An 11-point caption 3 points under a chart: MuPDF's word box starts at
    the font's ascender and overlaps the chart's region by under a point, but
    no glyph lies in it, so the caption cannot be read again. Named anyway at
    4105aac (D-51 round-4 review). The caption appears once and the page is not
    named; the precondition, the word box overlapping, is measured first.

    FAIL-BEFORE (4105aac): the page named as repeating.
    """
    import fitz  # pymupdf

    doc = fitz.open()
    page = doc.new_page(width=_A4[0], height=_A4[1])
    page.insert_text((40, 60), "SYNTHETIC CONTRACTOR LTD MONTHLY REPORT LETTERHEAD", fontsize=12)
    chart = fitz.Rect(40, 100, 555, 400)
    page.insert_image(chart, stream=_png(_words_picture(["PROGRESS CHART"], size=(1240, 722))),
                      keep_proportion=False)
    page.insert_text((40, 411.1), "FIGURE 3 PROGRESS AGAINST PLAN", fontsize=11)
    raw = doc.tobytes()
    with fitz.open(stream=raw, filetype="pdf") as d:
        words = [w for w in d[0].get_text("words") if w[4] == "FIGURE"]
        assert words and words[0][1] < chart.y1, words  # the box overlaps the chart
    got = ex.extract("caption.pdf", raw, _reading())
    page = got.pages[0]
    assert page.kind is PageKind.MIXED, (page.kind, got.notes)
    assert "PROGRESSCHART" in _flat(_image_lines(page)), page.text
    assert _flat(page.text).count("FIGURE3") == 1, page.text
    assert _repeat_notes(got) == [], got.notes


def test_text_over_a_picture_that_read_nothing_is_not_named_as_repeating():
    """Invisible text over a blank picture, and a chart elsewhere that reads.
    The page was named because the test ran per page: any region's text plus
    any overlap (D-51 round-4 review). Only a region that yielded text can
    repeat a word, and the blank one yielded none.

    FAIL-BEFORE (4105aac): the page named as repeating.
    """
    import fitz  # pymupdf

    doc = fitz.open()
    page = doc.new_page(width=_A4[0], height=_A4[1])
    page.insert_text((40, 60), "SYNTHETIC CONTRACTOR LTD MONTHLY REPORT LETTERHEAD", fontsize=12)
    page.insert_image(fitz.Rect(40, 100, 555, 300), stream=_typeset_png([], size=20),
                      keep_proportion=False)
    page.insert_text((60, 200), "INVISIBLE WORDS OVER A BLANK PICTURE", fontsize=14,
                     render_mode=3)
    page.insert_image(fitz.Rect(40, 450, 555, 750),
                      stream=_png(_words_picture(["PROGRESS CHART"], size=(1240, 722))),
                      keep_proportion=False)
    raw = doc.tobytes()
    got = ex.extract("blank_picture.pdf", raw, _reading())
    page = got.pages[0]
    assert page.kind is PageKind.MIXED, (page.kind, got.notes)
    assert "PROGRESSCHART" in _flat(page.text), page.text
    assert _repeat_notes(got) == [], got.notes


def _stencil_page(fill: str) -> bytes:
    """A full-page 1-bit stencil mask carrying a scan's words, painted with a
    solid color, a shading or a tiling pattern, and a typed stamp."""
    from PIL import Image

    img = Image.open(io.BytesIO(_typeset_png(_BODY))).convert("1")
    mask = _pdf_stream(b"/Type /XObject /Subtype /Image /Width %d /Height %d /ImageMask true "
                       b"/BitsPerComponent 1" % img.size, img.tobytes())
    stamp = b"BT /F1 12 Tf 40 20 Td (" + _STAMP.encode() + b") Tj ET\n"
    draw = b"595 0 0 842 0 0 cm /Im0 Do Q\n"
    if fill == "solid":
        return _raw_pdf(b"q 0 g " + draw + stamp, _IM0, {6: mask})
    if fill == "shading":
        pattern = (b"<< /PatternType 2 /Shading << /ShadingType 2 /ColorSpace /DeviceGray "
                   b"/Coords [0 0 595 0] /Function << /FunctionType 2 /Domain [0 1] "
                   b"/C0 [0.1] /C1 [0.2] /N 1 >> >> >>")
    else:
        pattern = _pdf_stream(b"/Type /Pattern /PatternType 1 /PaintType 1 /TilingType 1 "
                              b"/BBox [0 0 10 10] /XStep 10 /YStep 10 /Resources << >>",
                              b"0.1 g 0 0 10 10 re f")
    return _raw_pdf(b"q /Pattern cs /P0 scn " + draw + stamp,
                    b"<< /Font << /F1 5 0 R >> /XObject << /Im0 6 0 R >> "
                    b"/Pattern << /P0 7 0 R >> >>", {6: mask, 7: pattern})


@pytest.mark.parametrize("fill", ["solid", "shading", "tiling"])
def test_a_stencil_mask_scan_is_measured_and_read_whatever_fills_it(fill):
    """A scan stored as a stencil mask (1-bit ``ImageMask``) paints its color
    through the image. Filled with a shading or a tiling pattern it reaches
    MuPDF's device as a clip, not as ``fill_image_mask``, so it measured 0 and
    the page came out NATIVE, its words unread, with no note on either setting
    (D-51 round-4 review; a regression against 0a29e30). The solid fill was
    measured but no test held it (the review's X1 mutant).

    FAIL-BEFORE (4105aac): shading and tiling measured 0.0, NATIVE, no words.
    """
    raw = _stencil_page(fill)
    assert _share(raw) == 1.0
    got = ex.extract("stencil.pdf", raw)  # a scan, so read on the default run
    page = got.pages[0]
    flat = _flat(page.text)
    assert page.kind is PageKind.MIXED, (page.kind, page.notes, got.notes)
    assert {t: flat.count(t) for t in _BODY_TOKENS} == dict.fromkeys(_BODY_TOKENS, 1), page.text
    assert page.locator_text.strip() == _STAMP


def test_tier3_never_calls_an_unmeasurable_page_blank():
    """``pdf_spans`` answers "an image of unknown size" for a page whose
    geometry cannot be interpreted, so Tier 3 never calls it a Blank page (the
    review's X4 mutant: "the page has no text and no image" for an image drawn
    inside 2,047 nested graphics states)."""
    raw = _raw_pdf(b"q " * 2047 + b"400 0 0 400 100 200 cm /Im0 Do " + b"Q " * 2047 + b"\n",
                   _IM0, {6: _gray_image()})
    got = ex.extract("deep.pdf", raw, ex.ExtractOptions(ocr_enabled=False))
    spans = ex.pdf_spans(raw, got.pages)
    assert spans and all(s.section != "Blank page" for s in spans), spans
    assert [s.section for s in spans] == ["Image-only page"], spans


def test_boxes_that_touch_merge_and_boxes_apart_do_not():
    """The merge's gap, alone: touching boxes (a band edge) and boxes under
    ``_MERGE_GAP_PT`` apart are one region; boxes further apart stay two."""
    page = (0.0, 0.0, 595.0, 842.0)
    assert ex._merge_boxes([(0, 0, 10, 10), (0, 10, 10, 20)], page) == [(0, 0, 10, 20)]
    g = ex._MERGE_GAP_PT
    assert ex._merge_boxes([(0, 0, 10, 10), (0, 10 + g / 2, 10, 20)], page) == [(0, 0, 10, 20)]
    assert len(ex._merge_boxes([(0, 0, 10, 10), (0, 10 + 2 * g, 10, 20)], page)) == 2


def _page_mupdf_does_not_have(page2: bytes) -> bytes:
    """Two page objects under a /Count of 1: pypdf reads two pages, MuPDF one."""
    return _raw_pdf(_TYPED, _IM0, {
        2: b"<< /Type /Pages /Kids [3 0 R 7 0 R] /Count 1 >>",
        6: _gray_image(),
        7: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources " + _IM0
           + b" /Contents 8 0 R >>",
        8: _pdf_stream(b"", page2),
    })


def test_a_page_mupdf_does_not_have_is_marked_on_either_route():
    """The whole-page route dropped a page that the text layer's reader sees
    and MuPDF does not: EMPTY, no note, a FULL document (D-51 round-4 review,
    C; the geometry route was fixed in round 3). It now carries the page
    failure marker, as a scan that will not rasterize does. The same page with
    a text layer and a picture is unmeasured and cannot be read whole either.

    FAIL-BEFORE (4105aac): page 2 EMPTY with no note.
    """
    scan = _page_mupdf_does_not_have(b"q 595 0 0 842 0 0 cm /Im0 Do Q\n")
    for opt in (ex.ExtractOptions(), _reading()):
        got = ex.extract("count.pdf", scan, opt)
        assert len(got.pages) == 2
        assert got.pages[1].notes == (f"{ex.M_OCR_PAGE} to rasterize or read",), got.pages[1]
        assert ex.has_transient_marker(" ".join(got.pages[1].notes))

    typed = _page_mupdf_does_not_have(b"q 595 0 0 421 0 0 cm /Im0 Do Q\n" + _TYPED)
    got = ex.extract("count.pdf", typed, _reading())
    assert got.pages[1].notes == (
        ex.M_IMAGE_UNMEASURED,
        f"{ex.M_OCR_PAGE} to rasterize or read the page whole (unmeasurable)")


def test_many_small_pictures_that_add_up_are_skip_noted_and_read():
    """36 pictures, each under 1% of the page, in six overlapping rows beside a
    short text layer: together about a third of the page, under the 90% scan
    share. The quick pass leaves them unread and says so on the page and in the
    document; a reading run reads them. A measure that dropped small draws (the
    review's R7) left the page NATIVE with no note on both settings.
    """
    import fitz  # pymupdf
    from PIL import Image, ImageDraw

    doc = fitz.open()
    page = doc.new_page(width=_A4[0], height=_A4[1])
    page.insert_text((40, 40), "PHOTO RECORD SHEET FOR THE SYNTHETIC SITE VISIT", fontsize=12)
    side = 68.0
    for k in range(36):
        tile = Image.new("L", (60, 20), 255)
        ImageDraw.Draw(tile).text((4, 4), f"T{k:02d}", fill=0)
        img = Image.new("L", (400, 400), 255)
        img.paste(tile.resize((360, 120), Image.LANCZOS), (20, 140))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        x, y = 40 + (k % 6) * (side - 1), 80 + (k // 6) * (side + 20)
        page.insert_image(fitz.Rect(x, y, x + side, y + side), stream=buf.getvalue())
    raw = doc.tobytes()
    with fitz.open(stream=raw, filetype="pdf") as d:
        draws = ex._image_placements(d[0])
        assert len(draws) == 36
        area = _A4[0] * _A4[1]
        assert all((b[2] - b[0]) * (b[3] - b[1]) < 0.01 * area for b in draws)
        assert len(ex._pdf_image_rects(d[0])) == 6
    share = _share(raw)
    assert ex.PHOTO_MIN_IMAGE_AREA_SHARE <= share < ex._SCAN_MIN_IMAGE_SHARE, share

    quick = ex.extract("sheet.pdf", raw)
    assert quick.pages[0].kind is PageKind.NATIVE
    assert quick.pages[0].notes == (ex.M_IMAGE_SKIPPED,), quick.notes
    (skip,) = [n for n in quick.notes if "left unread because" in n]
    assert skip.endswith("page(s) 1"), skip

    reading = ex.extract("sheet.pdf", raw, _reading()).pages[0]
    assert reading.kind is PageKind.MIXED, (reading.kind, reading.notes)
    assert any(f"T{k:02d}" in _flat(reading.text) for k in range(36)), reading.text


def test_every_draw_counts_even_the_same_image_drawn_twice_at_one_place():
    """What "every draw" means for identical overlapping placements: each one
    counts. The share is a SUM and an upper bound, so the same picture painted
    twice at one place adds its area twice -- the error that makes is to read a
    page, never to leave one unread without a note -- and the notes that quote
    it say the drawn areas add up, not that the page shows them. Two different
    images at one box (a background and a foreground layer) add up the same
    way. The review's R6 (identical boxes counted once) halved both.
    """
    import fitz  # pymupdf

    box = b"300 0 0 280 100 300 cm"
    twice = _raw_pdf(b"q " + box + b" /Im0 Do Q q " + box + b" /Im0 Do Q\n" + _TYPED,
                     _IM0, {6: _gray_image()})
    once = _raw_pdf(b"q " + box + b" /Im0 Do Q\n" + _TYPED, _IM0, {6: _gray_image()})
    with fitz.open(stream=twice, filetype="pdf") as d:
        a, b = ex._image_placements(d[0])
        assert a == b
    single = 300 * 280 / (_A4[0] * _A4[1])
    assert single < ex.PHOTO_MIN_IMAGE_AREA_SHARE <= 2 * single
    assert _share(once) == pytest.approx(single)
    assert _share(twice) == pytest.approx(2 * single)

    page = ex.extract("twice.pdf", twice).pages[0]
    assert page.notes == (ex.M_IMAGE_SKIPPED,)
    assert ex.extract("once.pdf", once).pages[0].notes == ()

    layers = _raw_pdf(b"q 290 0 0 800 150 21 cm /Im0 Do Q q 290 0 0 800 150 21 cm /Im1 Do Q\n"
                      + _TYPED,
                      b"<< /Font << /F1 5 0 R >> /XObject << /Im0 6 0 R /Im1 7 0 R >> >>",
                      {6: _gray_image(230), 7: _gray_image()})
    assert _share(layers) == pytest.approx(2 * 290 * 800 / (_A4[0] * _A4[1]))
    assert _share(layers) >= ex._SCAN_MIN_IMAGE_SHARE


_SOFT_MASK_OBJECTS = {
    6: _gray_image(),
    7: _pdf_stream(b"/Type /XObject /Subtype /Form /BBox [0 0 595 842] "
                   b"/Group << /S /Transparency /CS /DeviceGray >> "
                   b"/Resources << /XObject << /M 6 0 R >> >>",
                   b"q 595 0 0 842 0 0 cm /M Do Q"),
    8: b"<< /Type /ExtGState /SMask << /Type /Mask /S /Luminosity /G 7 0 R >> >>",
}


def test_an_image_used_only_as_a_soft_mask_is_not_a_drawn_image():
    """A typed letter over a background faded by a luminosity soft mask whose
    group draws a page-sized image. The image shapes the background's
    transparency and paints nothing, but the text device reported it, so the
    letter measured as a full-page scan: read on the quick pass, noted as an
    image that "contained no text" (D-51 round-3 review). An image carrying its
    own transparency mask still counts, once.

    FAIL-BEFORE (61baefd): share 1.0, and the page routed as a scan.
    """
    body = b"".join(b"BT /F1 11 Tf 60 %d Td (PARAGRAPH %02d THE CONTRACTOR GIVES NOTICE) Tj ET\n"
                    % (760 - 20 * k, k) for k in range(20))
    raw = _raw_pdf(b"q /GS0 gs 0.9 0.9 1 rg 0 0 595 842 re f Q\n" + body,
                   b"<< /Font << /F1 5 0 R >> /ExtGState << /GS0 8 0 R >> >>",
                   _SOFT_MASK_OBJECTS)
    assert _share(raw) == 0.0
    import fitz  # pymupdf

    with fitz.open(stream=raw, filetype="pdf") as d:
        assert ex._image_placements(d[0]) == []
        assert ex._page_image_share(d[0]) == (0.0, False)
    for opt in (ex.ExtractOptions(), _reading()):
        got = ex.extract("letter.pdf", raw, opt)
        assert got.pages[0].kind is PageKind.NATIVE
        assert got.pages[0].notes == () and got.notes == (), got.notes

    photo = _pdf_stream(b"/Type /XObject /Subtype /Image /Width 16 /Height 16 "
                        b"/ColorSpace /DeviceGray /BitsPerComponent 8 /SMask 7 0 R",
                        bytes(range(256)))
    with_alpha = _raw_pdf(b"q 595 0 0 421 0 421 cm /Im0 Do Q\n" + _TYPED, _IM0,
                          {6: photo, 7: _gray_image()})
    assert _share(with_alpha) == pytest.approx(0.5)


def test_an_image_painted_through_a_tiling_pattern_counts_the_area_it_fills():
    """A pattern whose 20-point cell draws an image fills 515 x 400 points. The
    text device reported the cell once, so the page measured as a sliver and
    went unread with no note on either setting. The area the pattern fills is
    the image content the page shows.

    FAIL-BEFORE (61baefd): one 20 x 20 box, NATIVE, no note.
    """
    import fitz  # pymupdf

    pattern = _pdf_stream(b"/Type /Pattern /PatternType 1 /PaintType 1 /TilingType 1 "
                          b"/BBox [0 0 20 20] /XStep 20 /YStep 20 "
                          b"/Resources << /XObject << /Im0 6 0 R >> >>",
                          b"q 20 0 0 20 0 0 cm /Im0 Do Q")
    raw = _raw_pdf(b"q /Pattern cs /P0 scn 40 100 515 400 re f Q\n" + _TYPED,
                   b"<< /Font << /F1 5 0 R >> /Pattern << /P0 7 0 R >> >>",
                   {6: _gray_image(), 7: pattern})
    with fitz.open(stream=raw, filetype="pdf") as d:
        (box,) = ex._image_placements(d[0])
    assert box == pytest.approx((40, 842 - 500, 555, 842 - 100), abs=0.5)
    assert _share(raw) == pytest.approx(515 * 400 / (_A4[0] * _A4[1]), abs=0.002)

    got = ex.extract("pattern.pdf", raw)
    assert got.pages[0].notes == (ex.M_IMAGE_SKIPPED,), got.notes


def test_an_image_drawn_off_the_page_is_not_measured_and_one_half_off_is_clipped():
    """Unclipped, an image placed entirely below the page measured as full
    coverage, and a reading run then reported image content that "could not be
    rasterized or read" -- a loss that did not happen. Half off, it measured as
    the whole page.

    FAIL-BEFORE (61baefd): share 1.0 for both, and the loss note on the first.
    """
    import fitz  # pymupdf

    off = _raw_pdf(b"q 595 0 0 842 0 -2000 cm /Im0 Do Q\n" + _TYPED, _IM0, {6: _gray_image()})
    half = _raw_pdf(b"q 595 0 0 842 297 0 cm /Im0 Do Q\n" + _TYPED, _IM0, {6: _gray_image()})
    with fitz.open(stream=off, filetype="pdf") as d:
        assert ex._image_placements(d[0]) == []
    with fitz.open(stream=half, filetype="pdf") as d:
        (box,) = ex._image_placements(d[0])
    assert box == pytest.approx((297, 0, 595, 842))
    assert _share(half) == pytest.approx(298 / 595)

    got = ex.extract("off.pdf", off, _reading())
    assert got.pages[0].kind is PageKind.NATIVE
    assert got.pages[0].notes == () and got.notes == (), got.notes


def _pairwise_union(boxes, gap: float | None = None):
    """61baefd's merge, in effect: the reference the sweep must equal. Boxes
    within ``gap`` (by default the product's ``_MERGE_GAP_PT``) merge as if
    they overlapped (D-51 round 4); 61baefd merged only overlapping ones."""
    g = ex._MERGE_GAP_PT if gap is None else gap
    boxes = [b for b in boxes if b[2] > b[0] and b[3] > b[1]]
    merged = True
    while merged and len(boxes) > 1:
        merged = False
        out = []
        for b in boxes:
            for i, a in enumerate(out):
                if a[0] < b[2] + g and b[0] < a[2] + g and a[1] < b[3] + g and b[1] < a[3] + g:
                    out[i] = (min(a[0], b[0]), min(a[1], b[1]),
                              max(a[2], b[2]), max(a[3], b[3]))
                    merged = True
                    break
            else:
                out.append(b)
        boxes = out
    boxes.sort(key=lambda b: (b[1], b[0], b[3], b[2]))
    return boxes


def test_merging_image_boxes_gives_the_pairwise_union_on_random_layouts():
    """The merge's result does not depend on the order boxes are merged in, so
    the sweep that replaced the quadratic loop must give exactly its regions:
    random layouts of every density, repeated and zero-area boxes included."""
    import random

    rng = random.Random(20260914)
    for _ in range(600):
        boxes = []
        for _ in range(rng.randint(0, 50)):
            x, y = rng.uniform(0, 595), rng.uniform(0, 842)
            size = rng.choice([4, 30, 150, 400])
            w = 0.0 if rng.random() < 0.05 else rng.uniform(0.5, size)
            boxes.append((x, y, x + w, y + rng.uniform(0.5, size)))
        if boxes and rng.random() < 0.3:
            boxes += rng.sample(boxes, min(5, len(boxes)))
        assert ex._merge_boxes(boxes, (0, 0, 595, 842)) == _pairwise_union(boxes)


def test_merging_one_image_drawn_14400_times_is_bounded():
    """The review's page: one 16-pixel image drawn on a 120 x 120 grid with
    1-point gaps. The pairwise merge took 16 s on it (D-51 round-3 review). The
    bound is generous, for a loaded machine; every draw still lies in a region.

    FAIL-BEFORE (61baefd): over the bound.
    """
    import time

    import fitz  # pymupdf

    n, w, h = 120, 595.0 / 120, 842.0 / 120
    content = b"".join(b"q %.3f 0 0 %.3f %.3f %.3f cm /Im0 Do Q\n"
                       % (w - 1, h - 1, c * w, r * h) for r in range(n) for c in range(n))
    raw = _raw_pdf(content + _TYPED, _IM0, {6: _gray_image()})
    with fitz.open(stream=raw, filetype="pdf") as d:
        start = time.perf_counter()
        regions = ex._pdf_image_rects(d[0])
        seconds = time.perf_counter() - start
        draws = ex._image_placements(d[0])
    assert len(draws) == n * n
    assert seconds < 5.0, seconds
    for x0, y0, x1, y1 in draws:
        assert any(r[0] <= x0 and r[1] <= y0 and x1 <= r[2] and y1 <= r[3] for r in regions)
    # One region: the grid's cells touch once widened, and touching regions
    # merge. Without that the page broke into 256 regions and 232 went past the
    # cap (the review's X6 mutant, which no test caught).
    assert len(regions) == 1, len(regions)


def test_past_the_exact_limit_boxes_are_merged_on_the_coarse_grid():
    """Above ``_MERGE_EXACT_MAX_DRAWS`` distinct boxes the merge widens each to
    the coarse grid first, which is what bounds its work whatever the layout.
    Every box still lies inside a region, and every region's edges are on the
    grid (a hair past the far edge)."""
    page = (0.0, 0.0, 595.0, 842.0)
    cw, ch = 595.0 / ex._MERGE_COARSE_CELLS, 842.0 / ex._MERGE_COARSE_CELLS
    n = ex._MERGE_EXACT_MAX_DRAWS + 1
    boxes = [(0.5 + (k % 200) * 2.9, 0.5 + (k // 200) * 70.0,
              0.5 + (k % 200) * 2.9 + 1.0, 0.5 + (k // 200) * 70.0 + 1.0) for k in range(n)]
    regions = ex._merge_boxes(boxes, page)
    assert 0 < len(regions) <= ex._MERGE_COARSE_CELLS ** 2
    for b in boxes:
        assert any(r[0] <= b[0] and r[1] <= b[1] and b[2] <= r[2] and b[3] <= r[3]
                   for r in regions), b
    for r in regions:
        assert r[0] / cw == pytest.approx(round(r[0] / cw), abs=1e-6), r
        assert r[1] / ch == pytest.approx(round(r[1] / ch), abs=1e-6), r
    assert ex._merge_boxes(boxes[:-1], page) == _pairwise_union(boxes[:-1])


def test_empty_page_is_still_a_page():
    got = _pages("04_empty_page.pdf")
    assert [p.page_no for p in got.pages] == [1, 2, 3]
    assert got.pages[1].kind is PageKind.EMPTY
    assert got.pages[1].text == ""


def test_ocr_disabled_leaves_the_scanned_pages_empty_and_says_so():
    got = _pages("02_scanned_instruction.pdf", ex.ExtractOptions(ocr_enabled=False))
    # Page 1 still carries the deterministic [PHOTO] block — that is EXIF, not
    # OCR — and every other page has nothing left to give.
    assert [p.kind for p in got.pages] == [PageKind.PHOTO, PageKind.EMPTY]
    assert any("OCR disabled" in n for n in got.notes)


def test_docx_is_one_synthetic_page_with_the_approximation_disclosed():
    got = _pages("05_letter.docx")
    assert len(got.pages) == 1 and got.pages[0].kind is PageKind.SYNTHETIC
    assert any("no page boundaries" in n for n in got.notes)


def test_xlsx_is_one_page_per_worksheet():
    got = _pages("06_register.xlsx")
    assert len(got.pages) == 2
    assert got.pages[0].text.startswith("[sheet: Register]")
    assert got.pages[1].text == "[sheet: Empty]"


def test_csv_header_and_rows():
    got = _pages("07_ncr_log.csv")
    assert "[header: Ref | Description | Date]" in got.pages[0].text


def test_txt_normalization_reaches_the_record():
    got = _pages("08_daily_log.txt")
    text = got.pages[0].text
    assert "\r" not in text and "\u200b" not in text and "\u00a0" not in text
    assert "\n\n\n" not in text


def test_tier2_is_never_extracted():
    got = _pages("13_legacy.doc")
    assert got.status is ProcessingStatus.UNSUPPORTED
    assert "Save-As" in (got.error or "")
    assert got.pages == ()


def test_eml_headers_and_body_with_an_iso_date_token():
    got = _pages("09_notice.eml")
    text = got.pages[0].text
    assert text.startswith("From: engineer@example.com")
    assert "Subject: Notice of Delay" in text
    assert "(2024-07-16)" in text          # the ISO token the dater anchors on
    assert "clause 20.1" in text
    assert got.pages[0].kind is PageKind.SYNTHETIC


def _eml_with_attachment(body: str = "See attached.",
                        attach_name: str = "report.txt",
                        attach_bytes: bytes = b"attachment body text") -> bytes:
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["From"] = "engineer@example.com"
    msg["To"] = "pm@example.com"
    msg["Subject"] = "See attached"
    msg.set_content(body)
    msg.add_attachment(attach_bytes, maintype="application",
                       subtype="octet-stream", filename=attach_name)
    return msg.as_bytes()


def test_eml_attachments_are_enumerated_as_child_members():
    """§3: MSG/EML attachments are child documents linked to the parent
    message — a Tier-1 requirement. Before this was added, ``_extract_eml``
    read only headers and body; nothing in the extractor ever looked at
    ``iter_attachments()``, so every attachment on every email vanished with
    no record and no note anywhere in the pipeline."""
    raw = _eml_with_attachment()
    exp = ex.expand_eml_attachments(raw)
    assert [m.name for m in exp.members] == ["report.txt"]
    assert exp.members[0].raw == b"attachment body text"


def test_eml_with_no_attachments_yields_no_members():
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["From"] = "a@example.com"
    msg.set_content("no attachments here")
    exp = ex.expand_eml_attachments(msg.as_bytes())
    assert exp.members == ()


def test_eml_zip_attachment_is_flattened_not_dropped():
    """A zip attached to an email must not silently vanish either — it gets
    the same one-level flatten a zip-inside-a-zip already gets."""
    zip_bytes = _zip_of([("inner.txt", b"inner content")])
    raw = _eml_with_attachment(attach_name="production.zip",
                               attach_bytes=zip_bytes)
    exp = ex.expand_eml_attachments(raw)
    assert [m.name for m in exp.members] == ["production.zip/inner.txt"]
    assert exp.members[0].raw == b"inner content"


# ---------------------------------------------------------------------------
# Codex review #1, B-3: an EML failure must never remove evidence in silence
# ---------------------------------------------------------------------------


def test_eml_body_failure_discloses_and_marks_for_retry(monkeypatch):
    """The body walk used to be a broad ``except`` that set ``body = ""``.

    A supported email whose body would not decode therefore came back with its
    headers, no body, no note, no marker and a FULL status — the run said
    nothing at all. Principle 1 forbids that, and because no transient marker
    was emitted the walker's serial-retry pass never even looked at the file.
    """
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["From"] = "engineer@example.com"
    msg["Subject"] = "Notice"
    msg.set_content("the body that must not vanish in silence")
    raw = msg.as_bytes()

    import email as _email

    real = _email.message_from_bytes

    def _boom(data, *a, **kw):
        parsed = real(data, *a, **kw)

        class _Wrapper:
            def __getattr__(self, name):
                return getattr(parsed, name)

            def get_body(self, *_a, **_kw):
                raise RuntimeError("charset table unavailable")

        return _Wrapper()

    monkeypatch.setattr(_email, "message_from_bytes", _boom)
    got = ex.extract("notice.eml", raw)

    joined = " ".join(got.notes)
    assert ex.has_transient_marker(joined), (
        "the lost body carries no transient marker, so the walker's serial "
        "retry never sees it: " + repr(got.notes))
    assert ex.M_EML_BODY in joined, (
        "the lost body was not disclosed at all: " + repr(got.notes))
    assert "the body that must not vanish" not in got.pages[0].text


def test_eml_attachment_enumeration_failure_marks_for_retry(monkeypatch):
    """``expand_eml_attachments`` caught a parse failure and returned a bare
    ``ZipExpansion()`` — zero attachments, no note, no marker. The parent was
    then emitted as a clean message with no attachments at all."""
    import email as _email

    def _boom(data, *a, **kw):
        raise RuntimeError("message header table unavailable")

    monkeypatch.setattr(_email, "message_from_bytes", _boom)
    exp = ex.expand_eml_attachments(_eml_with_attachment())

    assert exp.members == ()
    joined = " ".join(exp.notes)
    assert exp.notes, "the attachments vanished with no note at all"
    assert ex.has_transient_marker(joined), (
        "attachment enumeration does not participate in the serial retry: "
        + repr(exp.notes))


def test_eml_undecodable_attachment_payload_is_marked_not_just_mentioned():
    """A part with no decodable payload was mentioned in prose but carried no
    marker of any kind, so nothing downstream could find it mechanically."""
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["From"] = "a@example.com"
    msg["Subject"] = "s"
    msg.set_content("body")
    msg.add_attachment(b"x", maintype="application", subtype="octet-stream",
                       filename="a.bin")
    raw = msg.as_bytes()

    import email as _email

    real = _email.message_from_bytes

    def _no_payload(data, *a, **kw):
        parsed = real(data, *a, **kw)

        class _Part:
            def __init__(self, inner):
                self._inner = inner

            def get_filename(self):
                return self._inner.get_filename()

            def get_payload(self, *_a, **_kw):
                return None

        class _Wrapper:
            def __getattr__(self, name):
                return getattr(parsed, name)

            def iter_attachments(self):
                return [_Part(p) for p in parsed.iter_attachments()]

        return _Wrapper()

    import pytest as _pytest
    mp = _pytest.MonkeyPatch()
    mp.setattr(_email, "message_from_bytes", _no_payload)
    try:
        exp = ex.expand_eml_attachments(raw)
    finally:
        mp.undo()

    assert exp.members == ()
    joined = " ".join(exp.notes)
    assert exp.notes, "the skipped attachment was not disclosed at all"
    assert ex.has_evidence_marker(joined), (
        "a skipped attachment payload carries no marker: " + repr(exp.notes))


def test_exif_probe_failure_does_not_silently_erase_the_camera_date():
    """The sibling class Codex named: broad catches inside the EXIF read

    suppressed the camera date and the GPS fix with no note anywhere. The
    ``[PHOTO]`` block is the only place a site photo's evidence reaches text,
    so losing it in silence is the same Principle-1 defect as losing an email
    body."""
    meta, notes = ex.exif_from_image_bytes(b"not an image at all")
    assert meta == {}
    assert notes, "an EXIF read that recovered nothing said nothing"
    assert ex.has_evidence_marker(" ".join(notes))


def test_every_degradation_marker_is_classified_exactly_once():
    """The class assertion, not a spot check.

    A new degradation path that invents a marker constant and forgets to put
    it in a list is invisible to the retry AND to the accounting tally — which
    is precisely how the B-3 paths went unnoticed. This makes forgetting fail
    a test rather than fail a matter.
    """
    declared = {name: value for name, value in vars(ex).items()
                if name.startswith("M_") and isinstance(value, str)}
    assert declared, "no marker constants found — the check would be vacuous"

    both = set(ex.TRANSIENT_MARKERS) & set(ex.FINAL_MARKERS)
    assert not both, f"markers in both lists: {sorted(both)}"

    listed = set(ex.TRANSIENT_MARKERS) | set(ex.FINAL_MARKERS)
    unclassified = sorted(n for n, v in declared.items() if v not in listed)
    assert not unclassified, (
        "marker constant(s) in neither TRANSIENT_MARKERS nor FINAL_MARKERS, so "
        "nothing downstream can find the gap they describe: " + str(unclassified))

    for value in listed:
        assert ex.has_evidence_marker(f"head {value} tail")
    assert not ex.has_evidence_marker("XLSX has no page boundaries")
    assert not ex.has_evidence_marker(None)


def test_an_eml_attachment_gap_reaches_the_walkers_retry_registry():
    """B-3's operative claim: the marker is what makes the serial retry look.

    Asserted through the walker's own trigger function rather than by reading
    the note, because the note wording is not what the retry keys on.
    """
    from dociq.contracts import DocumentRecord, ProcessingStatus
    from dociq.ingest import walker

    exp_note = (f"{ex.M_ATTACH_ENUM}: the message envelope would not parse "
                "(boom), so NO attachment of this email was brought in")
    doc = DocumentRecord(doc_id="", rel_path="a/mail.eml", filename="mail.eml",
                         sha256="0" * 64, size_bytes=10, ext=".eml",
                         status=ProcessingStatus.FULL, notes=(exp_note,))
    assert walker._degradations([doc]), (
        "the attachment-enumeration failure is not a retry target")


def test_accounting_counts_evidence_gaps_and_stays_quiet_when_there_are_none():
    """B-3: a final failure must stay auditable in the parent record AND in
    accounting. A note buried in one document of nine thousand is not."""
    from dociq.contracts import (DocumentRecord, PageKind, ProcessingStatus,
                                 RunConfig, RunResult, PageRecord)
    from dociq.verify import accounting

    clean = DocumentRecord(
        doc_id="", rel_path="a.txt", filename="a.txt", sha256="1" * 64,
        size_bytes=1, ext=".txt",
        pages=(PageRecord(page_no=1, text="x", kind=PageKind.SYNTHETIC),))
    config = RunConfig(source_root="s", output_root="o")

    ok = accounting.check(RunResult(config=config, documents=(clean,)))
    assert ok.documents_degraded == 0 and ok.documents_evidence_lost == 0
    assert ok.evidence_line == ""
    assert "EVIDENCE GAPS" not in ok.render()

    degraded = DocumentRecord(
        doc_id="", rel_path="b.eml", filename="b.eml", sha256="2" * 64,
        size_bytes=1, ext=".eml", status=ProcessingStatus.FULL,
        pages=(PageRecord(page_no=1, text="x", kind=PageKind.SYNTHETIC),),
        notes=(f"{ex.M_EML_BODY}: could not decode",))
    lost = DocumentRecord(
        doc_id="", rel_path="c.msg", filename="c.msg", sha256="3" * 64,
        size_bytes=1, ext=".msg", status=ProcessingStatus.FULL,
        pages=(PageRecord(page_no=1, text="x", kind=PageKind.SYNTHETIC),),
        notes=(f"{ex.M_ATTACH_SKIPPED}: embedded message",))
    rep = accounting.check(
        RunResult(config=config, documents=(clean, degraded, lost)))
    assert rep.documents_degraded == 1
    assert rep.documents_evidence_lost == 1
    assert rep.ok, "a disclosed gap is Principle 1 working, not a discrepancy"
    assert "EVIDENCE GAPS" in rep.render()


def test_unknown_extension_is_tier2_not_an_error():
    got = ex.extract("survey.xyz", b"whatever")
    assert got.status is ProcessingStatus.UNSUPPORTED
    assert got.error == ex.UNKNOWN_HINT


def test_misnamed_pdf_is_recovered_and_the_mismatch_disclosed():
    path = FIXTURES / "attachments" / "10_misnamed.docx"
    got = ex.extract(path.name, path.read_bytes())
    assert got.status is not ProcessingStatus.FAILED
    assert any("content is PDF" in n for n in got.notes)


def test_empty_file_fails_with_a_message():
    got = ex.extract("x.pdf", b"")
    assert got.status is ProcessingStatus.FAILED and got.error == "empty file"


def test_unreadable_pdf_fails_without_raising():
    got = ex.extract("x.pdf", b"not a pdf at all")
    assert got.status is ProcessingStatus.FAILED and got.error


def test_extract_refuses_zip_and_says_where_to_go():
    with pytest.raises(Exception, match="expand_zip"):
        ex.extract("a.zip", b"PK\x03\x04")


# -- ZIP guards -------------------------------------------------------------


def _zip_of(members: list[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, blob in members:
            zf.writestr(zipfile.ZipInfo(name, date_time=(2024, 7, 16, 0, 0, 0)),
                        blob)
    return buf.getvalue()


def test_zip_members_keep_archive_order():
    raw = _zip_of([("b.txt", b"B"), ("a.txt", b"A"), ("c.txt", b"C")])
    exp = ex.expand_zip(raw)
    assert [m.name for m in exp.members] == ["b.txt", "a.txt", "c.txt"]
    assert [m.order for m in exp.members] == [0, 1, 2]


def test_nested_zip_flattens_with_a_qualified_name():
    inner = _zip_of([("deep.txt", b"D")])
    raw = _zip_of([("inner.zip", inner)])
    exp = ex.expand_zip(raw)
    assert [m.name for m in exp.members] == ["inner.zip/deep.txt"]


def test_zip_depth_guard_discloses_rather_than_silently_stopping(monkeypatch):
    monkeypatch.setattr(ex, "_ZIP_MAX_DEPTH", 0)
    inner = _zip_of([("deep.txt", b"D")])
    exp = ex.expand_zip(_zip_of([("inner.zip", inner)]))
    assert exp.members == ()
    assert any("nesting deeper" in n for n in exp.notes)


def test_zip_member_cap_discloses_what_was_dropped(monkeypatch):
    monkeypatch.setattr(ex, "_ZIP_MAX_MEMBERS", 2)
    exp = ex.expand_zip(_zip_of([(f"{i}.txt", b"x") for i in range(5)]))
    assert len(exp.members) == 2
    assert any("truncated at 2 members" in n for n in exp.notes)


def test_zip_size_cap_names_the_member_it_stopped_at(monkeypatch):
    monkeypatch.setattr(ex, "_ZIP_MAX_MB", 0)
    exp = ex.expand_zip(_zip_of([("big.txt", b"x" * 4096)]))
    assert exp.members == ()
    assert any("big.txt" in n for n in exp.notes)


def test_corrupt_zip_raises_an_actionable_error():
    with pytest.raises(Exception, match="Could not read ZIP"):
        ex.expand_zip(b"PK\x03\x04garbage")


# -- the no-network and no-cache guarantees ---------------------------------


def test_the_network_call_is_gone_from_the_vendored_module():
    """The docstring names the removed call, so grep alone cannot prove this:
    parse the module and assert no import and no invocation of it survives."""
    import ast

    tree = ast.parse((REPO_ROOT / "src" / "dociq" / "ingest" / "extract.py")
                     .read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = getattr(fn, "id", None) or getattr(fn, "attr", None)
            assert name != "enable_os_trust"
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            assert all(a.name != "enable_os_trust" for a in node.names)


def test_ocr_models_are_present_and_local():
    ok, msg = ex.ocr_models_present()
    assert ok, msg
    assert ex.ocr_model_dir().is_dir()


def test_missing_models_fail_loudly_with_the_fix(monkeypatch, tmp_path):
    monkeypatch.setenv("DOCIQ_OCR_MODEL_DIR", str(tmp_path))
    ok, msg = ex.ocr_models_present()
    assert not ok
    assert "never downloads" in msg and "DOCIQ_OCR_MODEL_DIR" in msg
    assert not ex.ocr_available()


def test_sanitize_message_strips_absolute_paths():
    msg = ex.sanitize_message(
        r"Could not read: C:\Users\a\AppData\Local\Temp\tmp61yhcl7p\x.msg")
    assert "tmp61yhcl7p" not in msg and "x.msg" in msg
    assert ex.sanitize_message("/var/folders/zz/T/abc/y.pdf") == "y.pdf"


def test_sanitize_message_leaves_relative_paths_alone():
    assert ex.sanitize_message("attachments/10_misnamed.docx: recovered") == \
        "attachments/10_misnamed.docx: recovered"


def test_an_unparseable_email_date_header_is_disclosed():
    """The date half of B-3's sibling class.

    ``detect_dates`` cannot read RFC-2822, so the parenthesized ISO token is
    the only thing that anchors an email to its own date. A Date header that
    would not parse used to cost the message its date under a bare ``except``,
    with no note anywhere.
    """
    raw = (b"From: a@example.com\r\n"
           b"Subject: s\r\n"
           b"Date: sometime last Thursday\r\n"
           b"\r\n"
           b"body\r\n")
    got = ex.extract("m.eml", raw)
    assert "Date: sometime last Thursday" in got.pages[0].text
    assert "(" not in got.pages[0].text.split("\n")[2]
    assert any("Date header" in n for n in got.notes), (
        "the lost date anchor was not disclosed: " + repr(got.notes))
    # Disclosed, deliberately NOT marked: nothing DocIQ read is missing.
    assert not ex.has_evidence_marker(" ".join(got.notes))


# --- B4: a dead OCR engine must not look like a few bad pages ---------------
#
# ``ocr_available()`` imports two modules and stats three .onnx files. It never
# constructs the engine and never runs inference, so it cannot tell a working
# engine from one that returns nothing on every page — and the Sprint-2 burn was
# INSIDE inference, under ``_ocr_pdf_pages``'s per-page ``except Exception``.
# The instance was fixed; the class — "presence stands in for capability, and
# nothing looks at the run as a whole" — was not.
#
# Enumeration of what could have raised this and did not:
#   ocr_available()                 presence only; passes a dead engine
#   _ocr_pdf_pages per-page except  one page at a time; no run-level view
#   per-document note               "N page(s) ... recovered no text", and the
#                                   innocent reading (bad scans) comes first
#   selftest's cold-construction    the real capability probe — and
#     inference probe               `build.py --skip-verify` bypasses it
# The backstop added here is the last one: a signal over the WHOLE run.


def _ocr_page_rec(page_no, text, **kw):
    from dociq.contracts import PageKind, PageRecord

    from dociq.ingest.pagemodel import M_OCR_BLANK
    if text.strip():
        kw.setdefault("ocr_conf", 0.9)
        kw.setdefault("ocr_line_count", 1)
        return PageRecord(page_no=page_no, text=text, kind=PageKind.OCR, **kw)
    # What make_page actually produces for a page routed to OCR that recovered
    # nothing: EMPTY (the only kind allowed to carry no ocr_conf), with the
    # disclosure note. Building it as PageKind.OCR would be testing a record
    # the pipeline cannot emit.
    kw.setdefault("notes", (M_OCR_BLANK,))
    return PageRecord(page_no=page_no, text=text, kind=PageKind.EMPTY, **kw)


def _doc_with(pages):
    from tests.fixtures import document

    return document("scan.pdf", tuple(pages), doc_id="LI-1")


def test_a_run_where_every_ocr_attempt_recovered_nothing_raises_an_alarm():
    """FAIL-BEFORE: nothing in the run said anything. Each document carried
    "N page(s) routed to OCR recovered no text", which reads as bad scans."""
    docs = (_doc_with([_ocr_page_rec(1, ""), _ocr_page_rec(2, "   ")]),
            _doc_with([_ocr_page_rec(1, "")]))
    assert ex.ocr_yield(docs) == (3, 0)
    warning = ex.ocr_yield_warning(docs)
    assert warning is not None
    assert "3 page(s)" in warning
    assert "dead OCR engine" in warning
    assert "selftest" in warning


def test_one_unreadable_page_among_readable_ones_raises_nothing():
    """The alarm must MEAN "every attempt", or it fires on ordinary scans and
    is turned off."""
    docs = (_doc_with([_ocr_page_rec(1, "SITE INSTRUCTION 44"),
                       _ocr_page_rec(2, "")]),)
    assert ex.ocr_yield(docs) == (2, 1)
    assert ex.ocr_yield_warning(docs) is None


def test_a_run_that_never_used_ocr_raises_nothing():
    from dociq.contracts import PageKind, PageRecord

    docs = (_doc_with([PageRecord(page_no=1, text="native text",
                                  kind=PageKind.NATIVE)]),)
    assert ex.ocr_yield(docs) == (0, 0)
    assert ex.ocr_yield_warning(docs) is None


def test_a_page_ocr_could_not_even_rasterize_counts_as_an_attempt():
    """It is an attempt that recovered nothing, and it is the exact shape a
    dead engine produces. Counting only PageKind.OCR would have let a run in
    which EVERY page failed to rasterize report no attempts and no alarm."""
    from dociq.contracts import PageKind, PageRecord

    failed = PageRecord(page_no=1, text="", kind=PageKind.NATIVE,
                        notes=(f"{ex.M_OCR_PAGE} to rasterize or read",))
    docs = (_doc_with([failed]),)
    assert ex.ocr_yield(docs) == (1, 0)
    assert ex.ocr_yield_warning(docs) is not None


def test_a_page_skipped_on_purpose_is_not_a_failed_ocr_attempt():
    """A-25 (D-51). ``ocr_yield`` counts a page note carrying ``M_IMAGE_UNREAD``
    as an attempt that failed, and the dead-engine alarm fires when every attempt
    failed. A skipped page carries that marker and was never attempted. Counted,
    a run over a production of letterhead-and-chart pages -- the ordinary case,
    at the default setting -- tells the operator the OCR engine is dead.

    FAIL-BEFORE, the false alarm itself: ``(1, 0)`` and the warning.
    """
    from dociq.contracts import PageKind, PageRecord

    got = _pages("15_mixed_content_page.pdf",
                 ex.ExtractOptions(skip_images_on_text_pages=True))
    assert ex.M_IMAGE_SKIPPED in got.pages[0].notes
    docs = (_doc_with(got.pages),)
    assert ex.ocr_yield(docs) == (0, 0)
    assert ex.ocr_yield_warning(docs) is None

    # A region that was TRIED and failed is still an attempt: what is excluded
    # is the skip, not the marker.
    failed = PageRecord(page_no=1, text="letterhead", kind=PageKind.NATIVE,
                        notes=(ex.M_IMAGE_UNREAD,))
    assert ex.ocr_yield((_doc_with([failed]),)) == (1, 0)


def test_ocr_available_no_longer_claims_a_capability_it_never_checks():
    """Withdraw the CLAIM. The function is unchanged in behaviour on purpose —
    it is a presence check and that is all it can be cheaply — but its docstring
    is what a maintainer reads before deciding whether a further check is
    needed, and it said nothing about the gap."""
    doc = ex.ocr_available.__doc__ or ""
    assert "PRESENCE check, not a capability check" in doc
    assert "never runs inference" in doc
    assert "ocr_yield" in doc
