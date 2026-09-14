"""D-50 stage 2b: documents embedded in a Word file become child documents.

Every test here is written against the CONTRACT stage 2b's build spec makes
true and MUST fail against ``b5aba07`` (no ``expand_docx_embeddings``, no
walker routing for a .docx's embedded objects, no recursion into a
container's own children). This file is the target for that package, not a
regression suite: do not "fix" a failure here by loosening an assertion, and
do not touch ``src/`` from this file.

New symbols the stage 2b draft adds to ``dociq.ingest.extract``
(``expand_docx_embeddings`` chief among them) are referenced only INSIDE the
test function that needs them, never at module import time -- so a symbol
still missing on ``b5aba07`` fails that one test with an ``AttributeError``,
not the whole module's collection.
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
                           footnote_target_part: str, body_bytes: bytes,
                           footer_bytes: bytes) -> bytes:
    """A .docx carrying one ``<o:OLEObject>`` in the body, one in
    ``word/footer1.xml`` and one in ``word/footnotes.xml`` -- each resolved
    through THAT part's own ``.rels``, and each using the SAME literal r:id
    string ("rIdEmbed1") on purpose, since a header/footer/footnote r:id
    only makes sense within its own part's relationship namespace. The
    footnotes object points at the SAME part as the body object, to exercise
    "each part read once" (a part referenced twice yields one child)."""
    doc_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document {_MIN_NS}><w:body>'
        '<w:p><w:r><w:object w:dxaOrig="1440" w:dyaOrig="1440">'
        '<o:OLEObject Type="Embed" ProgID="Excel.Sheet.12" r:id="rIdEmbed1"/>'
        '</w:object></w:r></w:p>'
        '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/></w:sectPr>'
        '</w:body></w:document>')
    doc_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
        '2006/relationships"><Relationship Id="rIdEmbed1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        f'relationships/oleObject" Target="{body_part[len("word/"):]}"/>'
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
        f'<w:footnotes {_MIN_NS}><w:footnote w:id="1"><w:p><w:r>'
        '<w:object w:dxaOrig="1440" w:dyaOrig="1440">'
        '<o:OLEObject Type="Embed" ProgID="Excel.Sheet.12" r:id="rIdEmbed1"/>'
        '</w:object></w:r></w:p></w:footnote></w:footnotes>')
    footnotes_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
        '2006/relationships"><Relationship Id="rIdEmbed1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/oleObject" '
        f'Target="{footnote_target_part[len("word/"):]}"/></Relationships>')

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
    # text, into a grandchild, or into an unrelated record is caught too
    # (critic finding: mutant leak_parent, which folds every child's page
    # text into the parent's own, passed a leak check scoped to the 4
    # children alone). Ground truth from a correct reference walk (critic
    # probe p5_reference_walk.py): every sentinel is held by exactly one
    # record, and the PDF and inner-docx-nested-xlsx paths pin the
    # "<part stem>.pdf" / nested-rel_path naming rules along with it.
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
    # just "some grandchild carries the sentinel" (critic finding: mutant
    # flat_relpath built a grandchild's rel_path from the TOP file instead
    # of its immediate parent, colliding GECKO-NESTED's path with object 1's
    # -- only test 6's accounting caught it, as a bare failure with no
    # detail).
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
    # recovered (critic finding: mutant false_notes added such a note for
    # each of the 4 recovered parts and passed a presence-only check; on a
    # correct walk this would also wrongly count the Word file as
    # evidence-lost in accounting).
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
    # The brief requires every marked note to name the PART and say why
    # (critic finding: mutant malformed_no_part used generic wording with no
    # part name and passed an "Ole10Native"-only check).
    assert any("oleObject1.bin" in n and "Ole10Native" in n for n in marked), (
        f"expected ONE marked note naming both the part "
        f"('word/embeddings/oleObject1.bin') and the malformed stream "
        f"('Ole10Native'): {exp.notes!r}")


# ---------------------------------------------------------------------------
# 5b. A nesting chain past _ZIP_MAX_DEPTH: marked note
# ---------------------------------------------------------------------------


def test_nesting_chain_past_zip_max_depth_yields_a_marked_note(tmp_path):
    # NOTE on design (word_brief_stage2b_tests.md brief_errors item 1): the
    # brief does not say whether the TOP-level Word file counts as one of
    # the "_ZIP_MAX_DEPTH containers". expand_zip's own precedent (extract.py
    # ~2770) calls the top zip depth 0 and stops only at depth >
    # _ZIP_MAX_DEPTH, i.e. it expands the top file plus _ZIP_MAX_DEPTH
    # nested levels. Applied the same way to container (.docx-in-.docx)
    # recursion: the entry file's own objects are always expanded (not
    # depth-gated -- that is the ordinary, always-performed first pass), and
    # each further level of recursion into a CHILD's own embeddings is
    # counted starting at 1, refused once that count reaches
    # _ZIP_MAX_DEPTH. That yields exactly _ZIP_MAX_DEPTH nested containers
    # below the entry file. This is the loosest reading consistent with
    # expand_zip's own wording ("deeper than N levels") and is verified
    # against an independent reference walker built for exactly this
    # question (critic probe p5_reference_walk.py) -- it produces the exact
    # record chain and note placement pinned below. Critic finding: the
    # PRIOR (loose) form of this test only required "SOME marked note,
    # SOMEWHERE, mentioning 'depth'" -- which a cap reached one container
    # early (mutant depth_off_by_one) satisfied just as well as a correct
    # cap, and which rejected a differently-worded but CORRECT note (mutant
    # deeper_wording, "nesting deeper than N ..." mirroring expand_zip's own
    # phrasing) because it does not contain the substring "depth".
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
    """May already PASS before parts 1-2 (the brief calls this out
    explicitly): fixture 17 is one more Tier-1 .docx today, with no
    children, and accounting only has to balance against what the walk
    actually produced. Reported either way -- not treated as a gate
    failure if it already passes."""
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
# 7 (critic missing_tests). Unwrap by what the bytes ARE, not by ProgID alone
# ---------------------------------------------------------------------------


def test_unwrap_is_keyed_on_bytes_not_on_progid(tmp_path):
    """Every fixture 17 object's ProgID happens to match its bytes, so a
    dispatcher keyed on ProgID alone would pass fixture 17 too -- this
    mismatches them on purpose. The corpus also carries
    'Acrobat.Document.*' ProgID variants (word_brief_stage2b.md's corpus
    table), so a PDF under a ProgID other than 'AcroExch.Document.DC' must
    still be recovered."""
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
# 8 (critic missing_tests). Object order: body, then aux parts in part-name
# order (each through its OWN .rels), then the unreferenced-part sweep; each
# part read once
# ---------------------------------------------------------------------------


def test_object_order_body_then_aux_parts_each_own_rels_dedup(tmp_path):
    """A part referenced from the body, the footer and the footnotes, with
    colliding r:id strings resolved through each part's OWN relationships.
    Critic finding: mutant skip_aux ignored every non-body part and passed
    all 7 tests -- an object in a header/footer/footnote would vanish
    silently, which is the exact class D-50 exists to close."""
    body_bytes = make_fixtures._we_xlsx_bytes(tmp_path / "body.xlsx", "BODY-OBJ")
    footer_bytes = make_fixtures._we_xlsx_bytes(tmp_path / "footer.xlsx", "FOOTER-OBJ")
    raw = _docx_with_aux_objects(
        body_part="word/embeddings/body_obj.xlsx",
        footer_part="word/embeddings/footer_obj.xlsx",
        # SAME part as the body object -- must be read once, not twice.
        footnote_target_part="word/embeddings/body_obj.xlsx",
        body_bytes=body_bytes, footer_bytes=footer_bytes)

    exp = ex.expand_docx_embeddings(raw)
    got = [(m.name, m.order) for m in exp.members]
    assert got == [("body_obj.xlsx", 0), ("footer_obj.xlsx", 1)], (
        f"expected body's object first, then the footer's (word/footer1.xml "
        f"sorts before word/footnotes.xml by part name), and the "
        f"footnotes' duplicate reference to the SAME part must not add a "
        f"third member: {got!r}")


# ---------------------------------------------------------------------------
# 9 (critic missing_tests). Unreferenced word/embeddings/ parts: after every
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
# 10 (critic missing_tests). Link / missing r:id / external target / a
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
# 11 (critic missing_tests). A legacy-Office compound file (neither PDF
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
    # left on r.documents. Probed and confirmed: word2b_impl/probe_ppt_tier2.py.
    child = next((d for d in r.unsupported if d.rel_path.endswith(".ppt")), None)
    assert child is not None, (
        f"expected a Tier-2 record for the stored .ppt part: "
        f"{[d.rel_path for d in r.unsupported]!r}")
    assert child.status == ProcessingStatus.UNSUPPORTED, (
        f"a legacy .ppt child must be marked Tier 2 (UNSUPPORTED): "
        f"{child.status!r}")


# ---------------------------------------------------------------------------
# 12 (critic missing_tests). An Ole10Native stream with no stored filename
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
# 13 (critic missing_tests). Every Ole10Native read is bounds-checked,
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
# 14 (critic missing_tests). Member-count and total-bytes caps on embeddings
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
# 15 (critic missing_tests). If expand_docx_embeddings raises, the walker
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
# 16 (critic missing_tests). Recursion covers a .msg/.eml inside a .zip and
# a .eml attached to a .eml -- the silent loss that predates D-50
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
# 17 (critic missing_tests). "N embedded document(s) extracted as child
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
# 18 (item 4). A stored filename is untrusted: made safe before it becomes
# part of a rel_path, whatever container kind supplied it.
# ---------------------------------------------------------------------------

# Expected leaf for the shapes where the transformation is exact and worth
# pinning precisely rather than only by the generic invariants below.
_UNTRUSTED_EXACT_LEAF = {
    "drive_absolute": "evil.txt",       # only the final path component survives
    "windows_traversal": "evil.txt",    # '..' segments are discarded, not walked
    "trailing_dots_spaces": "notice.eml",  # Windows would strip these silently
}


@pytest.mark.parametrize("stored_name", [
    "", ".", "..", "../../evil.txt", "..\\..\\evil.txt",
    "C:\\Windows\\evil.txt", "C:evil.txt", "CON", "CON.txt", "COM1.log",
    "notice.eml.   ",
], ids=["empty", "dot", "dotdot", "posix_traversal", "windows_traversal",
       "drive_absolute", "drive_relative", "reserved_bare",
       "reserved_with_ext", "reserved_device", "trailing_dots_spaces"])
def test_untrusted_ole10native_stored_filename_is_sanitized(tmp_path, stored_name):
    """item 4: an ``\\x01Ole10Native`` stored filename is untrusted. Probed
    against the walker as it stood before this fix
    (``word2b_impl/probe_untrusted_names.py``): ``_child_records`` built
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
# 19 (item 4). Two children of one parent with the same name: disambiguated
# deterministically by container order, for every container kind.
# ---------------------------------------------------------------------------


def test_two_embedded_objects_with_the_same_stored_name_get_distinct_rel_paths(
        tmp_path):
    """item 4's 'two children of one parent with the same name' shape: two
    Package objects, each wrapping a plainly-named 'duplicate.txt'. Probed
    against the walker as it stood before this fix
    (``word2b_impl/probe_duplicate_names.py``): both children's rel_path was
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
    """item 4's other half: ``expand_eml_attachments`` reads
    ``part.get_filename()`` unsanitized (word_brief_stage2b_impl.md item 4),
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
