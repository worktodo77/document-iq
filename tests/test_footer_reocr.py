"""D-25 — the targeted footer re-OCR, on the extractor side.

Criterion 4 measured 100.000% on native-text pages and 31.250% on pages DocIQ
had to OCR. The residue is not detector logic: it is a whole-page recognition,
tuned for a page of prose, losing a six-character stamp in a 10pt footer. D-25
rules that the stamp gets its own recognition pass rather than a new engine.

What is asserted here is not "it reads more stamps" — that is measured against
the production's load files in
``docs/verification/bates_d25_2026-08-01.md``, and a unit test cannot stand in
for it. What is asserted here is every property the measurement is only
trustworthy because of:

* it runs ONLY on pages it can help, so it does not multiply corpus runtime;
* it appends TEXT and never a locator, so it cannot turn a miss into a wrong;
* it is deterministic — same bytes in, same bytes out, every run;
* its geometry cannot trip the recognizer's own single-line bypass;
* it cannot sink a document when it fails.

The OCR engine is stubbed throughout. A test that ran the real recognizer
would measure rapidocr, take minutes, and still not be the acceptance run.
"""

from __future__ import annotations

import pytest

from dociq.contracts import PageKind
from dociq.identify.bates import FOOTER_BLOCK_MAX_LINES, BatesZone
from dociq.ingest import extract as ex
from tests.conftest import FIXTURES

REPEATS = 30
"""OCR-sensitive and pool-scheduled, so the long count — one green run of a
threaded pass proves nothing about the pass."""


class _Line:
    """Stand-in for :class:`dociq.ingest.extract.OcrLine`."""

    def __init__(self, text: str, conf: float = 0.9):
        self.text = text
        self.conf = conf
        self.box = ()


@pytest.fixture
def stub_ocr(monkeypatch):
    """Whole-page OCR that reads a page of prose and no footer at all.

    That is cause 1 from the acceptance run — the largest of the three — so it
    is the state the band pass exists to rescue.
    """
    calls: dict[str, list] = {"page": [], "band": []}

    def fake_page_array(page):
        return ("page", page.number)

    def fake_ocr_array(arr):
        calls["page"].append(arr[1])
        return "SITE INSTRUCTION 014 body text that carries no stamp", [0.9, 0.88]

    monkeypatch.setattr(ex, "_page_array", fake_page_array)
    monkeypatch.setattr(ex, "_ocr_array", fake_ocr_array)
    return calls


@pytest.fixture
def stub_band(monkeypatch, stub_ocr):
    """Band rasterization + recognition, recording every page it was asked for."""
    def fake_band_array(page, dpi, band, *, top):
        stub_ocr["band"].append((page.number, dpi, band, top))
        return ("band", page.number, top)

    def fake_ocr_lines(arr):
        if arr[0] != "band":
            return [_Line("SITE INSTRUCTION 014")]
        if arr[2]:            # the top band holds nothing on this fixture
            return [_Line("")]
        return [_Line(f"Page {arr[1] + 1}  iiCON{900000 + arr[1]}", 0.97)]

    monkeypatch.setattr(ex, "_band_array", fake_band_array)
    monkeypatch.setattr(ex, "ocr_lines", fake_ocr_lines)
    return stub_ocr


def _extract(name: str, **kw):
    raw = (FIXTURES / name).read_bytes()
    return ex._extract_pdf(raw, ex.ExtractOptions(**kw))


# ---------------------------------------------------------------------------
# it runs only where it can help
# ---------------------------------------------------------------------------


def test_the_band_pass_never_touches_a_native_text_page(stub_band):
    """The cost bound that makes this affordable on a real corpus.

    ``03_mixed.pdf`` is native / scanned / native, which is what a production
    actually looks like. Only the scanned page may be re-read.
    """
    pages, _notes = _extract("03_mixed_transmittal.pdf")
    assert [p.kind for p in pages] == [PageKind.NATIVE, PageKind.OCR,
                                       PageKind.NATIVE]
    assert [c[0] for c in stub_band["band"]] == [1]


def test_a_page_that_already_read_a_stamp_is_not_re_read(monkeypatch, stub_band):
    """Only a page with NO stamp-shaped line anywhere in its zone is re-read."""
    monkeypatch.setattr(ex, "_ocr_array",
                        lambda arr: ("body text\niiCON003944", [0.9]))
    pages, _notes = _extract("02_scanned_instruction.pdf")
    assert stub_band["band"] == []
    assert all(p.kind is PageKind.OCR for p in pages)


def test_the_pass_can_be_turned_off_entirely(stub_band):
    pages, notes = _extract("02_scanned_instruction.pdf", footer_reocr=False)
    assert stub_band["band"] == []
    assert not any("iiCON" in p.text for p in pages)


def test_the_top_band_is_read_only_where_the_bottom_band_found_nothing(
        monkeypatch, stub_ocr):
    """§4 says "corners/footers", so a header-stamped production is covered —
    but it is covered second, and only on the pages the footer did not
    resolve. Page 0's footer carries a stamp; page 1's does not."""
    def fake_band_array(page, dpi, band, *, top):
        stub_ocr["band"].append((page.number, top))
        return ("band", page.number, top)

    def fake_ocr_lines(arr):
        if arr[0] == "band" and not arr[2] and arr[1] == 0:
            return [_Line("iiCON900000", 0.95)]
        if arr[0] == "band" and arr[2] and arr[1] == 1:
            return [_Line("iiCON900001", 0.95)]
        return [_Line("nothing")]

    monkeypatch.setattr(ex, "_band_array", fake_band_array)
    monkeypatch.setattr(ex, "ocr_lines", fake_ocr_lines)
    pages, _notes = _extract("02_scanned_instruction.pdf")
    assert sorted(stub_ocr["band"]) == [(0, False), (1, False), (1, True)]
    assert pages[0].text.endswith("iiCON900000")
    assert pages[1].text.endswith("iiCON900001")


# ---------------------------------------------------------------------------
# what it does to the page
# ---------------------------------------------------------------------------


def test_the_recovered_token_lands_in_the_bates_zone(stub_band):
    pages, _notes = _extract("02_scanned_instruction.pdf")
    for i, p in enumerate(pages):
        zone = {line for _, line in BatesZone().slice_lines(p.text)}
        assert f"iiCON{900000 + i}" in zone


def test_only_the_stamp_token_is_appended_not_the_whole_strip(stub_band):
    """The page already has its own reading of that footer. A second full
    reading would double-count it in every token count and dedup downstream."""
    pages, _notes = _extract("02_scanned_instruction.pdf")
    assert "Page 1  iiCON900000" not in pages[0].text
    assert pages[0].text.endswith("\niiCON900000")


def test_the_recovered_confidence_joins_the_page(stub_band):
    """It is text on the page now, and §4 Stage 2's threshold is measured over
    the text the page carries."""
    pages, _notes = _extract("02_scanned_instruction.pdf")
    assert pages[0].ocr_conf == round((0.9 + 0.88 + 0.97) / 3, 4)


def test_the_recovery_is_disclosed_in_the_document_notes(stub_band):
    """No silent bounds: an operator can see how much of the numbering came
    from the second pass, and what the second pass was allowed to do."""
    _pages, notes = _extract("02_scanned_instruction.pdf")
    note = [n for n in notes if "targeted footer re-OCR" in n]
    assert len(note) == 1
    assert "2 page(s)" in note[0]
    assert str(ex.FOOTER_REOCR_DPI) in note[0]
    assert str(FOOTER_BLOCK_MAX_LINES) in note[0]


def test_a_token_the_page_already_carries_is_not_appended_twice(
        monkeypatch, stub_band):
    monkeypatch.setattr(ex, "_ocr_array",
                        lambda arr: ("untij isfiyed\niiCON900000", [0.9]))
    pages, _notes = _extract("02_scanned_instruction.pdf")
    assert pages[0].text.count("iiCON900000") == 1


# ---------------------------------------------------------------------------
# it cannot make things worse
# ---------------------------------------------------------------------------


def test_a_failing_band_pass_marks_the_document_and_keeps_every_page(
        monkeypatch, stub_ocr):
    def boom(*a, **k):
        raise RuntimeError("rasterizer said no")

    monkeypatch.setattr(ex, "_reocr_bands", boom)
    pages, notes = _extract("02_scanned_instruction.pdf")
    assert len(pages) == 2
    assert all(p.text.strip() for p in pages)
    assert any(ex.M_OCR_FOOTER in n for n in notes)
    assert ex.has_transient_marker(" ".join(notes))


def test_one_unreadable_band_does_not_sink_the_others(monkeypatch, stub_ocr):
    def fake_band_array(page, dpi, band, *, top):
        if page.number == 0:
            raise RuntimeError("this page will not rasterize")
        return ("band", page.number, top)

    monkeypatch.setattr(ex, "_band_array", fake_band_array)
    monkeypatch.setattr(ex, "ocr_lines",
                        lambda arr: [_Line(f"iiCON{900000 + arr[1]}", 0.9)])
    pages, _notes = _extract("02_scanned_instruction.pdf")
    assert "iiCON" not in pages[0].text
    assert pages[1].text.endswith("iiCON900001")


def test_the_appended_block_can_never_exceed_its_stated_bound(
        monkeypatch, stub_ocr):
    monkeypatch.setattr(ex, "_band_array",
                        lambda page, dpi, band, *, top: ("band", page.number, top))
    monkeypatch.setattr(ex, "ocr_lines", lambda arr: [
        _Line(" ".join(f"iiCON{900000 + i}" for i in range(20)), 0.9)])
    pages, _notes = _extract("02_scanned_instruction.pdf")
    for p in pages:
        assert p.text.count("iiCON") == FOOTER_BLOCK_MAX_LINES


# ---------------------------------------------------------------------------
# determinism (criterion 7)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("run", range(REPEATS))
def test_the_pass_is_byte_identical_run_to_run(stub_band, run):
    body = "SITE INSTRUCTION 014 body text that carries no stamp"
    pages, notes = _extract("02_scanned_instruction.pdf")
    # Page 1 also carries the deterministic [PHOTO] block, which rides on the
    # first page of an image-based file; the tail is the part under test.
    assert pages[0].text.endswith(f"{body}\niiCON900000")
    assert pages[1].text == f"{body}\niiCON900001"
    again_pages, again_notes = _extract("02_scanned_instruction.pdf")
    assert [p.text for p in pages] == [p.text for p in again_pages]
    assert [p.ocr_conf for p in pages] == [p.ocr_conf for p in again_pages]
    assert notes == again_notes


def test_nothing_about_the_ATTEMPT_reaches_the_page(stub_band):
    """No timing, no retry count, no resolution, no band, no marker. The only
    thing the second pass may contribute is the stamp itself."""
    pages, _notes = _extract("02_scanned_instruction.pdf")
    for p in pages:
        for leak in ("400", "dpi", "band", "re-OCR", "retry", "top", "0.14"):
            assert leak not in p.text


# ---------------------------------------------------------------------------
# geometry — the recognizer's own configuration is the constraint
# ---------------------------------------------------------------------------


def _band_shape(width_pt: float, height_pt: float, top: bool):
    import fitz

    doc = fitz.open()
    doc.new_page(width=width_pt, height=height_pt)
    arr = ex._band_array(doc[0], ex.FOOTER_REOCR_DPI, ex.FOOTER_REOCR_BAND,
                         top=top)
    doc.close()
    return arr.shape[1], arr.shape[0]  # (w, h) in pixels


@pytest.mark.parametrize("size", [
    (612, 792),      # US Letter portrait
    (792, 612),      # US Letter landscape — the shape that trips the bypass
    (595, 842),      # A4 portrait
    (1224, 792),     # 17x11 tabloid landscape
    (200, 1200),     # absurdly tall
])
@pytest.mark.parametrize("top", [False, True])
def test_the_band_never_trips_the_recognizers_single_line_bypass(size, top):
    """rapidocr skips DETECTION when width/height exceeds ``width_height_ratio``
    and recognizes the whole strip as one line — which would fold a page
    number, a footer note and the stamp into one unparseable string. The band
    height is bounded by the engine's own configured ratio, not by taste."""
    w, h = _band_shape(*size, top=top)
    assert w / h <= ex._FOOTER_MAX_ASPECT + 1e-6, (size, top, w, h)


def test_the_band_is_a_higher_RESOLUTION_re_render_not_an_upscale():
    """The mechanism is more pixels on the glyph before the recognizer's fixed
    48px crop resize. Rendering the same band at the page's own 200 dpi would
    add no information at all."""
    import fitz

    doc = fitz.open()
    doc.new_page(width=612, height=792)
    band = ex._band_array(doc[0], ex.FOOTER_REOCR_DPI, ex.FOOTER_REOCR_BAND,
                          top=False)
    page_px = ex._page_array(doc[0]).shape[0] * ex._page_array(doc[0]).shape[1]
    doc.close()
    assert ex.FOOTER_REOCR_DPI > 200
    # The band is a fraction of the page, at twice the resolution: it must be
    # sharper per inch and still smaller than a whole page of pixels.
    assert band.shape[0] * band.shape[1] < page_px * 2
