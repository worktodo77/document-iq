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
