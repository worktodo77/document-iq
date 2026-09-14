"""D-50: documents embedded in a Word file become child documents.

The regression suite for the ruling: which embedded objects are recovered,
in what order, under what names, how containers nest inside containers, and
that every object not recovered is named in a note carrying an evidence
marker. Each test was watched failing before the code it holds existed. Do
not make a failure here pass by loosening an assertion.

Symbols of ``dociq.ingest.extract`` are referenced inside the test that needs
them, never at module import time, so one missing symbol fails one test
rather than the whole module's collection.
"""

from __future__ import annotations

import io
import shutil
import struct
import zipfile

import pytest

from dociq.contracts import ProcessingStatus, RunConfig
from dociq.ingest import extract as ex
from dociq.ingest import walker

from .conftest import FIXTURES

import make_fixtures  # tests/fixtures/make_fixtures.py; conftest puts it on sys.path


# ---------------------------------------------------------------------------
# Shared walk helper
# ---------------------------------------------------------------------------


def _cfg(tmp_path, src) -> RunConfig:
    return RunConfig(source_root=str(src), output_root=str(tmp_path / "out"),
                     ocr_engine_version=ex.ocr_engine_version())


def _walk_single_fixture(tmp_path, name: str):
    """Copy exactly one fixture file into an isolated source folder and walk
    it alone -- the same pattern ``test_walker.py``'s eml-attachment test
    uses, so a child's ``rel_path`` is always ``<name>/<member>`` with no
    sibling fixture in the way."""
    src = tmp_path / "src"
    src.mkdir()
    shutil.copy(FIXTURES / name, src / name)
    return walker.run(_cfg(tmp_path, src),
                      walker.WalkOptions(ocr_enabled=False, resume=False))


def _text(doc) -> str:
    return doc.pages[0].text if doc.pages else ""


# ---------------------------------------------------------------------------
# A minimal, dependency-free .docx builder for the two synthetic edge cases
# (5a malformed Ole10Native, 5b nesting depth) -- only what
# expand_docx_embeddings and _extract_docx read: [Content_Types].xml,
# _rels/.rels, word/document.xml, word/_rels/document.xml.rels, and the
# embedded parts themselves. Fixture 17 (make_fixtures.word_embeddings_docx)
# is the corpus-facing, python-docx-built version of the same shape; this
# one exists so a deep nesting chain does not need N python-docx round trips.
# ---------------------------------------------------------------------------

_MIN_CT = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Default Extension="bin" '
    'ContentType="application/vnd.openxmlformats-officedocument.oleObject"/>'
    '<Default Extension="docx" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document"/>'
    '<Override PartName="/word/document.xml" ContentType='
    '"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '</Types>'
)
_MIN_ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rIdOfficeDoc" Type="http://schemas.openxmlformats.org/'
    'officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
    '</Relationships>'
)
_MIN_NS = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:o="urn:schemas-microsoft-com:office:office"'
)


def _minimal_docx_with_embeds(embeds) -> bytes:
    """``embeds`` is ``[(progid, part_name, part_bytes), ...]``: one
    ``<o:OLEObject>``, in order, per entry, each resolved through its own
    relationship to a stored part."""
    objects_xml = []
    rel_entries = []
    parts: dict = {}
    for i, (progid, part_name, part_bytes) in enumerate(embeds, start=1):
        rid = f"rIdEmbed{i}"
        objects_xml.append(
            f'<w:p><w:r><w:object><o:OLEObject Type="Embed" ProgID="{progid}" '
            f'r:id="{rid}"/></w:object></w:r></w:p>')
        rel_entries.append(
            f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/'
            f'officeDocument/2006/relationships/oleObject" '
            f'Target="{part_name[len("word/"):]}"/>')
        parts[part_name] = part_bytes

    doc_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document {_MIN_NS}><w:body>'
        '<w:p><w:r><w:t>a leaf paragraph</w:t></w:r></w:p>'
        + "".join(objects_xml)
        + '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/></w:sectPr>'
        '</w:body></w:document>')
    doc_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(rel_entries) + '</Relationships>')

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _MIN_CT)
        zf.writestr("_rels/.rels", _MIN_ROOT_RELS)
        zf.writestr("word/document.xml", doc_xml)
        zf.writestr("word/_rels/document.xml.rels", doc_rels)
        for name, data in parts.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _malformed_ole10_native() -> bytes:
    """A truncated ``\\x01Ole10Native`` stream: label and filename are
    well-formed, then it declares a temp-path length (9999) with nothing
    behind it to satisfy that length -- ``_parse_ole10_native``'s own
    bounds-check (``need()``) must raise on this, never a bare index error."""
    label = b"bad.eml\x00"
    filename = b"bad.eml\x00"
    reserved = b"\x00" * 4
    body = label + filename + reserved + struct.pack("<I", 9999)
    return struct.pack("<I", 0) + struct.pack("<H", 0) + body


def _malformed_ole10_native_bad_data_length() -> bytes:
    """Well-formed through the temp-path field (empty, so that check cannot
    be what fails), then declares a DATA length (999999) far larger than
    what actually follows it. Padded well past the 4096-byte mini-stream
    cutoff with trailing filler so ``_write_compound_file`` does not itself
    extend the stream -- which would otherwise silently supply enough bytes
    to satisfy an oversized ``data_len`` instead of exercising its own
    bounds check. 5a's malformed stream never reaches this check at all: it
    fails earlier, at the temp-path-length read."""
    label = b"legacy.doc\x00"
    filename = b"legacy.doc\x00"
    reserved = b"\x00" * 4
    temp_path = b""
    filler = b"\x00" * 4200
    body = (label + filename + reserved
            + struct.pack("<I", len(temp_path)) + temp_path
            + struct.pack("<I", 999999)  # declared data length
            + filler)  # actual bytes available: far fewer than declared
    return struct.pack("<I", len(body) + 2) + struct.pack("<H", 0) + body


def _minimal_pdf_bytes() -> bytes:
    """A tiny, real, single-page PDF -- only ``expand_docx_embeddings``'s
    4-byte ``%PDF`` sniff matters to these tests, not its content."""
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, invariant=1)
    c.drawString(60, 700, "placeholder")
    c.showPage()
    c.save()
    return buf.getvalue()


def _add_zip_parts(raw: bytes, parts: dict) -> bytes:
    """Return ``raw`` (a zip/.docx) with every ``name: bytes`` in ``parts``
    added as an extra member -- used to add UNREFERENCED
    ``word/embeddings/`` parts that ``_minimal_docx_with_embeds`` has no way
    to express (it only ever adds a part alongside an ``<o:OLEObject>``
    referencing it)."""
    zin = zipfile.ZipFile(io.BytesIO(raw))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for n in zin.namelist():
            zout.writestr(n, zin.read(n))
        for name, data in parts.items():
            zout.writestr(name, data)
    zin.close()
    return buf.getvalue()


def _docx_with_raw_object(object_xml: str, rels_xml: str | None = None,
                          parts: dict | None = None) -> bytes:
    """A minimal .docx whose body holds exactly one hand-written
    ``<o:OLEObject .../>`` (``object_xml``), resolved (or not) through
    ``rels_xml`` -- for the not-recovered edge cases
    (``expand_docx_embeddings`` never needs a real paragraph run around the
    object)."""
    doc_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document {_MIN_NS}><w:body>'
        '<w:p><w:r><w:object w:dxaOrig="1440" w:dyaOrig="1440">'
        + object_xml +
        '</w:object></w:r></w:p>'
        '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/></w:sectPr>'
        '</w:body></w:document>')
    rels = rels_xml if rels_xml is not None else (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/'
        'package/2006/relationships"/>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _MIN_CT)
        zf.writestr("_rels/.rels", _MIN_ROOT_RELS)
        zf.writestr("word/document.xml", doc_xml)
        zf.writestr("word/_rels/document.xml.rels", rels)
        for name, data in (parts or {}).items():
            zf.writestr(name, data)
    return buf.getvalue()


def _docx_with_aux_objects(*, body_part: str, footer_part: str,
                           footnote_target_parts: list[str], body_bytes: bytes,
                           footer_bytes: bytes,
                           extra_parts: dict | None = None) -> bytes:
    """A .docx carrying one ``<o:OLEObject>`` in the body, one in
    ``word/footer1.xml`` and one per ``footnote_target_parts`` entry in
    ``word/footnotes.xml`` -- each resolved through THAT part's own
    ``.rels``, and the body and footer objects using the SAME literal r:id
    string ("rIdEmbed1") on purpose, since a header/footer/footnote r:id
    only makes sense within its own part's relationship namespace. A
    footnote target may name the SAME part as the body object, to exercise
    "each part read once" (a part referenced twice yields one child).
    ``extra_parts`` are written as they are, referenced by nothing."""
    doc_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document {_MIN_NS}><w:body>'
        '<w:p><w:r><w:object w:dxaOrig="1440" w:dyaOrig="1440">'
        '<o:OLEObject Type="Embed" ProgID="Excel.Sheet.12" r:id="rIdEmbed1"/>'
        '</w:object></w:r></w:p>'
        '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/></w:sectPr>'
        '</w:body></w:document>')
    # The footer and footnotes parts are related from the main part, as in any
    # package Word writes; their objects are found through those relationships.
    doc_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
        '2006/relationships"><Relationship Id="rIdEmbed1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        f'relationships/oleObject" Target="{body_part[len("word/"):]}"/>'
        '<Relationship Id="rIdFooter1" Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/footer" Target="footer1.xml"/>'
        '<Relationship Id="rIdFootnotes" Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/footnotes" Target="footnotes.xml"/>'
        '</Relationships>')
    footer_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:ftr {_MIN_NS}><w:p><w:r>'
        '<w:object w:dxaOrig="1440" w:dyaOrig="1440">'
        '<o:OLEObject Type="Embed" ProgID="Excel.Sheet.12" r:id="rIdEmbed1"/>'
        '</w:object></w:r></w:p></w:ftr>')
    footer_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
        '2006/relationships"><Relationship Id="rIdEmbed1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        f'relationships/oleObject" Target="{footer_part[len("word/"):]}"/>'
        '</Relationships>')
    footnotes_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:footnotes {_MIN_NS}><w:footnote w:id="1">'
        + "".join(
            '<w:p><w:r><w:object w:dxaOrig="1440" w:dyaOrig="1440">'
            f'<o:OLEObject Type="Embed" ProgID="Excel.Sheet.12" r:id="rIdFn{i}"/>'
            '</w:object></w:r></w:p>'
            for i in range(len(footnote_target_parts)))
        + '</w:footnote></w:footnotes>')
    footnotes_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
        '2006/relationships">'
        + "".join(
            f'<Relationship Id="rIdFn{i}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/oleObject" '
            f'Target="{target[len("word/"):]}"/>'
            for i, target in enumerate(footnote_target_parts))
        + '</Relationships>')

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _MIN_CT)
        zf.writestr("_rels/.rels", _MIN_ROOT_RELS)
        zf.writestr("word/document.xml", doc_xml)
        zf.writestr("word/_rels/document.xml.rels", doc_rels)
        zf.writestr("word/footer1.xml", footer_xml)
        zf.writestr("word/_rels/footer1.xml.rels", footer_rels)
        zf.writestr("word/footnotes.xml", footnotes_xml)
        zf.writestr("word/_rels/footnotes.xml.rels", footnotes_rels)
        zf.writestr(body_part, body_bytes)
        zf.writestr(footer_part, footer_bytes)
        for name, data in (extra_parts or {}).items():
            zf.writestr(name, data)
    return buf.getvalue()


def _small_eml_bytes(subject: str, body_sentinel: str,
                     attachment: tuple[str, str] | None = None) -> bytes:
    """A minimal RFC-822 email, optionally with one text/plain attachment as
    ``(filename, sentinel_text)`` -- the same shape make_fixtures builds
    object 3's wrapped email with, factored out for reuse."""
    import base64

    lines = [
        "From: engineer@example.com",
        "To: contractor@example.com",
        f"Subject: {subject}",
        "Date: Fri, 19 Jul 2024 09:00:00 +0000",
        "MIME-Version: 1.0",
    ]
    if attachment is None:
        lines += ["Content-Type: text/plain; charset=utf-8", "", body_sentinel, ""]
    else:
        att_name, att_sentinel = attachment
        att_b64 = base64.b64encode(att_sentinel.encode("utf-8")).decode("ascii")
        lines += [
            'Content-Type: multipart/mixed; boundary="dociq-test-boundary"',
            "", "--dociq-test-boundary",
            "Content-Type: text/plain; charset=utf-8", "", body_sentinel, "",
            "--dociq-test-boundary",
            f'Content-Type: text/plain; name="{att_name}"',
            "Content-Transfer-Encoding: base64",
            f'Content-Disposition: attachment; filename="{att_name}"',
            "", att_b64, "", "--dociq-test-boundary--", "",
        ]
    return "\r\n".join(lines).encode("utf-8")


# ---------------------------------------------------------------------------
# 1. Fixture 17: every child recovered, in document order, right sentinel
# ---------------------------------------------------------------------------


def test_fixture17_children_recovered_in_document_order_with_sentinels(tmp_path):
    r = _walk_single_fixture(tmp_path, "17_word_embeddings.docx")
    parent_rel = "17_word_embeddings.docx"
    assert any(d.rel_path == parent_rel for d in r.documents), (
        f"the Word file itself must still appear as a record: "
        f"{[d.rel_path for d in r.documents]!r}")

    children = [d for d in r.documents if d.parent_doc_id == parent_rel]
    assert len(children) == 4, (
        f"expected exactly 4 recovered children (workbook, PDF, email, "
        f"inner .docx) -- object 5 is neither a ZIP nor a compound file and "
        f"must not produce one: {[(c.rel_path, c.container_order) for c in children]!r}")

    orders = sorted(c.container_order for c in children)
    assert orders == [0, 1, 2, 3], (
        f"container_order must be the document order of the 4 recovered "
        f"objects: {orders!r}")

    by_order = {c.container_order: c for c in children}
    expected_rel_path_by_order = {
        0: f"{parent_rel}/Microsoft_Excel_Worksheet1.xlsx",
        1: f"{parent_rel}/oleObject2.pdf",  # "<part stem>.pdf" naming rule
        2: f"{parent_rel}/notice.eml",
        3: f"{parent_rel}/Microsoft_Word_Document1.docx",
    }
    for order, expected_rel_path in expected_rel_path_by_order.items():
        assert by_order[order].rel_path == expected_rel_path, (
            f"child at container_order {order} must be {expected_rel_path!r}: "
            f"got {by_order[order].rel_path!r}")

    # Sentinel leak check, across EVERY record this walk produced -- not just
    # the 4 direct children -- so a sentinel copied into the PARENT's own
    # text, into a grandchild, or into an unrelated record is caught too (a
    # mutant that folded every child's page text into the parent's own
    # passed a check scoped to the 4 children alone). Every sentinel is held
    # by exactly one record, and the PDF and inner-docx-nested-xlsx paths pin
    # the "<part stem>.pdf" / nested-rel_path naming rules along with it.
    sentinel_owner = {
        "BISON-WORKBOOK": f"{parent_rel}/Microsoft_Excel_Worksheet1.xlsx",
        "CONDOR-PDF": f"{parent_rel}/oleObject2.pdf",
        "EGRET-EMAIL": f"{parent_rel}/notice.eml",
        "DUGONG-ATTACHMENT": f"{parent_rel}/notice.eml/attached.txt",
        "FALCON-INNER": f"{parent_rel}/Microsoft_Word_Document1.docx",
        "GECKO-NESTED": (f"{parent_rel}/Microsoft_Word_Document1.docx/"
                         "Microsoft_Excel_Worksheet1.xlsx"),
    }
    for sentinel, expected_owner in sentinel_owner.items():
        holders = [d.rel_path for d in r.documents
                  if any(sentinel in p.text for p in d.pages)]
        assert holders == [expected_owner], (
            f"{sentinel!r} must appear in exactly one record's page text "
            f"anywhere in this walk, {expected_owner!r}: found in {holders!r}")


# ---------------------------------------------------------------------------
# 2. Nesting: attachment-of-a-child and embed-of-a-child; Doc ID assignment
# ---------------------------------------------------------------------------


def test_nested_children_two_levels_deep_and_doc_id_assignment_has_no_warnings(tmp_path):
    r = _walk_single_fixture(tmp_path, "17_word_embeddings.docx")
    parent_rel = "17_word_embeddings.docx"
    email_rel = f"{parent_rel}/notice.eml"
    inner_docx_rel = f"{parent_rel}/Microsoft_Word_Document1.docx"
    email_child = next((d for d in r.documents if d.rel_path == email_rel), None)
    inner_docx_child = next(
        (d for d in r.documents if d.rel_path == inner_docx_rel), None)
    assert email_child is not None and inner_docx_child is not None, (
        f"expected the email ({email_rel!r}) and the inner .docx "
        f"({inner_docx_rel!r}) among the Word file's children: "
        f"{[d.rel_path for d in r.documents]!r}")

    grandkids_of_email = [d for d in r.documents
                          if d.parent_doc_id == email_child.rel_path]
    grandkids_of_inner = [d for d in r.documents
                          if d.parent_doc_id == inner_docx_child.rel_path]
    # Grandchild rel_path, count and container_order are all pinned, not
    # just "some grandchild carries the sentinel" (a mutant that built a
    # grandchild's rel_path from the TOP file instead of its immediate
    # parent collided GECKO-NESTED's path with object 1's, and only the
    # accounting test below caught it, as a bare failure with no detail).
    assert len(grandkids_of_email) == 1, (
        f"expected exactly one child of {email_rel!r}: "
        f"{[g.rel_path for g in grandkids_of_email]!r}")
    assert len(grandkids_of_inner) == 1, (
        f"expected exactly one child of {inner_docx_rel!r}: "
        f"{[g.rel_path for g in grandkids_of_inner]!r}")
    g_email, g_inner = grandkids_of_email[0], grandkids_of_inner[0]
    assert g_email.rel_path == f"{email_rel}/attached.txt", (
        f"got {g_email.rel_path!r}")
    assert g_inner.rel_path == f"{inner_docx_rel}/Microsoft_Excel_Worksheet1.xlsx", (
        f"got {g_inner.rel_path!r}")
    assert g_email.container_order == 0, g_email.container_order
    assert g_inner.container_order == 0, g_inner.container_order
    assert "DUGONG-ATTACHMENT" in _text(g_email), _text(g_email)
    assert "GECKO-NESTED" in _text(g_inner), _text(g_inner)

    from dociq.docid import assign

    result = assign.assign_doc_ids(r.documents, index=None)
    assert result.warnings == (), (
        f"Stage 3b Doc ID assignment over these nested records must issue "
        f"no warnings: {result.warnings!r}")
    for d in result.documents:
        assert d.doc_id, f"{d.rel_path!r} was left without a Doc ID: {d!r}"

    # NESTED IDs, not merely non-empty ones: a grandchild's doc_id is a
    # child of its parent's own (assigned) doc_id, and parent_doc_id is
    # REMAPPED from the rel_path token Stage 1 uses to the Stage 3b doc_id
    # (assign.py's own "Nested containers are real" / "The REMAP" comments).
    by_rel_path = {d.rel_path: d for d in result.documents}
    notice_doc = by_rel_path[email_rel]
    attach_doc = by_rel_path[f"{email_rel}/attached.txt"]
    assert attach_doc.parent_doc_id == notice_doc.doc_id, (
        f"grandchild's parent_doc_id must be remapped to the email's own "
        f"doc_id: {attach_doc.parent_doc_id!r} != {notice_doc.doc_id!r}")
    assert attach_doc.doc_id.startswith(notice_doc.doc_id), (
        f"grandchild doc_id {attach_doc.doc_id!r} must nest under parent "
        f"doc_id {notice_doc.doc_id!r}")


# ---------------------------------------------------------------------------
# 3. Object 5 (neither ZIP nor compound file): marked note, no child
# ---------------------------------------------------------------------------


def test_object5_is_neither_zip_nor_compound_file_marked_note_no_child(tmp_path):
    r = _walk_single_fixture(tmp_path, "17_word_embeddings.docx")
    parent = next(d for d in r.documents if d.rel_path == "17_word_embeddings.docx")
    marked = [n for n in parent.notes if ex.has_evidence_marker(n)]
    # EXACTLY one marked note, naming object 5 -- not "some marked note
    # among possibly several", and specifically NOT a false "could not be
    # read" disclosure duplicated onto every one of the 4 objects that WERE
    # recovered (a mutant that added such a note for each recovered part
    # passed a presence-only check; it would also wrongly count the Word
    # file as evidence-lost in accounting).
    assert len(marked) == 1, (
        f"expected EXACTLY one marked note on the Word file's own record "
        f"(object 5 only): {marked!r}; all notes: {parent.notes!r}")
    assert "oleObject5.bin" in marked[0], (
        f"expected the one marked note to name "
        f"word/embeddings/oleObject5.bin: {marked[0]!r}")
    for recovered_part in ("Microsoft_Excel_Worksheet1.xlsx", "oleObject2.bin",
                           "oleObject3.bin", "Microsoft_Word_Document1.docx"):
        assert recovered_part not in marked[0], (
            f"the one marked note must not ALSO name a successfully "
            f"recovered part ({recovered_part!r}): {marked[0]!r}")
    children = [d for d in r.documents if d.parent_doc_id == parent.rel_path]
    assert len(children) == 4, (
        f"object 5 must not add a 5th child: {[c.rel_path for c in children]!r}")


# ---------------------------------------------------------------------------
# 4. Fixture 16's existing placeholder object: marked note, no child
# ---------------------------------------------------------------------------


def test_fixture16_placeholder_object_marked_note_no_child(tmp_path):
    r = _walk_single_fixture(tmp_path, "16_word_constructs.docx")
    parent = next(d for d in r.documents if d.rel_path == "16_word_constructs.docx")
    marked = [n for n in parent.notes if ex.has_evidence_marker(n)]
    assert any("oleObject1.bin" in n for n in marked), (
        f"expected a marked note naming word/embeddings/oleObject1.bin: "
        f"{parent.notes!r}")
    children = [d for d in r.documents if d.parent_doc_id == parent.rel_path]
    assert children == [], (
        f"fixture 16's placeholder object must not produce a child: "
        f"{[c.rel_path for c in children]!r}")


# ---------------------------------------------------------------------------
# 5a. A malformed \x01Ole10Native stream: marked note, never an exception
# ---------------------------------------------------------------------------


def test_malformed_ole10native_stream_is_a_marked_note_not_an_exception():
    compound = make_fixtures._write_compound_file(
        {"\x01Ole10Native": _malformed_ole10_native()})
    raw = _minimal_docx_with_embeds(
        [("Package", "word/embeddings/oleObject1.bin", compound)])

    exp = ex.expand_docx_embeddings(raw)  # must not raise

    assert exp.members == (), (
        f"a malformed Ole10Native stream must not produce a child: "
        f"{exp.members!r}")
    marked = [n for n in exp.notes if ex.has_evidence_marker(n)]
    # Every marked note names the PART and says why (expand_docx_embeddings'
    # own docstring); generic wording with no part name passed an
    # "Ole10Native"-only check.
    assert any("oleObject1.bin" in n and "Ole10Native" in n for n in marked), (
        f"expected ONE marked note naming both the part "
        f"('word/embeddings/oleObject1.bin') and the malformed stream "
        f"('Ole10Native'): {exp.notes!r}")


# ---------------------------------------------------------------------------
# 5b. A nesting chain past _ZIP_MAX_DEPTH: marked note
# ---------------------------------------------------------------------------


def test_nesting_chain_past_zip_max_depth_yields_a_marked_note(tmp_path):
    # The bound this pins (walker._child_records' docstring): the entry
    # file's own objects always expand, and a child container at level L
    # (its direct children are level 1) expands only while L <
    # _ZIP_MAX_DEPTH. So records exist down to _ZIP_MAX_DEPTH containers
    # below the entry file, and the deepest one's own members are named in a
    # marked note, not recovered. That is one level shallower than
    # expand_zip, which also reads the members of its deepest archive; the
    # loss is disclosed either way. An earlier form of this test only
    # required "some marked note, somewhere, mentioning 'depth'", which a
    # cap reached one container early satisfied just as well, and which
    # rejected a correct note worded "nesting deeper than N".
    depth = ex._ZIP_MAX_DEPTH + 2

    inner = _minimal_docx_with_embeds([])
    for _ in range(depth):
        inner = _minimal_docx_with_embeds(
            [("Word.Document.12", "word/embeddings/inner.docx", inner)])

    src = tmp_path / "src"
    src.mkdir()
    (src / "chain.docx").write_bytes(inner)
    r = walker.run(_cfg(tmp_path, src),
                   walker.WalkOptions(ocr_enabled=False, resume=False))

    by_rel_path = {d.rel_path: d for d in r.documents}
    expected_chain = ["chain.docx"]
    for _ in range(ex._ZIP_MAX_DEPTH):
        expected_chain.append(expected_chain[-1] + "/inner.docx")
    for i, rel_path in enumerate(expected_chain):
        assert rel_path in by_rel_path, (
            f"expected a record at {rel_path!r} (level {i} of the nesting "
            f"chain): records seen: {sorted(by_rel_path)!r}")
        expected_parent = expected_chain[i - 1] if i > 0 else None
        assert by_rel_path[rel_path].parent_doc_id == expected_parent, (
            f"{rel_path!r}: parent_doc_id must be {expected_parent!r}, got "
            f"{by_rel_path[rel_path].parent_doc_id!r}")

    too_deep = expected_chain[-1] + "/inner.docx"
    assert too_deep not in by_rel_path, (
        f"nothing may appear past _ZIP_MAX_DEPTH ({ex._ZIP_MAX_DEPTH}) "
        f"nested containers below the entry file: {too_deep!r} was found "
        f"among {sorted(by_rel_path)!r}")

    marked_by_record = {
        rel_path: [n for n in doc.notes if ex.has_evidence_marker(n)]
        for rel_path, doc in by_rel_path.items() if rel_path in expected_chain}
    deepest = expected_chain[-1]
    assert marked_by_record[deepest], (
        f"the deepest record in the chain ({deepest!r}) must carry a marked "
        f"note about its own un-expanded nesting: {by_rel_path[deepest].notes!r}")
    assert "inner.docx" in marked_by_record[deepest][0], (
        f"the deepest record's marked note must name what was not "
        f"expanded ('inner.docx'): {marked_by_record[deepest]!r}")
    for rel_path in expected_chain[:-1]:
        assert marked_by_record[rel_path] == [], (
            f"only the DEEPEST record may carry a marked note about the "
            f"depth cap; {rel_path!r} unexpectedly carries "
            f"{marked_by_record[rel_path]!r}")


# ---------------------------------------------------------------------------
# 6. Regression guard: page accounting still reconciles over the whole corpus
# ---------------------------------------------------------------------------


def test_pipeline_page_accounting_reconciles_over_the_fixture_corpus(tmp_path):
    """A guard, not a proof of recovery: accounting balances against what
    the walk produced, so it passed before embedded documents were recovered
    too. It holds that nested children do not break the reconciliation."""
    from dociq import pipeline
    from dociq.operator import OperatorStamp

    cfg = RunConfig(source_root=str(FIXTURES), output_root=str(tmp_path / "out"),
                    ocr_engine_version=ex.ocr_engine_version())
    outcome = pipeline.run(cfg, pipeline.PipelineOptions(
        walk=walker.WalkOptions(ocr_enabled=False, resume=False),
        matter_name="fixture corpus",
        master_index=None,
        stamp=OperatorStamp("test", "2026-09-14T00:00:00Z", "test-host")))
    assert outcome.accounting.ok, outcome.accounting.render()


# ---------------------------------------------------------------------------
# 7. Unwrap by what the bytes ARE, not by ProgID alone
# ---------------------------------------------------------------------------


def test_unwrap_is_keyed_on_bytes_not_on_progid(tmp_path):
    """Every fixture 17 object's ProgID happens to match its bytes, so a
    dispatcher keyed on ProgID alone would pass fixture 17 too -- this
    mismatches them on purpose. The corpus carries PDFs under four Acrobat
    ProgIDs (Word spec, embedded-objects rows of the construct table), so a
    PDF under a ProgID other than 'AcroExch.Document.DC' must still be
    recovered."""
    xlsx_bytes = make_fixtures._we_xlsx_bytes(
        tmp_path / "unused.xlsx", "SERVAL-PROGID-MISMATCH")
    pdf_bytes = _minimal_pdf_bytes()
    compound = make_fixtures._write_compound_file({"CONTENTS": pdf_bytes})
    raw = _minimal_docx_with_embeds([
        ("Acrobat.Document.2020", "word/embeddings/oleObject1.bin", compound),
        ("Package", "word/embeddings/Sheet2.xlsx", xlsx_bytes),
    ])

    exp = ex.expand_docx_embeddings(raw)
    names = {m.name for m in exp.members}
    assert "oleObject1.pdf" in names, (
        f"a PDF-carrying compound file must be recovered whatever ProgID it "
        f"is labelled with (here 'Acrobat.Document.2020', not "
        f"'AcroExch.Document.DC'): {exp.members!r}")
    assert "Sheet2.xlsx" in names, (
        f"an Office-file part must be recovered whatever ProgID it is "
        f"labelled with (here 'Package', normally a wrapped-attachment "
        f"label): {exp.members!r}")


# ---------------------------------------------------------------------------
# 8. Object order: body, then aux parts in part-name order (each through its
# OWN .rels), then the unreferenced-part sweep; each part read once
# ---------------------------------------------------------------------------


def test_object_order_body_then_aux_parts_each_own_rels_dedup(tmp_path):
    """A part referenced from the body, the footer and the footnotes, with
    colliding r:id strings resolved through each part's OWN relationships.

    The part NAMES are chosen so that the order the unreferenced-part sweep
    would produce on its own (name order: a_orphan, b_footer, m_body,
    z_footnote) differs from the required one. With plainer names the sweep
    recovered the footer's and footnotes' objects in the expected order
    anyway, so a reader that skipped every header, footer and footnote part
    (or only the footnote and endnote parts) passed this test."""
    body_bytes = make_fixtures._we_xlsx_bytes(tmp_path / "body.xlsx", "BODY-OBJ")
    footer_bytes = make_fixtures._we_xlsx_bytes(tmp_path / "footer.xlsx", "FOOTER-OBJ")
    footnote_bytes = make_fixtures._we_xlsx_bytes(tmp_path / "fn.xlsx", "FOOTNOTE-OBJ")
    orphan_bytes = make_fixtures._we_xlsx_bytes(tmp_path / "orphan.xlsx", "ORPHAN-OBJ")
    raw = _docx_with_aux_objects(
        body_part="word/embeddings/m_body.xlsx",
        footer_part="word/embeddings/b_footer.xlsx",
        # The second target is the SAME part as the body object -- read once.
        footnote_target_parts=["word/embeddings/z_footnote.xlsx",
                               "word/embeddings/m_body.xlsx"],
        body_bytes=body_bytes, footer_bytes=footer_bytes,
        extra_parts={"word/embeddings/z_footnote.xlsx": footnote_bytes,
                     "word/embeddings/a_orphan.xlsx": orphan_bytes})

    exp = ex.expand_docx_embeddings(raw)
    got = [(m.name, m.order) for m in exp.members]
    assert got == [("m_body.xlsx", 0), ("b_footer.xlsx", 1),
                   ("z_footnote.xlsx", 2), ("a_orphan.xlsx", 3)], (
        f"expected the body's object, then the footer's (word/footer1.xml "
        f"sorts before word/footnotes.xml), then the footnote's, then the "
        f"part nothing references; the footnote's second reference to the "
        f"body's part adds nothing: {got!r}")


# ---------------------------------------------------------------------------
# 9. Unreferenced word/embeddings/ parts: after every
# referenced object, in name order -- not merely alphabetical over all parts
# ---------------------------------------------------------------------------


def test_unreferenced_embeddings_parts_come_after_referenced_in_name_order(tmp_path):
    ref1 = make_fixtures._we_xlsx_bytes(tmp_path / "ref1.xlsx", "REF1")
    ref2 = make_fixtures._we_xlsx_bytes(tmp_path / "ref2.xlsx", "REF2")
    unref_a = make_fixtures._we_xlsx_bytes(tmp_path / "unref_a.xlsx", "UNREFA")
    unref_b = make_fixtures._we_xlsx_bytes(tmp_path / "unref_b.xlsx", "UNREFB")
    raw = _minimal_docx_with_embeds([
        ("Excel.Sheet.12", "word/embeddings/z_ref1.xlsx", ref1),
        ("Excel.Sheet.12", "word/embeddings/z_ref2.xlsx", ref2),
    ])
    # These two part NAMES sort alphabetically BEFORE both referenced parts
    # -- if the sweep were merely "every embeddings part in name order" they
    # would come first; they must not.
    raw = _add_zip_parts(raw, {
        "word/embeddings/a_unref_second.xlsx": unref_b,
        "word/embeddings/a_unref_first.xlsx": unref_a,
    })

    exp = ex.expand_docx_embeddings(raw)
    got = [m.name for m in exp.members]
    assert got == ["z_ref1.xlsx", "z_ref2.xlsx", "a_unref_first.xlsx",
                   "a_unref_second.xlsx"], (
        f"expected referenced objects first (document order), THEN "
        f"unreferenced parts (name order): {got!r}")


# ---------------------------------------------------------------------------
# 10. Link / missing r:id / external target / a
# relationship to a part not in the package: each a marked note, no child
# ---------------------------------------------------------------------------


def test_link_type_object_is_not_recovered(tmp_path):
    object_xml = ('<o:OLEObject Type="Link" ProgID="Excel.Sheet.12" '
                 'r:id="rIdEmbed1"/>')
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
        '2006/relationships"><Relationship Id="rIdEmbed1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/oleObject" Target="embeddings/linked.xlsx"/>'
        '</Relationships>')
    raw = _docx_with_raw_object(object_xml, rels)
    exp = ex.expand_docx_embeddings(raw)
    assert exp.members == (), exp.members
    marked = [n for n in exp.notes if ex.has_evidence_marker(n)]
    assert len(marked) == 1 and "word/document.xml" in marked[0], (
        f"a Type=Link object is never resolved (it names no stored part), "
        f"so the note names the owning part instead: {exp.notes!r}")


def test_missing_relationship_id_is_not_recovered(tmp_path):
    object_xml = '<o:OLEObject Type="Embed" ProgID="Excel.Sheet.12"/>'
    raw = _docx_with_raw_object(object_xml)
    exp = ex.expand_docx_embeddings(raw)
    assert exp.members == (), exp.members
    marked = [n for n in exp.notes if ex.has_evidence_marker(n)]
    assert len(marked) == 1 and "word/document.xml" in marked[0], (
        f"an <o:OLEObject> with no r:id has nothing to resolve: {exp.notes!r}")


def test_external_target_is_not_recovered(tmp_path):
    object_xml = ('<o:OLEObject Type="Embed" ProgID="Excel.Sheet.12" '
                 'r:id="rIdEmbed1"/>')
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
        '2006/relationships"><Relationship Id="rIdEmbed1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/oleObject" '
        'Target="http://example.com/thing.xlsx" TargetMode="External"/>'
        '</Relationships>')
    raw = _docx_with_raw_object(object_xml, rels)
    exp = ex.expand_docx_embeddings(raw)
    assert exp.members == (), exp.members
    marked = [n for n in exp.notes if ex.has_evidence_marker(n)]
    assert any("http://example.com/thing.xlsx" in n for n in marked), (
        f"expected a marked note naming the external target: {exp.notes!r}")


def test_relationship_to_missing_part_is_not_recovered(tmp_path):
    object_xml = ('<o:OLEObject Type="Embed" ProgID="Excel.Sheet.12" '
                 'r:id="rIdEmbed1"/>')
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
        '2006/relationships"><Relationship Id="rIdEmbed1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/oleObject" Target="embeddings/missing.xlsx"/>'
        '</Relationships>')
    raw = _docx_with_raw_object(object_xml, rels)  # note: part never added
    exp = ex.expand_docx_embeddings(raw)
    assert exp.members == (), exp.members
    marked = [n for n in exp.notes if ex.has_evidence_marker(n)]
    assert any("word/embeddings/missing.xlsx" in n for n in marked), (
        f"expected a marked note naming the part that is not in the "
        f"package: {exp.notes!r}")


# ---------------------------------------------------------------------------
# 11. A legacy-Office compound file (neither PDF
# CONTENTS nor Ole10Native) is kept as stored, and the walker marks it Tier 2
# ---------------------------------------------------------------------------


def test_legacy_compound_file_kept_as_stored_and_marked_tier2(tmp_path):
    ppt_compound = make_fixtures._write_compound_file(
        {"PowerPoint Document": b"INVENTED-LEGACY-PPT-STREAM-BYTES"})
    part_name = "word/embeddings/Microsoft_PowerPoint_97-2003_Presentation1.ppt"
    raw = _minimal_docx_with_embeds([("PowerPoint.Show.8", part_name, ppt_compound)])

    exp = ex.expand_docx_embeddings(raw)
    assert len(exp.members) == 1, exp.members
    member = exp.members[0]
    assert member.name == "Microsoft_PowerPoint_97-2003_Presentation1.ppt", member.name
    assert member.raw == ppt_compound, (
        "a legacy compound-file Office part (neither a PDF-carrying "
        "CONTENTS stream nor an Ole10Native wrapper) must be kept AS "
        "STORED, byte for byte -- the walker's own Tier split decides its "
        "fate from there")

    src = tmp_path / "src"
    src.mkdir()
    (src / "legacy.docx").write_bytes(raw)
    r = walker.run(_cfg(tmp_path, src),
                   walker.WalkOptions(ocr_enabled=False, resume=False))
    # A Tier-2 container child is swept onto r.unsupported by walker.run
    # (§3: "a Tier-2 file inside a container is still a Tier-2 file"),
    # exactly like a Tier-2 zip member (test_walker.py::
    # test_a_tier2_archive_member_lands_on_the_unsupported_list) -- never
    # left on r.documents.
    child = next((d for d in r.unsupported if d.rel_path.endswith(".ppt")), None)
    assert child is not None, (
        f"expected a Tier-2 record for the stored .ppt part: "
        f"{[d.rel_path for d in r.unsupported]!r}")
    assert child.status == ProcessingStatus.UNSUPPORTED, (
        f"a legacy .ppt child must be marked Tier 2 (UNSUPPORTED): "
        f"{child.status!r}")


# ---------------------------------------------------------------------------
# 12. An Ole10Native stream with no stored filename
# names its child "<part stem>.bin"
# ---------------------------------------------------------------------------


def test_ole10native_empty_filename_falls_back_to_part_stem(tmp_path):
    compound = make_fixtures._write_compound_file(
        {"\x01Ole10Native": make_fixtures._ole10_native(
            "", b"EMPTY-FILENAME-PAYLOAD")})
    raw = _minimal_docx_with_embeds(
        [("Package", "word/embeddings/oleObject1.bin", compound)])

    exp = ex.expand_docx_embeddings(raw)
    assert len(exp.members) == 1, exp.members
    assert exp.members[0].name == "oleObject1.bin", exp.members[0].name


# ---------------------------------------------------------------------------
# 13. Every Ole10Native read is bounds-checked,
# including the DATA-length read (5a only ever reaches the temp-path check)
# ---------------------------------------------------------------------------


def test_ole10native_data_length_bounds_checked_not_silently_truncated(tmp_path):
    compound = make_fixtures._write_compound_file(
        {"\x01Ole10Native": _malformed_ole10_native_bad_data_length()})
    raw = _minimal_docx_with_embeds(
        [("Package", "word/embeddings/oleObject1.bin", compound)])

    exp = ex.expand_docx_embeddings(raw)
    assert exp.members == (), (
        f"an oversized declared data length must not silently truncate to "
        f"whatever bytes happen to follow it: {exp.members!r}")
    marked = [n for n in exp.notes if ex.has_evidence_marker(n)]
    assert any("oleObject1.bin" in n and "Ole10Native" in n for n in marked), (
        f"expected a marked note naming the part and the malformed stream: "
        f"{exp.notes!r}")


# ---------------------------------------------------------------------------
# 14. Member-count and total-bytes caps on embeddings
# are disclosed when they bite, mirroring expand_zip's own caps
# ---------------------------------------------------------------------------


def test_embeddings_member_cap_is_disclosed(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "_ZIP_MAX_MEMBERS", 2)
    embeds = [("Excel.Sheet.12", f"word/embeddings/obj{i}.xlsx",
              make_fixtures._we_xlsx_bytes(tmp_path / f"o{i}.xlsx", f"CAP-{i}"))
             for i in range(3)]
    raw = _minimal_docx_with_embeds(embeds)

    exp = ex.expand_docx_embeddings(raw)
    assert len(exp.members) == 2, exp.members
    marked = [n for n in exp.notes if ex.has_evidence_marker(n)]
    assert any("2" in n and "member" in n for n in marked), (
        f"expected a marked note disclosing the member-count truncation: "
        f"{exp.notes!r}")


def test_embeddings_byte_cap_is_disclosed(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "_ZIP_MAX_MB", 0)
    xlsx_bytes = make_fixtures._we_xlsx_bytes(tmp_path / "o.xlsx", "CAP-BYTES")
    raw = _minimal_docx_with_embeds(
        [("Excel.Sheet.12", "word/embeddings/obj.xlsx", xlsx_bytes)])

    exp = ex.expand_docx_embeddings(raw)
    assert exp.members == (), exp.members
    marked = [n for n in exp.notes if ex.has_evidence_marker(n)]
    assert any("MB" in n for n in marked), (
        f"expected a marked note disclosing the byte-cap truncation: "
        f"{exp.notes!r}")


# ---------------------------------------------------------------------------
# 15. If expand_docx_embeddings raises, the walker
# marks the Word record rather than failing the whole file
# ---------------------------------------------------------------------------


def test_walker_marks_the_word_record_when_expansion_raises(tmp_path, monkeypatch):
    def _raise(raw):
        raise RuntimeError("INVENTED-EXPANSION-FAILURE")

    monkeypatch.setattr(ex, "expand_docx_embeddings", _raise)
    r = _walk_single_fixture(tmp_path, "17_word_embeddings.docx")
    parent = next(d for d in r.documents if d.rel_path == "17_word_embeddings.docx")
    assert parent.pages, (
        "the Word file's own record must keep its own page even when "
        "expansion raises")
    marked = [n for n in parent.notes if ex.has_evidence_marker(n)]
    assert marked, (
        f"an exception out of expand_docx_embeddings must leave a marked "
        f"note on the Word file's own record, not fail the file: "
        f"{parent.notes!r}")


# ---------------------------------------------------------------------------
# 16. Recursion covers an .eml inside a .zip and an .eml attached to an
# .eml -- the silent loss that predates D-50 (.msg: see section 21)
# ---------------------------------------------------------------------------


def test_recursion_covers_eml_inside_a_zip(tmp_path):
    inner_eml = _small_eml_bytes(
        "ZIP-WRAPPED-EMAIL", "OCELOT-ZIP-EML-BODY",
        attachment=("leaf.txt", "OCELOT-ZIP-EML-ATTACHMENT"))
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("wrapped.eml", inner_eml)

    src = tmp_path / "src"
    src.mkdir()
    (src / "archive.zip").write_bytes(zip_buf.getvalue())
    r = walker.run(_cfg(tmp_path, src),
                   walker.WalkOptions(ocr_enabled=False, resume=False))

    eml_child = next(
        (d for d in r.documents if d.rel_path == "archive.zip/wrapped.eml"), None)
    assert eml_child is not None, (
        f"expected the zipped .eml as a child of the archive: "
        f"{[d.rel_path for d in r.documents]!r}")
    grandkid = next(
        (d for d in r.documents if d.parent_doc_id == eml_child.rel_path), None)
    assert grandkid is not None, (
        "expected the .eml's OWN attachment to be recovered as ITS child -- "
        "a .msg/.eml inside an archive never had its own attachments read "
        "before D-50")
    assert grandkid.rel_path == "archive.zip/wrapped.eml/leaf.txt", grandkid.rel_path
    assert "OCELOT-ZIP-EML-ATTACHMENT" in _text(grandkid), _text(grandkid)


def test_recursion_covers_eml_attached_to_eml(tmp_path):
    innermost = _small_eml_bytes(
        "INNERMOST-EMAIL", "MARGAY-INNERMOST-BODY",
        attachment=("leaf.txt", "MARGAY-INNERMOST-ATTACHMENT"))
    # _small_eml_bytes base64-encodes a SENTINEL STRING as the attachment
    # payload; here the payload must be the INNERMOST .eml's raw BYTES, so
    # the outer email is built by hand instead of through that helper.
    import base64
    att_b64 = base64.b64encode(innermost).decode("ascii")
    outer = "\r\n".join([
        "From: engineer@example.com", "To: contractor@example.com",
        "Subject: OUTER-EMAIL-WITH-EMAIL-ATTACHMENT",
        "Date: Fri, 19 Jul 2024 09:00:00 +0000", "MIME-Version: 1.0",
        'Content-Type: multipart/mixed; boundary="dociq-outer-boundary"',
        "", "--dociq-outer-boundary",
        "Content-Type: text/plain; charset=utf-8", "", "MARGAY-OUTER-BODY", "",
        "--dociq-outer-boundary",
        'Content-Type: application/octet-stream; name="inner.eml"',
        "Content-Transfer-Encoding: base64",
        'Content-Disposition: attachment; filename="inner.eml"',
        "", att_b64, "", "--dociq-outer-boundary--", "",
    ]).encode("utf-8")

    src = tmp_path / "src"
    src.mkdir()
    (src / "outer.eml").write_bytes(outer)
    r = walker.run(_cfg(tmp_path, src),
                   walker.WalkOptions(ocr_enabled=False, resume=False))

    inner_child = next(
        (d for d in r.documents if d.rel_path == "outer.eml/inner.eml"), None)
    assert inner_child is not None, (
        f"expected the attached .eml as a child of the outer email: "
        f"{[d.rel_path for d in r.documents]!r}")
    grandkid = next(
        (d for d in r.documents if d.parent_doc_id == inner_child.rel_path), None)
    assert grandkid is not None, (
        "expected the attached .eml's OWN attachment to be recovered as "
        "ITS child")
    assert grandkid.rel_path == "outer.eml/inner.eml/leaf.txt", grandkid.rel_path
    assert "MARGAY-INNERMOST-ATTACHMENT" in _text(grandkid), _text(grandkid)


# ---------------------------------------------------------------------------
# 17. "N embedded document(s) extracted as child
# document(s)" on the Word record, present only when something was recovered
# ---------------------------------------------------------------------------


def test_embedded_document_count_note_present_when_recovered(tmp_path):
    r = _walk_single_fixture(tmp_path, "17_word_embeddings.docx")
    parent = next(d for d in r.documents if d.rel_path == "17_word_embeddings.docx")
    assert "4 embedded document(s) extracted as child document(s)" in parent.notes, (
        f"all notes: {parent.notes!r}")


def test_no_embedded_document_count_note_when_nothing_recovered(tmp_path):
    r = _walk_single_fixture(tmp_path, "16_word_constructs.docx")
    parent = next(d for d in r.documents if d.rel_path == "16_word_constructs.docx")
    assert not any("embedded document(s) extracted as child document(s)" in n
                  for n in parent.notes), (
        f"fixture 16's placeholder object recovers nothing; no count note "
        f"is owed: {parent.notes!r}")


# ---------------------------------------------------------------------------
# 18. A stored filename is untrusted: made safe before it becomes
# part of a rel_path, whatever container kind supplied it.
# ---------------------------------------------------------------------------

# Expected leaf for the shapes where the transformation is exact and worth
# pinning precisely rather than only by the generic invariants below.
_UNTRUSTED_EXACT_LEAF = {
    "drive_absolute": "evil.txt",       # only the final path component survives
    "windows_traversal": "evil.txt",    # '..' segments are discarded, not walked
    "trailing_dots_spaces": "notice.eml",  # Windows would strip these silently
    # A stored filename with no usable basename names the child after its
    # part, "<part stem>.bin" (expand_docx_embeddings' docstring), the same
    # as an empty one -- not the sanitizer's generic placeholder.
    "dotdot": "oleObject1.bin",
    "drive_root": "oleObject1.bin",
    "blank": "oleObject1.bin",
}


@pytest.mark.parametrize("stored_name", [
    "", ".", "..", "../../evil.txt", "..\\..\\evil.txt",
    "C:\\Windows\\evil.txt", "C:evil.txt", "CON", "CON.txt", "COM1.log",
    "notice.eml.   ",
], ids=["empty", "dot", "dotdot", "posix_traversal", "windows_traversal",
       "drive_absolute", "drive_relative", "reserved_bare",
       "reserved_with_ext", "reserved_device", "trailing_dots_spaces"])
def test_untrusted_ole10native_stored_filename_is_sanitized(tmp_path, stored_name):
    """An ``\\x01Ole10Native`` stored filename is untrusted. Before
    the walker made stored names safe (now ``walker._child_names``), ``_child_records`` built
    ``child_rel`` from ``unicodedata.normalize('NFC', m.name)`` alone --
    empty, '.', '..', a path-carrying name, a drive letter or ':', trailing
    dots/spaces, and a Windows-reserved device name all passed straight
    into the record's ``rel_path`` unexamined.

    Every shape here must walk WITHOUT error and produce a child whose
    rel_path is exactly one safe path segment below the parent -- never
    escaping it, never a bare '.'/'..', never carrying '/' '\\\\' or ':',
    never a Windows-reserved stem, never ending in a space or a dot -- and
    Stage 3b Doc ID assignment over the result must issue no warnings.
    """
    payload = f"SENTINEL-FOR-{stored_name!r}".encode()
    compound = make_fixtures._write_compound_file({
        "\x01Ole10Native": make_fixtures._ole10_native(stored_name, payload)})
    raw = _minimal_docx_with_embeds(
        [("Package", "word/embeddings/oleObject1.bin", compound)])

    src = tmp_path / "src"
    src.mkdir()
    (src / "untrusted.docx").write_bytes(raw)
    r = walker.run(_cfg(tmp_path, src),
                   walker.WalkOptions(ocr_enabled=False, resume=False))

    parent_rel = "untrusted.docx"
    all_records = list(r.documents) + list(r.unsupported)
    children = [d for d in all_records if d.parent_doc_id == parent_rel]
    assert len(children) == 1, (
        f"expected exactly one recovered child for stored name "
        f"{stored_name!r}: {[(c.rel_path, c.status.value) for c in all_records]!r}")
    child = children[0]
    assert child.rel_path.startswith(parent_rel + "/"), child.rel_path
    leaf = child.rel_path[len(parent_rel) + 1:]
    assert "/" not in leaf and "\\" not in leaf, (
        f"a sanitized leaf must be exactly one path segment: {leaf!r}")
    assert leaf not in ("", ".", ".."), leaf
    assert ":" not in leaf, leaf
    assert not leaf.endswith(" ") and not leaf.endswith("."), leaf
    stem = leaf.split(".", 1)[0].lower()
    assert stem not in walker._WINDOWS_RESERVED_STEMS, leaf

    from dociq.docid import assign
    result = assign.assign_doc_ids(r.documents + r.unsupported, index=None)
    assert result.warnings == (), (
        f"Stage 3b Doc ID assignment must issue no warnings over a "
        f"sanitized child ({stored_name!r} -> {leaf!r}): {result.warnings!r}")


@pytest.mark.parametrize("case_id,stored_name", [
    ("drive_absolute", "C:\\Windows\\evil.txt"),
    ("windows_traversal", "..\\..\\evil.txt"),
    ("trailing_dots_spaces", "notice.eml.   "),
    ("dotdot", ".."),
    ("drive_root", "C:\\"),
    ("blank", "   "),
])
def test_untrusted_ole10native_stored_filename_exact_leaf(
        tmp_path, case_id, stored_name):
    """The subset of :func:`test_untrusted_ole10native_stored_filename_is_sanitized`
    whose expected leaf is exact and deterministic, not merely safe -- pinned
    separately so a fix that satisfies only the generic invariants (e.g. by
    renaming every shape to a bare fallback) is still caught."""
    compound = make_fixtures._write_compound_file({
        "\x01Ole10Native": make_fixtures._ole10_native(
            stored_name, b"EXACT-LEAF-PAYLOAD")})
    raw = _minimal_docx_with_embeds(
        [("Package", "word/embeddings/oleObject1.bin", compound)])

    src = tmp_path / "src"
    src.mkdir()
    (src / "exactleaf.docx").write_bytes(raw)
    r = walker.run(_cfg(tmp_path, src),
                   walker.WalkOptions(ocr_enabled=False, resume=False))

    all_records = list(r.documents) + list(r.unsupported)
    expected = f"exactleaf.docx/{_UNTRUSTED_EXACT_LEAF[case_id]}"
    assert any(d.rel_path == expected for d in all_records), (
        f"expected rel_path {expected!r} for stored name {stored_name!r}: "
        f"{[d.rel_path for d in all_records]!r}")


# ---------------------------------------------------------------------------
# 19. Two children of one parent with the same name: disambiguated
# deterministically by container order, for every container kind.
# ---------------------------------------------------------------------------


def test_two_embedded_objects_with_the_same_stored_name_get_distinct_rel_paths(
        tmp_path):
    """Two children of one parent with the same name: two Package objects,
    each wrapping a plainly-named 'duplicate.txt'. Before
    walker._dedup_child_names existed, both children's rel_path was
    built from the raw stored name with no dedup, so the second SILENTLY
    replaced the first as far as any rel_path-keyed lookup (Stage 3b Doc ID
    assignment, the resume journal) was concerned.
    """
    compound_a = make_fixtures._write_compound_file({
        "\x01Ole10Native": make_fixtures._ole10_native(
            "duplicate.txt", b"OKAPI-FIRST-DUPLICATE")})
    compound_b = make_fixtures._write_compound_file({
        "\x01Ole10Native": make_fixtures._ole10_native(
            "duplicate.txt", b"MARMOT-SECOND-DUPLICATE")})
    raw = _minimal_docx_with_embeds([
        ("Package", "word/embeddings/oleObject1.bin", compound_a),
        ("Package", "word/embeddings/oleObject2.bin", compound_b),
    ])

    src = tmp_path / "src"
    src.mkdir()
    (src / "dup.docx").write_bytes(raw)
    r = walker.run(_cfg(tmp_path, src),
                   walker.WalkOptions(ocr_enabled=False, resume=False))

    parent_rel = "dup.docx"
    children = [d for d in r.documents if d.parent_doc_id == parent_rel]
    assert len(children) == 2, [c.rel_path for c in children]
    rel_paths = [c.rel_path for c in children]
    assert len(set(rel_paths)) == 2, (
        f"two same-named children must get DISTINCT rel_paths: {rel_paths!r}")

    by_order = {c.container_order: c for c in children}
    assert "OKAPI-FIRST-DUPLICATE" in _text(by_order[0]), _text(by_order[0])
    assert "MARMOT-SECOND-DUPLICATE" in _text(by_order[1]), _text(by_order[1])
    assert "OKAPI-FIRST-DUPLICATE" not in _text(by_order[1]), (
        "the second child's text must not be the first's -- a collided "
        "rel_path would make the walker's own extraction of the SECOND "
        "member overwrite the first's record under one key")
    assert "MARMOT-SECOND-DUPLICATE" not in _text(by_order[0])

    from dociq.docid import assign
    result = assign.assign_doc_ids(r.documents, index=None)
    assert result.warnings == (), result.warnings
    doc_ids = [d.doc_id for d in result.documents]
    assert len(doc_ids) == len(set(doc_ids)), doc_ids


def test_two_email_attachments_with_the_same_name_get_distinct_rel_paths(
        tmp_path):
    """The other half: ``expand_eml_attachments`` reads
    ``part.get_filename()`` unsanitized,
    and two attachments sharing a name -- 'duplicate.txt' pasted twice, a
    common real-world shape -- collided the same way object 19's Package
    objects did, on the SAME code path (``_child_records``) before this fix.
    One dedup, applied once where every child's ``rel_path`` is built,
    closes it for every container kind, not only D-50's new Ole10Native one.
    """
    import base64
    boundary = "dociq-dup-eml-boundary"
    att_b64_a = base64.b64encode(b"IBEX-FIRST-ATTACHMENT").decode("ascii")
    att_b64_b = base64.b64encode(b"ORYX-SECOND-ATTACHMENT").decode("ascii")
    raw = "\r\n".join([
        "From: engineer@example.com", "To: contractor@example.com",
        "Subject: TWO-ATTACHMENTS-SAME-NAME",
        "Date: Fri, 19 Jul 2024 09:00:00 +0000", "MIME-Version: 1.0",
        f'Content-Type: multipart/mixed; boundary="{boundary}"',
        "", f"--{boundary}",
        "Content-Type: text/plain; charset=utf-8", "", "body text", "",
        f"--{boundary}",
        'Content-Type: text/plain; name="duplicate.txt"',
        "Content-Transfer-Encoding: base64",
        'Content-Disposition: attachment; filename="duplicate.txt"',
        "", att_b64_a, "",
        f"--{boundary}",
        'Content-Type: text/plain; name="duplicate.txt"',
        "Content-Transfer-Encoding: base64",
        'Content-Disposition: attachment; filename="duplicate.txt"',
        "", att_b64_b, "", f"--{boundary}--", "",
    ]).encode("utf-8")

    src = tmp_path / "src"
    src.mkdir()
    (src / "dup_attach.eml").write_bytes(raw)
    r = walker.run(_cfg(tmp_path, src),
                   walker.WalkOptions(ocr_enabled=False, resume=False))

    parent_rel = "dup_attach.eml"
    children = [d for d in r.documents if d.parent_doc_id == parent_rel]
    assert len(children) == 2, [c.rel_path for c in children]
    rel_paths = [c.rel_path for c in children]
    assert len(set(rel_paths)) == 2, (
        f"two same-named attachments must get DISTINCT rel_paths: {rel_paths!r}")

    by_order = {c.container_order: c for c in children}
    assert "IBEX-FIRST-ATTACHMENT" in _text(by_order[0]), _text(by_order[0])
    assert "ORYX-SECOND-ATTACHMENT" in _text(by_order[1]), _text(by_order[1])

    from dociq.docid import assign
    result = assign.assign_doc_ids(r.documents, index=None)
    assert result.warnings == (), result.warnings


# ---------------------------------------------------------------------------
# Shared builders for the sections below
# ---------------------------------------------------------------------------

_OBJ_CT = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Default Extension="bin" '
    'ContentType="application/vnd.openxmlformats-officedocument.oleObject"/>'
    '<Default Extension="docx" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document"/>'
    '<Default Extension="xlsx" '
    'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"/>'
    '<Default Extension="doc" ContentType="application/msword"/>'
    '<Override PartName="/word/document.xml" ContentType='
    '"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '</Types>'
)


def _docx_objects(objects, *, body_text: str = "an invented paragraph",
                  extra_parts: dict | None = None, extra_rels=()) -> bytes:
    """A .docx whose body holds one ``<o:OLEObject>`` per ``objects`` entry,
    ``(Type, ProgID, relationship Target, part bytes or None)``, in order.
    The Target is written exactly as given (relative to ``word/``, or
    package-absolute with a leading ``/``); bytes are stored at the part the
    Target resolves to, and ``None`` stores nothing (a link).
    ``extra_parts`` are stored as given; ``extra_rels`` are more main-part
    relationships, ``(id, type, target)``. Unlike
    ``_minimal_docx_with_embeds``, the content types cover every part, so
    python-docx opens the result and the Word file's own text is read."""
    import posixpath

    objs, rels, parts = [], [], {}
    for rid, rtype, target in extra_rels:
        rels.append(f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/'
                    f'officeDocument/2006/relationships/{rtype}" Target="{target}"/>')
    for i, (typ, progid, target, data) in enumerate(objects, start=1):
        rid = f"rIdObj{i}"
        objs.append(
            f'<w:p><w:r><w:object><o:OLEObject Type="{typ}" ProgID="{progid}" '
            f'r:id="{rid}"/></w:object></w:r></w:p>')
        rels.append(
            f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/'
            f'officeDocument/2006/relationships/oleObject" Target="{target}"'
            + (' TargetMode="External"' if typ == "Link" else "") + '/>')
        if data is not None:
            name = (target.lstrip("/") if target.startswith("/")
                    else posixpath.normpath(posixpath.join("word", target)))
            parts[name] = data
    doc_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document {_MIN_NS}><w:body>'
        f'<w:p><w:r><w:t>{body_text}</w:t></w:r></w:p>' + "".join(objs)
        + '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/></w:sectPr>'
        '</w:body></w:document>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _OBJ_CT)
        zf.writestr("_rels/.rels", _MIN_ROOT_RELS)
        zf.writestr("word/document.xml", doc_xml)
        zf.writestr(
            "word/_rels/document.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
            '2006/relationships">' + "".join(rels) + '</Relationships>')
        for name, data in {**parts, **(extra_parts or {})}.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _walk_files(tmp_path, files: dict, notes=None):
    src = tmp_path / "src"
    src.mkdir()
    for name, data in files.items():
        (src / name).parent.mkdir(parents=True, exist_ok=True)
        (src / name).write_bytes(data)
    return walker.run(_cfg(tmp_path, src),
                      walker.WalkOptions(ocr_enabled=False, resume=False), notes)


def _package(stored_name: str, payload: bytes) -> bytes:
    return make_fixtures._write_compound_file(
        {"\x01Ole10Native": make_fixtures._ole10_native(stored_name, payload)})


def _eml_attaching(name: str, payload: bytes) -> bytes:
    import base64

    return "\r\n".join([
        "From: engineer@example.com", "To: contractor@example.com",
        "Subject: INVENTED-ATTACHMENT-CARRIER",
        "Date: Fri, 19 Jul 2024 09:00:00 +0000", "MIME-Version: 1.0",
        'Content-Type: multipart/mixed; boundary="dociq-carrier-boundary"',
        "", "--dociq-carrier-boundary",
        "Content-Type: text/plain; charset=utf-8", "", "carrier body", "",
        "--dociq-carrier-boundary",
        f'Content-Type: application/octet-stream; name="{name}"',
        "Content-Transfer-Encoding: base64",
        f'Content-Disposition: attachment; filename="{name}"',
        "", base64.b64encode(payload).decode("ascii"), "",
        "--dociq-carrier-boundary--", "",
    ]).encode("utf-8")


def _marked(doc) -> list[str]:
    return [n for n in doc.notes if ex.has_evidence_marker(n)]


# ---------------------------------------------------------------------------
# 20. A PDF child keeps its section recognition wherever it is nested
# ---------------------------------------------------------------------------


def test_nested_pdf_keeps_its_section_and_is_dropped_under_an_approval(tmp_path):
    """The walker rebuilt every container child's extraction result field by
    field and left out ``spans``, so a PDF inside a zip, an email or a Word
    file lost section recognition, and an approved omission kept every one
    of its pages while the same PDF at the top level dropped one. Page 2 of
    fixture 03 is a photograph page with OCR off
    (``tests/test_codex_r2_findings.py::_PHOTOGRAPH_PAGES``)."""
    from dociq import pipeline
    from dociq.contracts import Disposition, matter_key
    from dociq.sections.model import ApprovedOmission
    from dociq.sections.templates import PROGRESS_REPORT

    pdf = (FIXTURES / "03_mixed_transmittal.pdf").read_bytes()
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("vol1/03_mixed_transmittal.pdf", pdf)
    acrobat = make_fixtures._write_compound_file({"CONTENTS": pdf})
    files = {
        "03_mixed_transmittal.pdf": pdf,
        "bundle.zip": zip_buf.getvalue(),
        "mail.eml": _eml_attaching("03_mixed_transmittal.pdf", pdf),
        "memo.docx": _docx_objects(
            [("Embed", "AcroExch.Document.DC", "embeddings/oleObject1.bin", acrobat)]),
    }
    src = tmp_path / "src"
    src.mkdir()
    for name, data in files.items():
        (src / name).write_bytes(data)
    approval = ApprovedOmission(
        family_id="progress-photographs", approved_by="abachowski",
        approved_at="2026-09-14T12:00:00Z", matter="nested",
        matter_root=matter_key(str(src)),
        template_id=PROGRESS_REPORT.template_id,
        template_version=PROGRESS_REPORT.version)
    outcome = pipeline.run(_cfg(tmp_path, src), pipeline.PipelineOptions(
        walk=walker.WalkOptions(ocr_enabled=False, resume=False),
        template=PROGRESS_REPORT, approvals=(approval,), matter_name="nested"))

    def shape(rel_path):
        doc = next((d for d in outcome.result.documents if d.rel_path == rel_path), None)
        assert doc is not None, (
            f"no record {rel_path!r}: {[d.rel_path for d in outcome.result.documents]!r}")
        return [(p.section, p.section_tier, p.disposition) for p in doc.pages]

    top = shape("03_mixed_transmittal.pdf")
    assert top[1][2] is Disposition.DROP and top[1][0], (
        f"the top-level copy's page 2 must be placed and dropped, or this test "
        f"proves nothing: {top!r}")
    for nested in ("bundle.zip/vol1/03_mixed_transmittal.pdf",
                   "mail.eml/03_mixed_transmittal.pdf",
                   "memo.docx/oleObject1.pdf"):
        assert shape(nested) == top, (
            f"{nested!r} must carry the same section, tier and disposition on "
            f"every page as the top-level copy: {shape(nested)!r} != {top!r}")


def test_every_contract_record_rebuild_names_every_field():
    """The class guard for the loss above: a record rebuilt from another
    record's fields one keyword at a time silently drops any field the call
    does not name, including one added to the dataclass later.

    Every call in ``src/`` that builds an ``ExtractedDoc``, a
    ``DocumentRecord`` or a ``PageRecord`` is read. A call that copies at
    least two same-named fields from one object (``status=got.status``,
    ``doc_id=d["doc_id"]``) is a rebuild. If every field it reads from that
    object belongs to the class being built, it must name every field of
    that class; ``dataclasses.replace`` is the way to change a few. If it
    reads fields of another contract class (``_record`` turns an
    ``ExtractedDoc`` into a ``DocumentRecord``), the function it sits in
    must read every field of that source class.

    A field copied through a local name (``pg_, st_ = got.pages, got.status``
    then ``pages=pg_``) is a copy too: the guard once counted only keywords
    whose value read ``base.<field>`` directly, so a rebuild through locals
    dropped ``spans`` with this test green. The guard checks itself on that
    shape first."""
    import ast
    import dataclasses
    from pathlib import Path

    from dociq.contracts import DocumentRecord, PageRecord

    classes = {"ExtractedDoc": ex.ExtractedDoc, "DocumentRecord": DocumentRecord,
               "PageRecord": PageRecord}
    fields = {k: {f.name for f in dataclasses.fields(v)} for k, v in classes.items()}
    src = Path(__file__).resolve().parents[1] / "src"

    def aliases(func) -> dict:
        """``{local name: node}`` for ``name = base.f`` and tuple
        assignments of such reads anywhere in ``func``."""
        out: dict = {}
        for n in ast.walk(func):
            if not isinstance(n, ast.Assign) or len(n.targets) != 1:
                continue
            target, value = n.targets[0], n.value
            pairs = ([(target, value)] if isinstance(target, ast.Name)
                     else list(zip(target.elts, value.elts))
                     if isinstance(target, ast.Tuple) and isinstance(value, ast.Tuple)
                     and len(target.elts) == len(value.elts) else [])
            for t, v in pairs:
                is_read = (
                    isinstance(v, (ast.Attribute, ast.Subscript))
                    and isinstance(v.value, ast.Name)
                    or isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute)
                    and v.func.attr == "get" and isinstance(v.func.value, ast.Name))
                if isinstance(t, ast.Name) and is_read:
                    out[t.id] = v
        return out

    def reads(node, local=None):
        """``{base name: {field names}}`` read as ``base.f``, ``base["f"]``
        or ``base.get("f")`` anywhere under ``node``, following a local name
        bound to such a read."""
        out: dict = {}
        for n in ast.walk(node):
            if local and isinstance(n, ast.Name) and n.id in local:
                for base, got in reads(local[n.id]).items():
                    out.setdefault(base, set()).update(got)
            if (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                    and n.attr != "get"):
                out.setdefault(n.value.id, set()).add(n.attr)
            elif (isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name)
                  and isinstance(n.slice, ast.Constant)
                  and isinstance(n.slice.value, str)):
                out.setdefault(n.value.id, set()).add(n.slice.value)
            elif (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                  and n.func.attr == "get" and isinstance(n.func.value, ast.Name)
                  and n.args and isinstance(n.args[0], ast.Constant)
                  and isinstance(n.args[0].value, str)):
                out.setdefault(n.func.value.id, set()).add(n.args[0].value)
        return out

    def scan(tree, label: str, sites: list, failures: list) -> None:
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            local = aliases(func)
            func_reads = reads(func, local)
            for call in ast.walk(func):
                if not isinstance(call, ast.Call):
                    continue
                name = (call.func.attr if isinstance(call.func, ast.Attribute)
                        else getattr(call.func, "id", None))
                if name not in classes:
                    continue
                named = {kw.arg for kw in call.keywords if kw.arg}
                per_base: dict = {}
                for kw in call.keywords:
                    if not kw.arg:
                        continue
                    for base, got in reads(kw.value, local).items():
                        per_base.setdefault(base, [set(), set()])
                        per_base[base][0] |= got
                        if kw.arg in got:
                            per_base[base][1].add(kw.arg)
                for base, (read, copied) in per_base.items():
                    if len(copied) < 2:
                        continue
                    where = f"{label}:{call.lineno} ({func.name})"
                    if read <= fields[name]:
                        sites.append(where)
                        missing = fields[name] - named
                        if missing:
                            failures.append(f"{where}: rebuilds {name} from "
                                            f"{base!r} without {sorted(missing)}")
                        continue
                    source = next((k for k, v in fields.items() if read <= v), None)
                    if source is None:
                        continue  # copied from something that is not a contract record
                    sites.append(where)
                    unread = fields[source] - func_reads.get(base, set())
                    if unread:
                        failures.append(f"{where}: builds {name} from {source} "
                                        f"{base!r} without reading {sorted(unread)}")

    probe_sites: list = []
    probe_failures: list = []
    scan(ast.parse(
        "def rebuild(got, extra_notes):\n"
        "    pg_, st_, er_ = got.pages, got.status, got.error\n"
        "    return ex.ExtractedDoc(pages=pg_, notes=got.notes + extra_notes,\n"
        "                           status=st_, error=er_)\n"), "probe", probe_sites, probe_failures)
    assert any("without ['spans']" in f for f in probe_failures), (
        f"the guard must see a field dropped through locals: {probe_failures!r}")

    sites, failures = [], []
    for path in sorted(src.rglob("*.py")):
        scan(ast.parse(path.read_text(encoding="utf-8")), str(path.relative_to(src)),
             sites, failures)
    assert any("_record" in s for s in sites) and any("_doc_from_jsonable" in s for s in sites), (
        f"the guard must see the known rebuild sites, or it checks nothing: {sites!r}")
    assert not failures, "\n".join(failures)


# ---------------------------------------------------------------------------
# 21. An Outlook .msg child's own attachments are read, whatever carries it
# ---------------------------------------------------------------------------


def test_msg_child_attachments_are_read_through_the_msg_reader(tmp_path, monkeypatch):
    """A Package object can wrap any file, an Outlook .msg among them (Word
    spec, construct table: 13 ``Package`` objects in the corpus), and nothing
    exercised a .msg child: dropping .msg from the recursion, or reading it
    with the .eml parser, passed every test.
    The .msg reader is replaced by a stub so the test needs no Outlook
    writer; what is pinned is that the walker hands it the child's bytes and
    turns what it returns into grandchildren."""
    msg_bytes = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"INVENTED-MSG-BYTES" * 40
    seen: list[bytes] = []

    def fake_msg(raw, scratch_dir):
        seen.append(raw)
        return ex.ZipExpansion((ex.ZipMember("leaf.txt", b"MSG-LEAF-QUOKKA", 0),), ())

    monkeypatch.setattr(ex, "expand_msg_attachments", fake_msg)
    raw = _docx_objects([("Embed", "Package", "embeddings/oleObject1.bin",
                          _package("notice.msg", msg_bytes))])
    r = _walk_files(tmp_path, {"outer.docx": raw})

    assert msg_bytes in seen, "the .msg child's bytes never reached the .msg reader"
    leaf = next((d for d in r.documents
                 if d.rel_path == "outer.docx/notice.msg/leaf.txt"), None)
    assert leaf is not None, [d.rel_path for d in r.documents]
    assert leaf.parent_doc_id == "outer.docx/notice.msg", leaf.parent_doc_id
    assert "MSG-LEAF-QUOKKA" in _text(leaf), _text(leaf)


# ---------------------------------------------------------------------------
# 22. The embedded-object stream is chosen by name, not by set order
# ---------------------------------------------------------------------------

_HASHSEED_CHILD = '''
import json
import sys

from dociq.contracts import RunConfig
from dociq.ingest import extract as ex
from dociq.ingest import walker

src, out = sys.argv[1], sys.argv[2]
r = walker.run(RunConfig(source_root=src, output_root=out,
                         ocr_engine_version=ex.ocr_engine_version()),
               walker.WalkOptions(ocr_enabled=False, resume=False, workers=1))
print(json.dumps([[d.rel_path, d.status.value, d.ext, d.sha256, d.parent_doc_id,
                   d.container_order, list(d.notes), d.error]
                  for d in list(r.documents) + list(r.unsupported)]))
'''


def test_embedded_object_records_are_identical_under_every_hash_seed(tmp_path):
    """A legacy Word document keeps its own embedded objects in
    ``ObjectPool/_NNN/`` storages, each with its own ``\\x01Ole10Native``.
    The reader used to take whichever stream ``next()`` met first in a SET of
    full stream paths ending in "Ole10Native", so which nested object
    replaced the legacy document depended on ``PYTHONHASHSEED``. Only
    root-level streams are an object's own; the legacy document is kept as
    stored. Ten interpreters, ten hash seeds, one answer."""
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    legacy_doc = make_fixtures._write_compound_file({
        "WordDocument": b"INVENTED-WORDDOCUMENT-STREAM",
        "1Table": b"INVENTED-TABLE-STREAM",
        "ObjectPool/_1111/\x01Ole10Native": make_fixtures._ole10_native(
            "alpha.txt", b"ALPHA-NESTED-PACKAGE"),
        "ObjectPool/_2222/\x01Ole10Native": make_fixtures._ole10_native(
            "bravo.txt", b"BRAVO-NESTED-PACKAGE"),
    })
    raw = _docx_objects([
        ("Embed", "Word.Document.8",
         "embeddings/Microsoft_Word_97_-_2003_Document.doc", legacy_doc),
        ("Embed", "Package", "embeddings/oleObject2.bin",
         _package("delta.txt", b"DELTA-ROOT-PACKAGE")),
    ])
    src = tmp_path / "src"
    src.mkdir()
    (src / "legacy.docx").write_bytes(raw)
    script = tmp_path / "hashseed_child.py"
    script.write_text(_HASHSEED_CHILD, encoding="utf-8")
    repo_src = str(Path(__file__).resolve().parents[1] / "src")

    outputs = {}
    for seed in range(10):
        env = dict(os.environ, PYTHONHASHSEED=str(seed), PYTHONPATH=repo_src)
        done = subprocess.run(
            [sys.executable, str(script), str(src), str(tmp_path / f"out{seed}")],
            capture_output=True, text=True, env=env, timeout=300)
        assert done.returncode == 0, done.stderr[-2000:]
        outputs[seed] = done.stdout.strip().splitlines()[-1]
    distinct = set(outputs.values())
    assert len(distinct) == 1, (
        "records differ between hash seeds: "
        + "; ".join(f"seed {s}: {[row[0] for row in json.loads(o)]}"
                    for s, o in outputs.items()))

    rows = {row[0]: row for row in json.loads(distinct.pop())}
    assert "legacy.docx/Microsoft_Word_97_-_2003_Document.doc" in rows, sorted(rows)
    assert "legacy.docx/delta.txt" in rows, sorted(rows)
    assert not any(k.endswith(("alpha.txt", "bravo.txt")) for k in rows), (
        f"an object nested inside the legacy document is not the Word file's "
        f"own embedded object: {sorted(rows)}")


# ---------------------------------------------------------------------------
# 23. Embedded objects are expanded by what the file IS, not by its name
# ---------------------------------------------------------------------------


def test_only_a_word_package_is_sent_to_the_embedded_object_expander(tmp_path, monkeypatch):
    """PDF bytes named .docx (fixture 10, the misnamed-file case productions
    deliver) were sent to the expander by their name. The expander could
    not open them, and the walker recorded a TRANSIENT "could not enumerate
    attachments" gap on a file that read completely, retried it serially on
    every run and never replayed it on resume. A workbook is a zip too and
    must not be sent either."""
    import hashlib

    real = ex.expand_docx_embeddings
    sent: list[str] = []

    def spy(raw):
        sent.append(hashlib.sha256(raw).hexdigest())
        return real(raw)

    monkeypatch.setattr(ex, "expand_docx_embeddings", spy)
    notes = walker.RunNotes()
    r = _walk_files(tmp_path, {
        "misnamed.docx": (FIXTURES / "attachments" / "10_misnamed.docx").read_bytes(),
        "06_register.xlsx": (FIXTURES / "06_register.xlsx").read_bytes(),
        "17_word_embeddings.docx": (FIXTURES / "17_word_embeddings.docx").read_bytes(),
    }, notes)

    misnamed = next(d for d in r.documents if d.rel_path == "misnamed.docx")
    assert not any(ex.has_evidence_marker(n) for n in misnamed.notes), misnamed.notes
    assert notes.load_dependent == [], notes.load_dependent
    word = {d.sha256 for d in r.documents if d.rel_path in (
        "17_word_embeddings.docx",
        "17_word_embeddings.docx/Microsoft_Word_Document1.docx")}
    assert set(sent) == word, (
        f"exactly the two Word packages must reach the expander: sent "
        f"{len(sent)} blob(s), {len(set(sent) - word)} of them not Word")


def test_a_word_package_under_another_name_still_yields_its_embedded_documents(tmp_path):
    """A Word file delivered as .pdf or .xlsx, inside a zip, attached to an
    email or wrapped in a Package object under another name was read as Word
    by the content-sniff recovery, but its embedded documents were routed by
    name and silently never recovered."""
    word = (FIXTURES / "17_word_embeddings.docx").read_bytes()
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("exhibit.pdf", word)
    r = _walk_files(tmp_path, {
        "as_pdf.pdf": word,
        "as_xlsx.xlsx": word,
        "bundle.zip": zip_buf.getvalue(),
        "mail.eml": _eml_attaching("report.pdf", word),
        "outer.docx": _docx_objects([("Embed", "Package", "embeddings/oleObject1.bin",
                                      _package("memo.pdf", word))]),
    })
    by_rel = {d.rel_path: d for d in r.documents}
    for route in ("as_pdf.pdf", "as_xlsx.xlsx", "bundle.zip/exhibit.pdf",
                  "mail.eml/report.pdf", "outer.docx/memo.pdf"):
        child = by_rel.get(f"{route}/Microsoft_Excel_Worksheet1.xlsx")
        assert child is not None, (
            f"the workbook embedded in the Word file at {route!r} was not "
            f"recovered: {sorted(k for k in by_rel if k.startswith(route))!r}")
        assert child.parent_doc_id == route, child.parent_doc_id
        assert "BISON-WORKBOOK" in _text(child), _text(child)


# ---------------------------------------------------------------------------
# 24. A container's own disclosure survives nesting, and the depth cap
# ---------------------------------------------------------------------------


def _inner_with_losses(tmp_path) -> bytes:
    """A Word file holding one recoverable workbook, one part that is
    neither a ZIP nor a compound file, and one link."""
    return _docx_objects([
        ("Embed", "Excel.Sheet.12", "embeddings/good.xlsx",
         make_fixtures._we_xlsx_bytes(tmp_path / "good.xlsx", "WOMBAT-GOOD")),
        ("Embed", "DocIQFixture.Unreadable.1", "embeddings/junk.bin",
         b"INVENTED-NEITHER-ZIP-NOR-COMPOUND"),
        ("Link", "Excel.Sheet.12", "file:///C:/elsewhere/linked.xlsx", None),
    ], body_text="inner with losses")


def _chain(inner: bytes, levels: int) -> bytes:
    raw = inner
    for i in range(levels, 0, -1):
        raw = _docx_objects([("Embed", "Word.Document.12", f"embeddings/l{i}.docx", raw)],
                            body_text=f"level {i - 1}")
    return raw


def _assert_losses_named(doc) -> None:
    marked = _marked(doc)
    assert any("junk.bin" in n for n in marked), (
        f"{doc.rel_path!r}: the unrecoverable part must be named in a marked "
        f"note: {doc.notes!r}")
    assert any("link" in n.lower() for n in marked), (
        f"{doc.rel_path!r}: the link must be named in a marked note: {doc.notes!r}")


def test_a_nested_word_files_own_losses_are_named_on_its_own_record(tmp_path, monkeypatch):
    """Every nested case the suite built was well formed, so a nested
    container's marked notes, its count note, and the note for an exception
    out of its expansion could each be dropped with every test green."""
    inner = _inner_with_losses(tmp_path)
    r = _walk_files(tmp_path, {"outer.docx": _chain(inner, 1)})
    rec = next(d for d in r.documents if d.rel_path == "outer.docx/l1.docx")
    _assert_losses_named(rec)
    assert "1 embedded document(s) extracted as child document(s)" in rec.notes, rec.notes
    assert any(d.rel_path == "outer.docx/l1.docx/good.xlsx" for d in r.documents)

    real = ex.expand_docx_embeddings

    def raise_for_inner(raw):
        if raw == inner:
            raise RuntimeError("INVENTED-NESTED-FAILURE")
        return real(raw)

    monkeypatch.setattr(ex, "expand_docx_embeddings", raise_for_inner)
    tmp2 = tmp_path / "second"
    tmp2.mkdir()
    r2 = _walk_files(tmp2, {"outer.docx": _chain(inner, 1)})
    rec2 = next(d for d in r2.documents if d.rel_path == "outer.docx/l1.docx")
    assert any(ex.M_ATTACH_ENUM in n and "INVENTED-NESTED-FAILURE" in n
               for n in _marked(rec2)), rec2.notes


def test_the_depth_capped_word_file_still_names_its_own_losses(tmp_path, monkeypatch):
    """At the depth cap the walker looked inside the capped child only to
    name its members, discarded the child's own notes, and swallowed an
    exception. An unrecoverable object or a link at the cap was then named
    nowhere; walked on its own, the same file names both."""
    inner = _inner_with_losses(tmp_path)
    levels = ex._ZIP_MAX_DEPTH
    capped = "top.docx/" + "/".join(f"l{i}.docx" for i in range(1, levels + 1))
    r = _walk_files(tmp_path, {"top.docx": _chain(inner, levels)})
    rec = next((d for d in r.documents if d.rel_path == capped), None)
    assert rec is not None, [d.rel_path for d in r.documents]
    _assert_losses_named(rec)
    assert any("nesting deeper than" in n and "good.xlsx" in n for n in _marked(rec)), rec.notes
    assert not any(d.rel_path.startswith(capped + "/") for d in r.documents)

    real = ex.expand_docx_embeddings

    def raise_for_inner(raw):
        if raw == inner:
            raise RuntimeError("INVENTED-CAPPED-FAILURE")
        return real(raw)

    monkeypatch.setattr(ex, "expand_docx_embeddings", raise_for_inner)
    tmp2 = tmp_path / "second"
    tmp2.mkdir()
    r2 = _walk_files(tmp2, {"top.docx": _chain(inner, levels)})
    rec2 = next(d for d in r2.documents if d.rel_path == capped)
    assert any(ex.M_ATTACH_ENUM in n and "INVENTED-CAPPED-FAILURE" in n
               for n in _marked(rec2)), rec2.notes


# ---------------------------------------------------------------------------
# 25. Relationship targets: package-absolute, or relative to the source part
# ---------------------------------------------------------------------------


def test_a_package_absolute_object_target_is_recovered_without_a_false_note(tmp_path):
    """``Target="/word/embeddings/..."`` is a valid OPC part name. It used to
    be joined onto ``word/`` as ``/word/...``, so the object got a FINAL
    "not in the package" note while the unreferenced-part sweep recovered
    the same part anyway."""
    xlsx = make_fixtures._we_xlsx_bytes(tmp_path / "abs.xlsx", "NUMBAT-ABSOLUTE")
    raw = _docx_objects([
        ("Embed", "Excel.Sheet.12", "/word/embeddings/abs.xlsx", xlsx),
        ("Embed", "Excel.Sheet.12", "embeddings/../embeddings/rel.xlsx",
         make_fixtures._we_xlsx_bytes(tmp_path / "rel.xlsx", "NUMBAT-RELATIVE")),
    ])
    exp = ex.expand_docx_embeddings(raw)
    assert [m.name for m in exp.members] == ["abs.xlsx", "rel.xlsx"], exp.members
    assert exp.notes == (), exp.notes


# ---------------------------------------------------------------------------
# 26. What a compound file or a Package holds decides what is recovered
# ---------------------------------------------------------------------------


def test_a_zip_wrapped_in_a_package_object_has_its_members_read(tmp_path):
    """A zip attached to an email is flattened into its members; the same zip
    wrapped in a Word Package object became one UNSUPPORTED record whose
    members were never read."""
    inner_zip = io.BytesIO()
    with zipfile.ZipFile(inner_zip, "w") as zf:
        zf.writestr("memo.txt", b"PANGOLIN-ZIPPED-MEMO")
        zf.writestr("sub/second.txt", b"PANGOLIN-ZIPPED-SECOND")
    raw = _docx_objects([("Embed", "Package", "embeddings/oleObject1.bin",
                          _package("production.zip", inner_zip.getvalue()))])
    r = _walk_files(tmp_path, {"outer.docx": raw})
    by_rel = {d.rel_path: d for d in r.documents}
    for rel, sentinel in (("outer.docx/production.zip/memo.txt", "PANGOLIN-ZIPPED-MEMO"),
                          ("outer.docx/production.zip/sub/second.txt",
                           "PANGOLIN-ZIPPED-SECOND")):
        assert rel in by_rel, sorted(by_rel)
        assert sentinel in _text(by_rel[rel]), _text(by_rel[rel])
        assert by_rel[rel].parent_doc_id == "outer.docx", by_rel[rel].parent_doc_id
    assert not any(d.rel_path.endswith("production.zip") for d in r.unsupported), (
        [d.rel_path for d in r.unsupported])


def test_compound_files_are_recovered_by_what_they_hold(tmp_path):
    """Every compound file that was not a PDF ``CONTENTS`` or an
    ``\\x01Ole10Native`` wrapper used to be kept as a ``.bin`` child, listed
    "Unrecognized format" with no marker, while the Word record counted it
    as extracted. A ``Package`` stream holds the file itself; a legacy
    Office document is kept whole under its own kind; anything else is not
    recovered and a marked note names the part."""
    xlsx = make_fixtures._we_xlsx_bytes(tmp_path / "pkg.xlsx", "CASSOWARY-PACKAGE-STREAM")
    legacy_doc = make_fixtures._write_compound_file(
        {"WordDocument": b"INVENTED-WORD-97", "1Table": b"INVENTED-TABLE"})
    equation = make_fixtures._write_compound_file({"Equation Native": b"\x1c\x00" * 40})
    not_pdf = make_fixtures._write_compound_file({"CONTENTS": b"INVENTED-NOT-A-PDF"})
    raw = _docx_objects([
        ("Embed", "Excel.Sheet.12", "embeddings/oleObject1.bin",
         make_fixtures._write_compound_file({"Package": xlsx})),
        ("Embed", "Word.Document.8", "embeddings/oleObject2.bin", legacy_doc),
        ("Embed", "Equation.3", "embeddings/oleObject3.bin", equation),
        ("Embed", "AcroExch.Document.DC", "embeddings/oleObject4.bin", not_pdf),
    ])
    exp = ex.expand_docx_embeddings(raw)
    assert [(m.name, m.raw) for m in exp.members] == [
        ("oleObject1.xlsx", xlsx), ("oleObject2.doc", legacy_doc)], (
        [m.name for m in exp.members])
    marked = [n for n in exp.notes if ex.has_evidence_marker(n)]
    assert len(marked) == 2 and "oleObject3.bin" in marked[0] and "oleObject4.bin" in marked[1], (
        exp.notes)

    r = _walk_files(tmp_path, {"objects.docx": raw})
    parent = next(d for d in r.documents if d.rel_path == "objects.docx")
    # The legacy .doc child is inventoried only, never extracted: the count
    # note said "2 ... extracted" while that child's record was UNSUPPORTED.
    assert "1 embedded document(s) extracted as child document(s)" in parent.notes, parent.notes
    assert ("1 embedded document(s) inventoried as child document(s) only, in a format "
            "DocIQ does not read") in parent.notes, parent.notes
    workbook = next(d for d in r.documents if d.rel_path == "objects.docx/oleObject1.xlsx")
    assert "CASSOWARY-PACKAGE-STREAM" in _text(workbook), _text(workbook)
    assert any(d.rel_path == "objects.docx/oleObject2.doc" for d in r.unsupported), (
        [d.rel_path for d in r.unsupported])


def test_a_stored_name_that_is_only_an_extension_keeps_its_extension(tmp_path):
    """``Path(".eml").suffix`` is empty, so an attachment stored as ".eml" was
    listed as an unrecognized format instead of being read."""
    eml = _small_eml_bytes("DOTNAME-EMAIL", "DOTNAME-BODY-JERBOA")
    raw = _docx_objects([("Embed", "Package", "embeddings/oleObject1.bin",
                          _package(".eml", eml))])
    r = _walk_files(tmp_path, {"dotname.docx": raw})
    child = next((d for d in r.documents if d.parent_doc_id == "dotname.docx"), None)
    assert child is not None, [(d.rel_path, d.status.value) for d in r.unsupported]
    assert child.ext == ".eml" and "DOTNAME-BODY-JERBOA" in _text(child), (
        child.rel_path, child.ext, _text(child))


def test_a_malformed_part_loses_only_its_own_objects(tmp_path):
    """One malformed header part used to abort the whole expansion, so every
    embedded document of the file was lost behind one note. (The part is
    related from the main part: a part nothing relates is not scanned.)"""
    xlsx = make_fixtures._we_xlsx_bytes(tmp_path / "keep.xlsx", "TUATARA-KEPT")
    raw = _docx_objects([("Embed", "Excel.Sheet.12", "embeddings/keep.xlsx", xlsx)],
                        extra_parts={"word/header9.xml": b"<w:hdr this is not xml"},
                        extra_rels=[("rIdH9", "header", "header9.xml")])
    exp = ex.expand_docx_embeddings(raw)
    assert [m.name for m in exp.members] == ["keep.xlsx"], exp.members
    marked = [n for n in exp.notes if ex.has_evidence_marker(n)]
    assert len(marked) == 1 and "header9.xml" in marked[0], exp.notes


# ---------------------------------------------------------------------------
# 27. Every citation in the package resolves inside the repository
# ---------------------------------------------------------------------------


def test_package_citations_resolve_inside_the_repository():
    """The package's comments cited the uncommitted build briefs by their
    item and gap numbers and file names, and review probes and mutants by
    file name, none of which a reader of the repository can open. They cite
    the Word spec (``docs/design/word_fidelity_spec.md``, parts 1 to 10) or
    a register ruling instead."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    files = ["src/dociq/ingest/extract.py", "src/dociq/ingest/walker.py",
             "src/dociq/selftest.py", "tests/conftest.py",
             "tests/fixtures/make_fixtures.py", "tests/test_word_fidelity.py",
             "tests/test_word_embeddings.py", "tests/test_extract.py",
             "tests/test_walker.py"]
    # Built from pieces so this test's own source is not a match.
    scratch = re.compile("|".join([
        r"word" + r"_brief", r"\bbrief" + r"_errors\b", r"\bmissing" + r"_tests\b",
        r"word2b" + r"_", r"scratch" + r"pad", r"\b[Ii]tem" + r" \d+\b",
        r"\bgap" + r" \d\b", r"\bcritic" + r" (?:finding|probe)",
        r"\b(?:p\d+|probe)_[a-z0-9_]+" + r"\.py\b"]))
    part = re.compile(r"Word spec,? (?:part|parts|section) (\d+)")
    assert (root / "docs" / "design" / "word_fidelity_spec.md").is_file()
    bad = []
    for rel in files:
        for no, line in enumerate((root / rel).read_text(encoding="utf-8").splitlines(), 1):
            if scratch.search(line):
                bad.append(f"{rel}:{no}: {line.strip()}")
            for m in part.finditer(line):
                if not 1 <= int(m.group(1)) <= 10 or "section" in m.group(0):
                    bad.append(f"{rel}:{no}: {line.strip()}")
    assert not bad, "\n".join(bad)


# ---------------------------------------------------------------------------
# 28. A stored name that has to change loses as little as possible, and the
# record quotes it exactly
# ---------------------------------------------------------------------------


def _zip_of(entries) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return buf.getvalue()


def _eml_attaching_all(items) -> bytes:
    import base64

    lines = ["From: engineer@example.com", "To: contractor@example.com",
             "Subject: INVENTED-MULTI-ATTACHMENT", "Date: Fri, 19 Jul 2024 09:00:00 +0000",
             "MIME-Version: 1.0",
             'Content-Type: multipart/mixed; boundary="dociq-multi-boundary"',
             "", "--dociq-multi-boundary", "Content-Type: text/plain; charset=utf-8", "",
             "carrier body", ""]
    for name, payload in items:
        lines += ["--dociq-multi-boundary",
                  f'Content-Type: application/octet-stream; name="{name}"',
                  "Content-Transfer-Encoding: base64",
                  f'Content-Disposition: attachment; filename="{name}"', "",
                  base64.b64encode(payload).decode("ascii"), ""]
    lines += ["--dociq-multi-boundary--", ""]
    return "\r\n".join(lines).encode("utf-8")


def test_a_changed_stored_name_loses_as_little_as_possible_and_is_quoted_exactly(tmp_path):
    """Ordinary names ("Con. Schedule Rev 3", "Aux/", "Minutes 10:30",
    "RE: Delay Notice") were replaced whole by ``unnamed_child_N``, with no
    record of the stored name, and one ``Aux/`` folder became two invented
    folders. Now each unsafe character or device name is changed as little as
    possible, a changed folder stays one folder (and yields to a stored folder
    that already has its new name), and the child's first note quotes the
    stored name exactly -- unscrubbed, so a name that looks like an absolute
    path is still quoted whole."""
    production = _zip_of([
        ("Con. Schedule Rev 3.txt", b"ZIP-CON"), ("Aux/Pump test.txt", b"ZIP-PUMP"),
        ("_Aux/literal.txt", b"ZIP-LITERAL"), ("Aux/Valve log.txt", b"ZIP-VALVE"),
        ("PRN.2 register.txt", b"ZIP-PRN"), ("Minutes 10:30.txt", b"ZIP-COLON"),
        ("notes.txt. ", b"ZIP-TRAILING"), ("/srv/Invented/abs.txt", b"ZIP-ABSOLUTE"),
        ("Plain Schedule Rev 4.txt", b"ZIP-PLAIN")])
    carrier = _eml_attaching_all([
        ("RE: Delay Notice.eml", _small_eml_bytes("INVENTED-NOTICE", "EML-NOTICE-BODY")),
        ("Minutes 10:30.txt", b"EML-COLON")])
    r = _walk_files(tmp_path, {"production.zip": production, "carrier.eml": carrier})
    by_rel = {d.rel_path: d for d in list(r.documents) + list(r.unsupported)}
    device = "a Windows device name was prefixed with '_'"
    numbered = "numbered to keep it apart from another name in this container"
    expected = {
        "production.zip/_Con. Schedule Rev 3.txt": (
            "ZIP-CON", f"stored name 'Con. Schedule Rev 3.txt' is recorded as "
                       f"'_Con. Schedule Rev 3.txt': {device}"),
        "production.zip/_Aux__2/Pump test.txt": (
            "ZIP-PUMP", f"stored name 'Aux/Pump test.txt' is recorded as "
                        f"'_Aux__2/Pump test.txt': {device}; {numbered}"),
        "production.zip/_Aux__2/Valve log.txt": (
            "ZIP-VALVE", f"stored name 'Aux/Valve log.txt' is recorded as "
                         f"'_Aux__2/Valve log.txt': {device}; {numbered}"),
        "production.zip/_Aux/literal.txt": ("ZIP-LITERAL", None),
        "production.zip/_PRN.2 register.txt": (
            "ZIP-PRN", f"stored name 'PRN.2 register.txt' is recorded as "
                       f"'_PRN.2 register.txt': {device}"),
        "production.zip/Minutes 10_30.txt": (
            "ZIP-COLON", "stored name 'Minutes 10:30.txt' is recorded as "
                         "'Minutes 10_30.txt': ':' was replaced by '_'"),
        "production.zip/notes.txt": (
            "ZIP-TRAILING", "stored name 'notes.txt. ' is recorded as 'notes.txt': "
                            "trailing dots or spaces were removed"),
        "production.zip/srv/Invented/abs.txt": (
            "ZIP-ABSOLUTE", "stored name '/srv/Invented/abs.txt' is recorded as "
                            "'srv/Invented/abs.txt': empty, '.' and '..' parts were removed"),
        "production.zip/Plain Schedule Rev 4.txt": ("ZIP-PLAIN", None),
        "carrier.eml/RE_ Delay Notice.eml": (
            "EML-NOTICE-BODY", "stored name 'RE: Delay Notice.eml' is recorded as "
                               "'RE_ Delay Notice.eml': ':' was replaced by '_'"),
        "carrier.eml/Minutes 10_30.txt": (
            "EML-COLON", "stored name 'Minutes 10:30.txt' is recorded as "
                         "'Minutes 10_30.txt': ':' was replaced by '_'"),
    }
    for rel, (sentinel, note) in expected.items():
        doc = by_rel.get(rel)
        assert doc is not None, (rel, sorted(by_rel))
        assert sentinel in _text(doc), (rel, _text(doc))
        name_notes = [n for n in doc.notes if n.startswith("stored name ")]
        assert name_notes == ([note] if note else []), (rel, doc.notes)
        if note:
            assert doc.notes[0] == note, doc.notes


@pytest.mark.parametrize("device", [
    "CON", "PRN", "AUX", "NUL", "COM0", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7",
    "COM8", "COM9", "COM\u00b9", "COM\u00b2", "COM\u00b3", "LPT0", "LPT1", "LPT2", "LPT3", "LPT4",
    "LPT5", "LPT6", "LPT7", "LPT8", "LPT9", "LPT\u00b9", "LPT\u00b2", "LPT\u00b3"])
def test_every_windows_device_name_is_prefixed_and_nothing_else_is(device):
    """The device names Windows reserves, whatever the case and extension,
    held as a literal list here rather than read back from the product's own
    set; a name that only starts like one keeps its name."""
    got = walker._child_names([
        ex.ZipMember(f"{device.lower()}.log", b"", 0), ex.ZipMember(f"{device}X.log", b"", 1)])
    assert [name for name, _note in got] == [f"_{device.lower()}.log", f"{device}X.log"], got
    assert got[1][1] is None, got


def test_repeated_stored_names_are_numbered_exactly_and_the_first_keeps_its_name():
    got = walker._child_names([ex.ZipMember("dup.txt", b"", i) for i in range(3)])
    assert got == [
        ("dup.txt", None),
        ("dup__2.txt", "stored name 'dup.txt' is recorded as 'dup__2.txt': numbered to "
                       "keep it apart from another name in this container"),
        ("dup__3.txt", "stored name 'dup.txt' is recorded as 'dup__3.txt': numbered to "
                       "keep it apart from another name in this container")], got


# ---------------------------------------------------------------------------
# 29. Hashed output carries no memory address or temporary file name
# ---------------------------------------------------------------------------

_PIPELINE_CHILD = '''
import json
import sys
from pathlib import Path

from dociq import pipeline
from dociq.contracts import RunConfig
from dociq.ingest import extract as ex
from dociq.ingest import walker

src, out = sys.argv[1], sys.argv[2]
outcome = pipeline.run(
    RunConfig(source_root=src, output_root=out, ocr_engine_version=ex.ocr_engine_version()),
    pipeline.PipelineOptions(walk=walker.WalkOptions(ocr_enabled=False, resume=False, workers=1),
                             write_workbook=False, write_summary_pdf=False,
                             write_package=False, auto_confirm_bates=True))
log = json.loads((Path(out) / "processing_log.json").read_text(encoding="utf-8"))
print(json.dumps([log["content_sha256"],
                  [[d.rel_path, d.status.value, d.error, list(d.notes)]
                   for d in list(outcome.result.documents) + list(outcome.result.unsupported)]]))
'''


def _macro_enabled_word(text: str) -> bytes:
    import docx

    d = docx.Document()
    d.add_paragraph(text)
    buf = io.BytesIO()
    d.save(buf)
    zin = zipfile.ZipFile(io.BytesIO(buf.getvalue()))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == "[Content_Types].xml":
                data = data.replace(
                    b"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
                    b"application/vnd.ms-word.document.macroEnabled.main+xml")
            zout.writestr(zipfile.ZipInfo(info.filename, date_time=(2021, 3, 4, 5, 6, 8)), data)
    return out.getvalue()


def test_a_rejected_word_main_part_hashes_identically_in_two_interpreters(tmp_path):
    """python-docx rejects a macro-enabled main part with a message holding its
    stream object's repr, memory address and all; that text reached the
    record's error and the hashed log, so two runs of one input disagreed.
    One Word file at the top level and one held in an embedded Package
    stream, each run in two fresh interpreters: one content hash."""
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    macro = _macro_enabled_word("INVENTED-MACRO-BODY")
    src = tmp_path / "src"
    src.mkdir()
    (src / "macro.docx").write_bytes(macro)
    (src / "memo.docx").write_bytes(_docx_objects(
        [("Embed", "Package", "embeddings/oleObject1.bin",
          make_fixtures._write_compound_file({"Package": macro}))]))
    script = tmp_path / "pipeline_child.py"
    script.write_text(_PIPELINE_CHILD, encoding="utf-8")
    repo_src = str(Path(__file__).resolve().parents[1] / "src")
    runs = []
    for seed in (1, 2):
        env = dict(os.environ, PYTHONHASHSEED=str(seed), PYTHONPATH=repo_src)
        done = subprocess.run([sys.executable, str(script), str(src), str(tmp_path / f"out{seed}")],
                              capture_output=True, text=True, env=env, timeout=600)
        assert done.returncode == 0, done.stderr[-2000:]
        runs.append(json.loads(done.stdout.strip().splitlines()[-1]))
    failed = {row[0]: row for row in runs[0][1] if row[1] == "failed"}
    assert {"macro.docx", "memo.docx/oleObject1.docx"} <= set(failed), runs[0][1]
    assert runs[0][0] == runs[1][0], (
        "content_sha256 differs between two runs: "
        + "; ".join(str(row[2]) for row in runs[0][1] + runs[1][1] if row[1] == "failed"))
    assert not any(" at 0x" in json.dumps(row) for row in runs[0][1]), runs[0][1]


def test_sanitize_message_removes_addresses_and_temporary_names():
    assert ex.sanitize_message(
        "file '<_io.BytesIO object at 0x000001AD7ECCB8D0>' is not a Word file") == (
        "file '<_io.BytesIO object>' is not a Word file")
    assert ex.sanitize_message(
        "cannot open C:\\Users\\Invented\\AppData\\Local\\Temp\\tmpk3j2l1x0.msg: bad") == (
        "cannot open <temp file>.msg: bad")
    assert ex.sanitize_message("see /var/tmp/tmpab_cd123/report.pdf") == "see report.pdf"
    assert ex.sanitize_message("kept: tmpfile_notes.txt and 0x1F") == (
        "kept: tmpfile_notes.txt and 0x1F")


# ---------------------------------------------------------------------------
# 30. Parts are found through relationships, and compared as OPC compares them
# ---------------------------------------------------------------------------


def _part_xml(root: str, inner: str) -> bytes:
    return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:{root} {_MIN_NS}>'
            f"{inner}</w:{root}>").encode("utf-8")


def _object_para(rid: str) -> str:
    return (f'<w:p><w:r><w:object><o:OLEObject Type="Embed" ProgID="Excel.Sheet.12" '
            f'r:id="{rid}"/></w:object></w:r></w:p>')


def _rels_xml(*rels) -> bytes:
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships '
            'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(f'<Relationship Id="{i}" Type="http://schemas.openxmlformats.org/'
                      f'officeDocument/2006/relationships/{t}" Target="{g}"/>' for i, t, g in rels)
            + "</Relationships>").encode("utf-8")


def test_objects_in_notes_and_comments_are_found_through_relationships_whatever_their_names(tmp_path):
    """The notes parts were found by their default file names: objects in a
    ``footnotes2.xml`` or a comments part lost their place in the order, and a
    stale, unrelated ``footnotes.xml`` produced a false "relationship id does
    not exist" note. Every header, footer, footnotes, endnotes and comments
    part the main part relates is scanned, in part-name order."""
    xlsx = lambda name: make_fixtures._we_xlsx_bytes(tmp_path / name, name.upper())  # noqa: E731
    raw = _docx_objects(
        [("Embed", "Excel.Sheet.12", "embeddings/m_body.xlsx", xlsx("m_body.xlsx"))],
        extra_parts={
            "word/footnotes2.xml": _part_xml("footnotes", '<w:footnote w:id="1">'
                                            + _object_para("rIdA") + "</w:footnote>"),
            "word/_rels/footnotes2.xml.rels": _rels_xml(("rIdA", "oleObject", "embeddings/a_foot.xlsx")),
            "word/embeddings/a_foot.xlsx": xlsx("a_foot.xlsx"),
            "word/header1.xml": _part_xml("hdr", _object_para("rIdB")),
            "word/_rels/header1.xml.rels": _rels_xml(("rIdB", "oleObject", "embeddings/b_head.xlsx")),
            "word/embeddings/b_head.xlsx": xlsx("b_head.xlsx"),
            "word/comments7.xml": _part_xml("comments", '<w:comment w:id="0" w:author="x">'
                                            + _object_para("rIdC") + "</w:comment>"),
            "word/_rels/comments7.xml.rels": _rels_xml(("rIdC", "oleObject", "objects/c_cmt.xlsx")),
            "word/objects/c_cmt.xlsx": xlsx("c_cmt.xlsx"),
            "word/footnotes.xml": _part_xml("footnotes", '<w:footnote w:id="9">'
                                           + _object_para("rIdObjFn") + "</w:footnote>"),
        },
        extra_rels=[("rIdFn", "footnotes", "footnotes2.xml"), ("rIdH", "header", "header1.xml"),
                    ("rIdCm", "comments", "comments7.xml")])
    exp = ex.expand_docx_embeddings(raw)
    assert [m.name for m in exp.members] == ["m_body.xlsx", "c_cmt.xlsx", "a_foot.xlsx",
                                             "b_head.xlsx"], exp.members
    assert exp.notes == (), exp.notes


def test_an_object_target_differing_only_in_case_or_escaping_is_recovered_without_a_note(tmp_path):
    """Part names compare case-insensitively and a target's percent-escapes
    name the same part: ``KEEP.XLSX`` got a false "not in the package" note
    while the trailing sweep recovered ``keep.xlsx`` anyway."""
    raw = _docx_objects(
        [("Embed", "Excel.Sheet.12", "embeddings/KEEP.XLSX", None),
         ("Embed", "Excel.Sheet.12", "embeddings/Sheet%201.xlsx", None)],
        extra_parts={
            "word/embeddings/keep.xlsx": make_fixtures._we_xlsx_bytes(tmp_path / "k.xlsx", "KEPT-CASE"),
            "word/embeddings/Sheet 1.xlsx": make_fixtures._we_xlsx_bytes(tmp_path / "s.xlsx", "KEPT-ESC")})
    exp = ex.expand_docx_embeddings(raw)
    assert [m.name for m in exp.members] == ["keep.xlsx", "Sheet 1.xlsx"], exp.members
    assert exp.notes == (), exp.notes


# ---------------------------------------------------------------------------
# 31. Each disclosure path of the embedded-object reader, held by a test that
# fails when its note is deleted or unmarked
# ---------------------------------------------------------------------------


def _deep_zip() -> bytes:
    """A zip nested five archives deep: expanded, the fifth is past the cap."""
    raw = _zip_of([("leaf.txt", b"INVENTED-DEEP-LEAF")])
    for level in (4, 3, 2):
        raw = _zip_of([(f"l{level}.zip", raw)])
    return _zip_of([("x.txt", b"INVENTED-SHALLOW"), ("l1.zip", _zip_of([("l2.zip", raw)]))])


def test_an_embedded_archive_that_cannot_be_read_is_named_in_a_marked_note():
    raw = _docx_objects([("Embed", "Package", "embeddings/oleObject1.bin",
                          _package("report.zip", b"INVENTED-NOT-A-ZIP"))])
    exp = ex.expand_docx_embeddings(raw)
    assert exp.members == (), exp.members
    marked = [n for n in exp.notes if ex.has_evidence_marker(n)]
    assert len(marked) == 1 and "embedded archive 'report.zip' could not be read" in marked[0], (
        exp.notes)


def test_an_embedded_archives_own_unmarked_notes_are_marked():
    raw = _docx_objects([("Embed", "Package", "embeddings/oleObject1.bin",
                          _package("deep.zip", _deep_zip()))])
    exp = ex.expand_docx_embeddings(raw)
    assert "deep.zip/x.txt" in [m.name for m in exp.members], exp.members
    deep = [n for n in exp.notes if "nesting deeper than" in n]
    assert len(deep) == 1 and deep[0].startswith(
        f"{ex.M_ATTACH_SKIPPED}: embedded archive 'deep.zip': "), exp.notes


def test_at_the_nesting_limit_a_containers_unmarked_notes_are_marked(tmp_path):
    innermost = _eml_attaching("deep.zip", _deep_zip())
    chain = _eml_attaching("c.eml", innermost)
    chain = _eml_attaching("b.eml", chain)
    r = _walk_files(tmp_path, {"top.eml": _eml_attaching("a.eml", chain)})
    capped = next((d for d in r.documents if d.rel_path == "top.eml/a.eml/b.eml/c.eml"), None)
    assert capped is not None, [d.rel_path for d in r.documents]
    limit = [n for n in capped.notes if "nesting deeper than 3 levels" in n]
    assert len(limit) == 1 and limit[0].startswith(
        f"{ex.M_ATTACH_SKIPPED}: inside a container at the nesting limit: "), capped.notes


def test_unparsable_relationships_of_a_related_part_are_named_in_a_marked_note():
    raw = _docx_objects([], extra_parts={
        "word/header9.xml": _part_xml("hdr", _object_para("rIdX")),
        "word/_rels/header9.xml.rels": b"<Relationships this is not xml"},
        extra_rels=[("rIdH9", "header", "header9.xml")])
    exp = ex.expand_docx_embeddings(raw)
    marked = [n for n in exp.notes if ex.has_evidence_marker(n)]
    assert len(marked) == 1 and "the relationships of 'word/header9.xml' could not be parsed" in (
        marked[0]), exp.notes


def test_a_package_stream_holding_a_word_file_or_a_pdf_is_recovered_as_what_it_is(tmp_path):
    word = _docx_objects([], body_text="WOMBAT-INNER-WORD")
    pdf = _minimal_pdf_bytes()
    raw = _docx_objects([
        ("Embed", "Package", "embeddings/oleObject1.bin", make_fixtures._write_compound_file({"Package": word})),
        ("Embed", "Package", "embeddings/oleObject2.bin", make_fixtures._write_compound_file({"Package": pdf}))])
    exp = ex.expand_docx_embeddings(raw)
    # The fixture's compound-file writer pads a stream to 4096 bytes.
    assert [(m.name, m.raw[:len(data)]) for m, data in zip(exp.members, (word, pdf))] == [
        ("oleObject1.docx", word), ("oleObject2.pdf", pdf)], [m.name for m in exp.members]
    r = _walk_files(tmp_path, {"outer.docx": raw})
    inner = next(d for d in r.documents if d.rel_path == "outer.docx/oleObject1.docx")
    assert "WOMBAT-INNER-WORD" in _text(inner), _text(inner)


@pytest.mark.parametrize("stream,ext", [("Workbook", ".xls"), ("Book", ".xls"),
                                        ("__properties_version1.0", ".msg")])
def test_a_legacy_office_stream_keeps_the_compound_file_whole_under_its_kind(stream, ext):
    compound = make_fixtures._write_compound_file({stream: b"INVENTED-LEGACY-STREAM"})
    exp = ex.expand_docx_embeddings(_docx_objects(
        [("Embed", "Invented.1", "embeddings/oleObject3.bin", compound)]))
    assert [(m.name, m.raw) for m in exp.members] == [(f"oleObject3{ext}", compound)], exp.members


def test_the_member_cap_also_stops_the_unreferenced_part_sweep(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "_ZIP_MAX_MEMBERS", 1)
    raw = _docx_objects(
        [("Embed", "Excel.Sheet.12", "embeddings/ref.xlsx",
          make_fixtures._we_xlsx_bytes(tmp_path / "r.xlsx", "CAP-REF"))],
        extra_parts={"word/embeddings/unref.xlsx": make_fixtures._we_xlsx_bytes(
            tmp_path / "u.xlsx", "CAP-UNREF")})
    exp = ex.expand_docx_embeddings(raw)
    assert [m.name for m in exp.members] == ["ref.xlsx"], exp.members
    assert any(ex.has_evidence_marker(n) and "truncated at 1 members" in n for n in exp.notes), (
        exp.notes)


# ---------------------------------------------------------------------------
# 32. A Word package is read wherever it arrives, whatever it is called
# ---------------------------------------------------------------------------


def test_a_word_package_named_zip_is_read_wherever_it_arrives(tmp_path):
    """Named ``.zip``, a Word file was unpacked as an archive at the top level,
    inside an archive, attached to an email and held in a Package object: its
    body and its PDF object were never read, and its parts became
    "unrecognized format" records."""
    word = (FIXTURES / "17_word_embeddings.docx").read_bytes()
    r = _walk_files(tmp_path, {
        "report.zip": word,
        "bundle.zip": _zip_of([("inner.zip", word)]),
        "mail.eml": _eml_attaching("report.zip", word),
        "outer.docx": _docx_objects([("Embed", "Package", "embeddings/oleObject1.bin",
                                      _package("report.zip", word))]),
    })
    by_rel = {d.rel_path: d for d in r.documents}
    for route in ("report.zip", "bundle.zip/inner.zip", "mail.eml/report.zip",
                  "outer.docx/report.zip"):
        rec = by_rel.get(route)
        assert rec is not None and "This sentence opens the fixture" in _text(rec), (
            route, sorted(by_rel))
        for child, sentinel in (("Microsoft_Excel_Worksheet1.xlsx", "BISON-WORKBOOK"),
                                ("oleObject2.pdf", "CONDOR-PDF")):
            got = by_rel.get(f"{route}/{child}")
            assert got is not None and sentinel in _text(got), (route, child, sorted(by_rel))
    parts = [d.rel_path for d in list(r.documents) + list(r.unsupported)
             if d.rel_path.endswith((".xml", ".rels"))]
    assert parts == [], parts


@pytest.mark.parametrize("name", ["memo.txt", "memo.csv", "memo.eml", "memo.md", "memo.log",
                                  "memo.email"])
def test_a_word_package_under_a_text_name_is_read_as_word(tmp_path, name):
    """The text, CSV and email readers never fail, so a Word file named
    ``.txt`` was never retried by content: its page was its raw zip bytes,
    reported FULL, while its embedded documents were recovered."""
    word = (FIXTURES / "17_word_embeddings.docx").read_bytes()
    r = _walk_files(tmp_path, {name: word})
    rec = next(d for d in r.documents if d.rel_path == name)
    ext = name[name.rfind("."):]
    assert "This sentence opens the fixture" in _text(rec) and not _text(rec).startswith("PK"), (
        _text(rec)[:80])
    assert (f"extension {ext} but content is a zip-family container; recovered via Word "
            "extractor") in rec.notes, rec.notes


def test_two_different_archives_with_one_name_are_filed_apart(tmp_path):
    """Two Package objects (or two email attachments) each holding a
    different ``bundle.zip`` put both archives' members under one
    ``bundle.zip/`` folder, as if one archive had held them all."""
    zip_a = _zip_of([("a.txt", b"ALPHA-FIRST-ARCHIVE")])
    zip_b = _zip_of([("a.txt", b"BRAVO-SECOND-ARCHIVE")])
    exp = ex.expand_docx_embeddings(_docx_objects([
        ("Embed", "Package", "embeddings/oleObject1.bin", _package("bundle.zip", zip_a)),
        ("Embed", "Package", "embeddings/oleObject2.bin", _package("bundle.zip", zip_b))]))
    assert [(m.name, m.raw) for m in exp.members] == [
        ("bundle.zip/a.txt", b"ALPHA-FIRST-ARCHIVE"),
        ("bundle__2.zip/a.txt", b"BRAVO-SECOND-ARCHIVE")], exp.members
    assert exp.notes == ("embedded archive 'bundle.zip' has the name of one already listed; "
                         "its members are listed under 'bundle__2.zip'",), exp.notes
    r = _walk_files(tmp_path, {"mail.eml": _eml_attaching_all([("bundle.zip", zip_a),
                                                              ("bundle.zip", zip_b)])})
    by_rel = {d.rel_path: _text(d) for d in r.documents}
    assert "ALPHA-FIRST-ARCHIVE" in by_rel.get("mail.eml/bundle.zip/a.txt", ""), sorted(by_rel)
    assert "BRAVO-SECOND-ARCHIVE" in by_rel.get("mail.eml/bundle__2.zip/a.txt", ""), sorted(by_rel)


def test_an_email_child_named_dot_email_has_its_own_attachments_read(tmp_path):
    inner = _small_eml_bytes("INNER-DOT-EMAIL", "DOT-EMAIL-BODY",
                             attachment=("leaf.txt", "DOT-EMAIL-LEAF"))
    r = _walk_files(tmp_path, {"mail.eml": _eml_attaching("inner.email", inner)})
    leaf = next((d for d in r.documents if d.rel_path == "mail.eml/inner.email/leaf.txt"), None)
    assert leaf is not None and "DOT-EMAIL-LEAF" in _text(leaf), [d.rel_path for d in r.documents]


def test_attachment_counts_say_how_many_were_read_and_how_many_only_inventoried(tmp_path):
    r = _walk_files(tmp_path, {"mail.eml": _eml_attaching_all([
        ("notes.txt", b"INVENTED-READ"), ("drawing.dwg", b"INVENTED-NOT-READ")])})
    parent = next(d for d in r.documents if d.rel_path == "mail.eml")
    assert "1 attachment(s) extracted as child document(s)" in parent.notes, parent.notes
    assert ("1 attachment(s) inventoried as child document(s) only, in a format DocIQ does "
            "not read") in parent.notes, parent.notes


# ---------------------------------------------------------------------------
# 33. The remaining disclosure paths, one test each (the Word spec addendum's
# note table names the test that holds every note the package emits)
# ---------------------------------------------------------------------------


def test_an_object_naming_a_relationship_id_that_does_not_exist_is_named():
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships '
            'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>')
    exp = ex.expand_docx_embeddings(_docx_with_raw_object(
        '<o:OLEObject Type="Embed" ProgID="Excel.Sheet.12" r:id="rIdMissing"/>', rels))
    marked = [n for n in exp.notes if ex.has_evidence_marker(n)]
    assert exp.members == () and len(marked) == 1 and (
        "names relationship id 'rIdMissing', which does not exist" in marked[0]), exp.notes


def test_an_embedded_part_that_cannot_be_decompressed_is_named_for_a_retry(tmp_path):
    """A stored part whose bytes fail their checksum is named under the
    transient archive-member marker, so the walker re-reads the file."""
    good = make_fixtures._we_xlsx_bytes(tmp_path / "c.xlsx", "CRC-KEPT")
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(_docx_objects(
            [("Embed", "Excel.Sheet.12", "embeddings/bad.xlsx", good)]))) as zin, \
            zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zout:
        for info in zin.infolist():
            zout.writestr(info.filename, zin.read(info.filename))
    raw = bytearray(buf.getvalue())
    at = raw.find(good[40:80])
    assert at > 0
    raw[at] ^= 0xFF
    exp = ex.expand_docx_embeddings(bytes(raw))
    assert exp.members == (), exp.members
    assert any(n.startswith(f"{ex.M_ZIP_MEMBER}: 'word/embeddings/bad.xlsx'") for n in exp.notes), (
        exp.notes)
    assert ex.has_transient_marker(exp.notes[0]), exp.notes


def test_a_compound_file_that_cannot_be_opened_is_named():
    garbage = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600
    exp = ex.expand_docx_embeddings(_docx_objects(
        [("Embed", "Invented.1", "embeddings/oleObject1.bin", garbage)]))
    marked = [n for n in exp.notes if ex.has_evidence_marker(n)]
    assert exp.members == () and len(marked) == 1 and (
        "'word/embeddings/oleObject1.bin' is a compound file that could not be read" in marked[0]), (
        exp.notes)


def test_a_compound_file_without_its_reader_installed_is_named(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "olefile", None)
    exp = ex.expand_docx_embeddings(_docx_objects(
        [("Embed", "Package", "embeddings/oleObject1.bin", _package("x.txt", b"INVENTED"))]))
    marked = [n for n in exp.notes if ex.has_evidence_marker(n)]
    assert exp.members == () and len(marked) == 1 and "'olefile' is not installed" in marked[0], (
        exp.notes)


def test_an_attached_archive_that_cannot_be_read_is_named(tmp_path):
    r = _walk_files(tmp_path, {"mail.eml": _eml_attaching("bad.zip", b"INVENTED-NOT-A-ZIP")})
    parent = next(d for d in r.documents if d.rel_path == "mail.eml")
    marked = [n for n in parent.notes if ex.has_evidence_marker(n)]
    assert len(marked) == 1 and marked[0].startswith(
        f"{ex.M_ZIP_ATTACH}: attachment 'bad.zip' is a zip that could not be read"), parent.notes


def test_two_inner_archives_with_one_name_in_one_archive_are_filed_apart():
    buf = io.BytesIO()
    with pytest.warns(UserWarning), zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("inner.zip", _zip_of([("a.txt", b"INNER-ONE")]))
        zf.writestr("inner.zip", _zip_of([("a.txt", b"INNER-TWO")]))
    exp = ex.expand_zip(buf.getvalue())
    assert [(m.name, m.raw) for m in exp.members] == [("inner.zip/a.txt", b"INNER-ONE"),
                                                      ("inner__2.zip/a.txt", b"INNER-TWO")], exp
    assert exp.notes == ("archive 'inner.zip' has the name of one already listed; its members "
                         "are listed under 'inner__2.zip'",), exp.notes
