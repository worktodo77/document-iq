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

    # 4. The native text is NOT duplicated by the OCR-region merge.
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

    sentences = [unread(g) for g in (skipped, disabled, unavailable)]
    assert all(len(s) == 1 for s in sentences), sentences
    assert len({s[0] for s in sentences}) == 3, (
        "a skipped image, OCR disabled and a missing engine read as one "
        f"disclosure: {sentences}")
    assert "page(s) 1" in sentences[0][0], sentences[0][0]

    page = skipped.pages[0]
    assert page.kind is PageKind.NATIVE
    assert page.notes == (ex.M_IMAGE_SKIPPED,)
    assert "NOTICEOFDELAYNo14" not in page.text
    for other in (disabled, unavailable):
        assert ex.M_IMAGE_SKIPPED not in other.pages[0].notes


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
