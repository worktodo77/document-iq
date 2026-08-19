"""The §7 deliverables: clean text, index, log, summary, handoff."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from dociq.contracts import (
    matter_key,
    ContractViolation,
    Disposition,
    MasterIndexSnapshot,
    RunConfig,
    canonical_json,
    content_hash,
)
from dociq.docid.assign import assign_doc_ids
from dociq.docid.reconcile import reconcile
from dociq.emit.cleantext import (
    page_marker,
    render_document,
    write_clean_text,
    write_sources_json,
)
from dociq.emit.handoff import (
    README_NAME,
    SANCTIONED_NAMES,
    PackageContentError,
    ProjectLimits,
    assert_only_sanctioned,
    build_upload_package,
    default_scope_statement,
    expert_assist_layout,
    render_readme,
)
from dociq.emit.indexbook import (
    INDEX_COLUMNS,
    RECONCILIATION_COLUMNS,
    build_index_rows,
    render_index_csv,
    write_index_csv,
    write_index_xlsx,
    write_reconciliation_csv,
)
from dociq.emit.log import assert_float_free, build_log, write_processing_log
from dociq.emit.paths import OutputLayout, safe_component
from dociq.emit.summary import DOC_REMEDIATION_HINT, build_summary_data, write_run_summary
from dociq.identify.bates import (
    BatesDecision,
    DecisionStatus,
    apply_bates,
    document_ranges,
    propose_format,
)
from dociq.profiles.model import OperatorStamp
from dociq.sections.apply import apply_sections
from dociq.sections.model import ApprovedOmission
from dociq.sections.resolve import resolve_sections
from dociq.sections.templates import PROGRESS_REPORT
from dociq.verify.tokens import estimate_for_texts, estimate_tokens
from tests.fixtures import corpus, document, ocr_page, page


def assigned(count=3):
    return assign_doc_ids(corpus(count), None).documents


# --- the dropped-page fixture ----------------------------------------------
#
# Every dropped page in this file used to come from ``profiles.apply``, which
# D-35 deleted: it matched a rule's regex against every line of every page and
# carried the matched section forward until the next match, so one rule for
# PROGRESS PHOTOGRAPHS could drop an executive summary and attribute it to
# photographs. Nothing here is a translation of that engine. Drops now come from
# the two things that replaced it, and both are visible in the fixture:
#
#   * a SPAN, resolved from the document's own outline, which names its LAST
#     page and therefore cannot reach past it; and
#   * an :class:`ApprovedOmission`, which names a person — under D-34 it is the
#     only thing in the system that can turn a KEEP into a DROP. A template on
#     its own drops nothing, which is what ``sectioned(approved=())`` builds.

MPR_OUTLINE = [
    ("COVER", 0),
    ("EXECUTIVE SUMMARY", 1),
    ("SCHEDULE STATUS", 2),
    ("4. HSE STATISTICS", 3),
    ("PHOTO LOG", 4),
    ("ORGANISATION CHART", 5),
]
"""The outline of ``fixtures.MPR_PAGES``, one entry per page, 0-based as
PyMuPDF's ``get_toc`` gives them. Entries the shipped template classifies into
six different families — including ``EXECUTIVE SUMMARY``, which is recognized
and can never be offered.

**One entry carries its section number on purpose.** A real outline numbers its
sections and the number changes between months, so the family a template matches
is the STRIPPED key while the label the log and the index show is the
document's own words. With every entry pre-normalized the two would be equal on
every row here and an assertion on either would pass for the wrong reason."""

OMITTED = ("hse-statistics", "progress-photographs", "organization-chart")
"""The three families engaged in most of this file's fixtures — the same three
sections the retired profile fixture dropped, so the page arithmetic every
downstream assertion depends on is unchanged."""

APPROVER = "abachowski"
APPROVED_AT = "2026-08-17T12:00:00Z"
MATTER = "Project 495 — QDCPC v Domopan"


def approvals(*family_ids: str) -> tuple[ApprovedOmission, ...]:
    return tuple(
        ApprovedOmission(
            family_id=family_id,
            approved_by=APPROVER,
            approved_at=APPROVED_AT,
            matter=MATTER,
            matter_root=matter_key(MATTER),
            template_id=PROGRESS_REPORT.template_id,
            template_version=PROGRESS_REPORT.version,
        )
        for family_id in family_ids
    )


def sectioned(count=3, *, approved=OMITTED):
    """Doc IDs first, then recognition, then the approved omissions.

    That order is the pipeline's and it is load-bearing rather than tidy: a
    :class:`~dociq.sections.apply.SectionDropEntry` is written against a Doc ID,
    so Stage 4 cannot run before Stage 3b has issued one. Applying sections to
    an unassigned corpus produces drop entries keyed on the empty string, and
    the index's "Omission approved by" column — which joins on ``doc_id`` —
    silently empties.

    Returns ``(documents, drops)``; ``drops`` is empty when ``approved`` is.
    """
    documents, drops = [], []
    for doc in assign_doc_ids(corpus(count), None).documents:
        spans = resolve_sections(outline=list(MPR_OUTLINE), page_count=len(doc.pages))
        result = apply_sections(
            doc, spans, template=PROGRESS_REPORT, approvals=approvals(*approved),
            matter_root=MATTER
        )
        assert result.warnings == (), result.warnings
        documents.extend(result.documents)
        drops.extend(result.drops)
    return tuple(documents), tuple(drops)


# --- markers ---------------------------------------------------------------


def test_marker_forms():
    assert page_marker(12) == "===== PAGE 12 ====="
    assert page_marker(12, "MNFV 000391") == "===== PAGE 12 [BATES: MNFV 000391] ====="
    assert page_marker(12, "") == "===== PAGE 12 ====="


def test_marker_rejects_a_zero_page():
    with pytest.raises(ContractViolation):
        page_marker(0)


def test_dropped_pages_vanish_but_original_numbers_survive():
    """RE-POINTED at the engine that replaced the one D-35 deleted. The
    guarantee is unchanged and is the reason the assertion is on the NUMBERS:
    an omitted page leaves a hole in the sequence, so page 3 is still page 3
    and the reader of the clean text can see that pages were removed."""
    doc = sectioned(1)[0][0]
    body = render_document(doc)
    assert "===== PAGE 3 =====" in body
    assert "===== PAGE 4 =====" not in body  # HSE STATISTICS omitted
    numbers = [int(l.split()[2]) for l in body.split("\n") if l.startswith("===== PAGE")]
    assert numbers == [1, 2, 3]


def test_empty_page_still_gets_a_marker():
    doc = document("a.pdf", (page(1, "text"), page(2, ""), page(3, "more")))
    doc = assign_doc_ids((doc,), None).documents[0]
    body = render_document(doc)
    assert body.count("===== PAGE") == 3


# --- clean text ------------------------------------------------------------


def test_clean_text_is_lf_only_utf8(tmp_path):
    layout = OutputLayout.at(tmp_path)
    result = write_clean_text(assigned(), layout)
    for doc_id, rel in result.sources:
        raw = (tmp_path / rel).read_bytes()
        assert b"\r" not in raw
        raw.decode("utf-8")


def test_clean_text_refuses_a_duplicate_doc_id(tmp_path):
    a = document("a.pdf", (page(1, "x"),), doc_id="LI-00001")
    b = document("b.pdf", (page(1, "y"),), doc_id="LI-00001")
    with pytest.raises(ContractViolation) as exc:
        write_clean_text((a, b), OutputLayout.at(tmp_path))
    assert "would lose one document" in str(exc.value)


def test_clean_text_refuses_an_unassigned_document(tmp_path):
    with pytest.raises(ContractViolation):
        write_clean_text(corpus(1), OutputLayout.at(tmp_path))


def test_sources_json_maps_ids_to_paths(tmp_path):
    layout = OutputLayout.at(tmp_path)
    result = write_clean_text(assigned(), layout)
    write_sources_json(result, layout)
    payload = json.loads(layout.sources_json.read_text(encoding="utf-8"))
    assert set(payload) == {d for d, _ in result.sources}
    assert all(v.startswith("clean_text/") for v in payload.values())


def test_clean_text_is_byte_identical_across_runs(tmp_path):
    docs = assigned()
    digests = []
    for i in range(8):
        layout = OutputLayout.at(tmp_path / f"run{i}")
        result = write_clean_text(docs, layout)
        write_sources_json(result, layout)
        blob = b"".join(
            sorted((layout.root / rel).read_bytes() for _, rel in result.sources)
        ) + layout.sources_json.read_bytes()
        digests.append(hashlib.sha256(blob).hexdigest())
    assert len(set(digests)) == 1


def test_safe_component_neutralises_traversal_and_devices():
    assert "/" not in safe_component("../../etc/passwd")
    assert safe_component("CON") == "_CON"
    assert safe_component("") == "_"


# --- index -----------------------------------------------------------------


def test_index_columns_match_section_5():
    rows = build_index_rows(assigned())
    assert len(rows[0].values) == len(INDEX_COLUMNS)
    text = render_index_csv(rows)
    assert text.split("\n")[0].startswith("Doc ID,LI File No,Filename")
    assert "\r" not in text


def test_the_index_carries_the_recognition_tier_and_the_approver(tmp_path):
    """The three columns the taxonomy added, asserted on content.

    ``test_index_columns_match_section_5`` counts columns and would go on
    passing if all three rendered empty on every row for ever. Each of them
    answers a question "Sections dropped" raises and cannot settle: what was
    recognized (a 0 in the dropped column means two different things without
    it), how strongly (§5.4 — the outline and a page-class rule are not equal
    evidence), and who is answerable (D-34 — a person who acted, never a
    template).
    """
    docs, drops = sectioned(1)
    cells = dict(zip(INDEX_COLUMNS, build_index_rows(docs, drops=drops)[0].values))
    assert cells["Sections recognized"] == "6"
    assert cells["Recognition tier"] == "t1_outline"
    assert cells["Sections dropped"] == "3"
    assert cells["Omission approved by"] == APPROVER


def test_the_index_distinguishes_nothing_omitted_from_nothing_recognized(tmp_path):
    """The pair the "Sections recognized" column exists to separate, both built
    and compared rather than described. A document an expert kept whole and a
    document the recognizer never placed both show 0 omissions; only one of them
    is a decision, and the approver cell is empty on both — an empty cell means
    nothing was omitted, never that nobody is answerable."""
    docs, drops = sectioned(1, approved=())
    kept_whole = dict(zip(
        INDEX_COLUMNS, build_index_rows(docs, drops=drops)[0].values))
    unrecognized = dict(zip(
        INDEX_COLUMNS, build_index_rows(assigned(1))[0].values))

    assert kept_whole["Sections dropped"] == unrecognized["Sections dropped"] == "0"
    assert kept_whole["Omission approved by"] == ""
    assert unrecognized["Omission approved by"] == ""
    assert kept_whole["Sections recognized"] == "6"
    assert kept_whole["Recognition tier"] == "t1_outline"
    assert unrecognized["Sections recognized"] == "0"
    assert unrecognized["Recognition tier"] == ""


def test_index_csv_quotes_embedded_commas(tmp_path):
    doc = document("a, b/c.pdf", (page(1, "x"),), doc_id="DIQ-000001")
    text = render_index_csv(build_index_rows((doc,)))
    assert '"a, b/c.pdf"' in text


def test_index_csv_is_byte_identical_across_runs(tmp_path):
    docs = assigned()
    outs = set()
    for i in range(8):
        layout = OutputLayout.at(tmp_path / f"r{i}")
        layout.ensure()
        outs.add(write_index_csv(build_index_rows(docs), layout).read_bytes())
    assert len(outs) == 1


def test_index_xlsx_and_reconciliation_tab_are_written(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    layout = OutputLayout.at(tmp_path)
    layout.ensure()
    result = assign_doc_ids(corpus(2), None)
    report = reconcile(result, None)
    path = write_index_xlsx(build_index_rows(result.documents), layout, report)
    wb = openpyxl.load_workbook(path)
    assert wb.sheetnames == ["Document Index", "Reconciliation", "Reconciliation notes"]


def test_reconciliation_csv_is_written(tmp_path):
    layout = OutputLayout.at(tmp_path)
    layout.ensure()
    result = assign_doc_ids(corpus(2), None)
    path = write_reconciliation_csv(reconcile(result, None), layout)
    assert path.read_text(encoding="utf-8").startswith("Category,")


def _quarantined_report(tmp_path):
    from dociq.docid.masterindex import load_master_index

    header = (
        '"Original Sort","Filename","File Extension","Filepath","Size (KB)"\n'
    )
    body = (
        '"1","a.pdf","pdf","dir","10"\n'
        '"1","dupe.pdf","pdf","dir","11"\n'
        '"-3","neg.pdf","pdf","dir","12"\n'
    )
    src = tmp_path / "idx.csv"
    src.write_text(header + body, encoding="utf-8", newline="\n")
    index = load_master_index(src)
    return reconcile(assign_doc_ids(corpus(1), index), index)


def test_quarantined_index_rows_render_without_a_borrowed_li_file_no(tmp_path):
    """D-1: an unusable index row must appear in the deliverable, in its own
    category, and must not be shown carrying an LI File No it never got."""
    layout = OutputLayout.at(tmp_path / "out")
    layout.ensure()
    report = _quarantined_report(tmp_path)
    text = write_reconciliation_csv(report, layout).read_text(encoding="utf-8")

    rows = list(csv.reader(text.splitlines()))
    assert rows[0] == list(RECONCILIATION_COLUMNS)
    bad = [r for r in rows[1:] if r[0] == "In index, unusable row"]
    assert len(bad) == 2
    for cells in bad:
        assert cells[2] == ""  # LI File No: none, and none invented
        assert cells[9]  # Detail: says why
    assert [r[3] for r in bad] == ["dupe.pdf", "neg.pdf"]  # sheet order, stable
    # The one properly-numbered unmatched row keeps its ordinary category.
    assert [r[2] for r in rows[1:] if r[0] == "In index, not in folder"] == ["1"]
    assert report.totals["index_only_unnumbered"] == 2


def test_quarantined_reconciliation_csv_is_byte_stable(tmp_path):
    outs = set()
    for i in range(4):
        layout = OutputLayout.at(tmp_path / f"r{i}")
        layout.ensure()
        outs.add(
            write_reconciliation_csv(_quarantined_report(tmp_path), layout).read_bytes()
        )
    assert len(outs) == 1


# --- log -------------------------------------------------------------------


def config(tmp_path, **kw):
    return RunConfig(source_root=str(tmp_path / "src"), output_root=str(tmp_path), **kw)


def test_log_content_hash_replays_from_the_written_file(tmp_path):
    layout = OutputLayout.at(tmp_path)
    layout.ensure()
    docs = assigned()
    bundle = build_log(config(tmp_path), docs, stamp=OperatorStamp("a", "2026-07-30T00:00:00Z"))
    path = write_processing_log(bundle, layout)
    doc = json.loads(path.read_text(encoding="utf-8"))
    replay = hashlib.sha256(
        canonical_json(doc["content"], for_identity=True).encode("utf-8")
    ).hexdigest()
    assert replay == doc["content_sha256"]


def test_log_content_is_identical_across_runs_but_the_run_section_is_not(tmp_path):
    docs = assigned()
    a = build_log(config(tmp_path), docs, stamp=OperatorStamp("a", "2026-07-30T00:00:00Z"))
    b = build_log(config(tmp_path), docs, stamp=OperatorStamp("b", "2026-07-31T00:00:00Z"))
    assert a.content_sha256 == b.content_sha256
    assert a.run != b.run


def test_a_float_cannot_enter_the_hashed_content():
    with pytest.raises(ContractViolation):
        assert_float_free({"documents": [{"ocr": 0.91}]})


def test_ocr_confidence_reaches_the_log_as_an_integer_percent(tmp_path):
    doc = document("scan.pdf", (ocr_page(1, "text", 0.9123),))
    doc = assign_doc_ids((doc,), None).documents[0]
    bundle = build_log(config(tmp_path), (doc,), stamp=OperatorStamp("a", "t"))
    assert bundle.content["documents"][0]["ocr_mean_conf_pct"] == 91


def test_low_confidence_pages_are_flagged_against_the_threshold(tmp_path):
    """The pages carry REAL text now, and that is the point of the change.

    They read ``"a"`` and ``"b"`` — one character each — which since 2026-08-18
    exercises the blank-page exclusion rather than the threshold: a page with
    nothing on it has nothing for a human to check, so it is counted in the log
    and kept out of the review list. The guarantee under test here is still
    "a page below the threshold is flagged", so the fixture now gives it a page
    that can express it.
    """
    docs = (
        document("scan.pdf", (ocr_page(1, "a page of real scanned text " * 4, 0.60),
                              ocr_page(2, "another page of real text " * 4, 0.99))),
    )
    docs = assign_doc_ids(docs, None).documents
    bundle = build_log(config(tmp_path, ocr_conf_threshold_pct=85), docs,
                       stamp=OperatorStamp("a", "t"))
    assert [f["page_no"] for f in bundle.content["ocr_flagged_pages"]] == [1]


def test_log_records_the_id_regime_and_index_snapshot(tmp_path):
    snapshot = MasterIndexSnapshot("index.xlsx", "a" * 64, 9259)
    cfg = config(tmp_path, master_index=snapshot)
    bundle = build_log(cfg, assigned(), stamp=OperatorStamp("a", "t"))
    assert bundle.content["doc_ids"]["regime"] == "master-index"
    assert bundle.content["doc_ids"]["master_index"]["row_count"] == 9259


def test_log_states_bates_absence_is_not_an_error(tmp_path):
    docs = assigned()
    ranges = document_ranges(docs)
    bundle = build_log(config(tmp_path), docs, bates_ranges=ranges,
                       stamp=OperatorStamp("a", "t"))
    assert "not an error" in bundle.content["bates"]["note"]


def test_every_drop_reaches_the_log(tmp_path):
    docs, drops = sectioned(2)
    bundle = build_log(config(tmp_path), docs, drops=drops,
                       stamp=OperatorStamp("a", "t"))
    assert len(bundle.content["drops"]) == sum(d.pages_dropped for d in docs)
    assert all(e["drop_rule"] for e in bundle.content["drops"])


def test_a_drop_entry_answers_which_rule_what_evidence_and_who_approved(tmp_path):
    """RE-POINTED, and the re-pointing is the substance rather than a rename.

    The old assertion was ``all(e["rule_id"] ...)`` — one field, the profile
    rule. Under the engine D-35 deleted that field could be, and on the five
    reproduced shapes was, a lie: the rule named was the rule whose regex last
    matched a line, not the rule that governed the page. Two amendments landed
    with the replacement and each adds a question the log must now answer:

    * **A-18** — WHAT KIND OF EVIDENCE placed this page in that section.
      "The document's own outline said so" and "a page-class rule matched it"
      are not equally strong claims and must not render identically.
    * **D-34 / A-19** — WHO approved the omission, when, and on which matter,
      against which template and version. A template can never supply this,
      because a template approved nothing.

    Asserted field by field rather than by a length check, because a missing
    key here is silent: ``canonical_json`` writes whatever dict it is given.
    """
    docs, drops = sectioned(2)
    bundle = build_log(config(tmp_path), docs, drops=drops,
                       stamp=OperatorStamp("a", "t"))
    entries = bundle.content["drops"]
    assert entries, "the fixture engaged three levers and dropped nothing"

    for entry in entries:
        assert entry["tier"] == "t1_outline"
        assert entry["evidence"] == (
            "the document's own outline entry {!r}".format(entry["section"])
        )
        assert entry["approved_by"] == APPROVER
        assert entry["approved_at"] == APPROVED_AT
        assert entry["matter"] == MATTER
        assert entry["template_id"] == PROGRESS_REPORT.template_id
        assert entry["template_version"] == PROGRESS_REPORT.version
        assert entry["drop_rule"] == f"progress-report:{entry['family_id']}"
        assert entry["doc_id"] and entry["rel_path"] and entry["page_no"]

    # The section an expert reads is the label the DOCUMENT used, verbatim —
    # section number and all. The normalized key it was matched on is beside it
    # under `family`, and the two are deliberately not the same string: an
    # expert checking an omission against the PDF looks for the words the PDF
    # used, and next month the same section is numbered differently.
    assert {e["section"] for e in entries} == {
        "4. HSE STATISTICS", "PHOTO LOG", "ORGANISATION CHART"}
    numbered = next(e for e in entries if e["family_id"] == "hse-statistics")
    assert numbered["section"] == "4. HSE STATISTICS"
    assert numbered["family"] == "HSE STATISTICS"
    assert {e["family_id"] for e in entries} == set(OMITTED)
    # And nothing outside the engaged families reached the log, which is the
    # D-35 class itself: the executive summary and the schedule status were
    # recognized on every one of these documents and neither was omitted.
    assert "EXECUTIVE SUMMARY" not in {e["section"] for e in entries}


def test_the_log_records_what_was_recognized_not_only_what_was_dropped(tmp_path):
    """§5.4 / A-18's per-document ``sections`` block.

    A count of omissions is unreadable without the recognition it came out of:
    a document showing 0 dropped is either a document nothing was recognized in
    or a document an expert chose to keep whole, and those are different facts.
    """
    docs, drops = sectioned(1)
    bundle = build_log(config(tmp_path), docs, drops=drops,
                       stamp=OperatorStamp("a", "t"))
    sections = bundle.content["documents"][0]["sections"]

    assert [row["section"] for row in sections] == [
        "COVER", "EXECUTIVE SUMMARY", "SCHEDULE STATUS",
        "4. HSE STATISTICS", "PHOTO LOG", "ORGANISATION CHART",
    ]
    assert all(row["tier"] == "t1_outline" for row in sections)
    assert [(row["first_page"], row["last_page"]) for row in sections] == [
        (1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (6, 6)]
    assert {row["section"]: row["pages_dropped"] for row in sections} == {
        "COVER": 0,
        "EXECUTIVE SUMMARY": 0,
        "SCHEDULE STATUS": 0,
        "4. HSE STATISTICS": 1,
        "PHOTO LOG": 1,
        "ORGANISATION CHART": 1,
    }
    assert sum(row["pages"] for row in sections) == docs[0].pages_in


def test_a_template_with_no_approvals_still_writes_the_tier_into_the_log(tmp_path):
    """The shipped state, and the one that would have regressed in silence.

    Under D-34 a template arrives UNENGAGED, so the ordinary run — the one a
    freshly-installed DocIQ performs — drops nothing at all. If the tier reached
    the log only through a drop entry, §5.4's "recognition tier belongs in the
    log, per page" would be satisfied by exactly the runs that need it least and
    A-18 would be a field no real run populates. Every assertion below is on a
    run with a template, no approvals and zero drops.
    """
    docs, drops = sectioned(1, approved=())
    assert drops == (), "an unengaged template dropped a page"
    assert docs[0].pages_dropped == 0

    bundle = build_log(config(tmp_path), docs, drops=drops,
                       stamp=OperatorStamp("a", "t"))
    assert bundle.content["drops"] == []
    assert bundle.content["accounting"]["pages_dropped"] == 0

    sections = bundle.content["documents"][0]["sections"]
    assert len(sections) == 6, "recognition vanished when nothing was omitted"
    assert {row["tier"] for row in sections} == {"t1_outline"}
    assert all(row["pages_dropped"] == 0 for row in sections)


# --- summary ---------------------------------------------------------------


def summary_data(docs, unsupported=()):
    before = estimate_for_texts(p.text for d in docs for p in d.pages)
    after = estimate_for_texts(
        p.text for d in docs for p in d.pages if p.disposition is Disposition.KEEP
    )
    return build_summary_data(
        matter_name="Project 495 — QDCPC v Domopan",
        source_root=r"C:\matter\native",
        output_root=r"C:\matter\dociq",
        generated_at="2026-07-30 12:00 UTC",
        operator="abachowski",
        documents=docs,
        unsupported=unsupported,
        tokens_before=before,
        tokens_after=after,
        ocr_threshold_pct=85,
        id_regime="master-index",
        master_index="File Index as of 8Jun26.xlsx",
        bates_note="No Bates stamps detected — absence is normal.",
    )


def test_summary_pdf_is_one_page(tmp_path):
    pypdf = pytest.importorskip("pypdf")
    layout = OutputLayout.at(tmp_path)
    docs = sectioned(4)[0]
    path = write_run_summary(summary_data(docs), layout)
    assert len(pypdf.PdfReader(str(path)).pages) == 1


def test_summary_shows_the_legacy_doc_remediation_hint(tmp_path):
    docs = assigned(2)
    legacy = document("old/report.doc", ())
    data = summary_data(docs, unsupported=(legacy,))
    assert data.has_legacy_doc
    write_run_summary(data, OutputLayout.at(tmp_path))
    assert "Save-As DOCX" in DOC_REMEDIATION_HINT


def test_summary_truncates_long_lists_with_a_stated_remainder(tmp_path):
    docs = assigned(1)
    many = tuple(document(f"old/f{i}.xer", ()) for i in range(20))
    data = summary_data(docs, unsupported=many)
    assert len(data.unsupported_files) == 8
    assert data.unsupported == 20


# --- handoff ---------------------------------------------------------------


def full_matter(tmp_path):
    layout = OutputLayout.at(tmp_path)
    docs, drops = sectioned(3)
    result = write_clean_text(docs, layout)
    write_sources_json(result, layout)
    # ``drops`` is passed on, not dropped on the floor: it is what fills the
    # index's "Omission approved by" column, and a matter folder whose index
    # says three sections were omitted and names nobody is the D-34 failure
    # rendered into the one file a reviewer opens first.
    write_index_csv(build_index_rows(docs, drops=drops), layout)
    write_processing_log(
        build_log(config(tmp_path), docs, drops=drops,
                  stamp=OperatorStamp("a", "t")),
        layout,
    )
    return layout, docs


def test_upload_package_contains_only_the_uploadable_files(tmp_path):
    layout, docs = full_matter(tmp_path)
    pkg = build_upload_package(layout, matter_name="P495", document_count=len(docs))
    names = set(pkg.files)
    assert "sources.json" in names
    assert "document_index.csv" in names
    assert README_NAME in names
    assert "processing_log.json" not in names
    assert "run_summary.pdf" not in names
    assert sum(1 for n in names if n.endswith(".txt") and n != README_NAME) == len(docs)


def test_upload_package_is_rebuilt_not_merged(tmp_path):
    layout, docs = full_matter(tmp_path)
    build_upload_package(layout, document_count=len(docs))
    stale = layout.upload_package / "STALE.txt"
    stale.write_text("old", encoding="utf-8")
    pkg = build_upload_package(layout, document_count=len(docs))
    assert "STALE.txt" not in pkg.files
    assert not stale.exists()


def test_unenforced_limits_are_named(tmp_path):
    layout, docs = full_matter(tmp_path)
    pkg = build_upload_package(layout, document_count=len(docs))
    assert any("file-count limit not enforced" in u for u in pkg.check.unenforced)
    assert pkg.check.ok


def test_oversized_file_is_reported(tmp_path):
    layout, docs = full_matter(tmp_path)
    pkg = build_upload_package(
        layout, document_count=len(docs), limits=ProjectLimits(max_file_bytes=10)
    )
    assert pkg.check.oversized
    assert not pkg.check.ok


def test_readme_states_original_pagination():
    text = render_readme(
        matter_name="P495",
        document_count=3,
        page_count=18,
        date_range="2019–2022",
        estimate=estimate_tokens("x" * 10_000),
        has_bates=False,
        id_regime="LI",
    )
    assert "ORIGINAL native document" in text
    assert "not Bates-stamped" in text


# --- D-20 / A-12: the scope statement, the doc_ids filter, the §8 rule -----


def test_the_scope_statement_is_the_very_first_thing_in_the_readme(tmp_path):
    """FAIL-BEFORE: with the statement appended, or placed under the
    "368 documents, N pages" headline, it is read *after* the belief it exists to
    prevent has already formed. D-20 makes position part of the requirement.

    The headline's page count is left as N on purpose: it is an illustration of
    where the reader's eye lands, not a measurement, and the literal that used to
    stand here (18,521) was superseded by the acceptance run's 18,556.
    ``emit/handoff.py``'s ``render_readme`` docstring carried the same stale
    literal and is now de-literalised too, the file having been released to this
    package."""
    layout, docs = full_matter(tmp_path)
    scope = default_scope_statement(1, "P495")
    pkg = build_upload_package(
        layout, matter_name="P495", doc_ids=(docs[0].doc_id,),
        document_count=1, scope_statement=scope,
    )
    text = pkg.readme.read_text(encoding="utf-8")
    assert text.startswith("SCOPE OF THIS PACKAGE"), text[:120]
    assert text.index("SCOPE OF THIS PACKAGE") < text.index("LI DOCUMENT IQ")


def test_the_scope_statement_is_also_inside_the_pasteable_instructions(tmp_path):
    """The README is one file among hundreds in a Project's knowledge; the
    instruction block is what actually steers the model. A scope that appears
    only in the README is a scope the analysis never sees."""
    layout, docs = full_matter(tmp_path)
    pkg = build_upload_package(
        layout, doc_ids=(docs[0].doc_id,), document_count=1,
        scope_statement="SCOPE OF THIS PACKAGE\n" + "=" * 60 + "\n  MARKER-X\n",
    )
    text = pkg.readme.read_text(encoding="utf-8")
    block = text.split("PROJECT INSTRUCTIONS (paste this block)")[1]
    assert "MARKER-X" in block


def test_a_package_with_no_stated_scope_is_impossible(tmp_path):
    """D-20's core rule. Every package says what it covers, including the one
    nobody scoped — because downstream a silent whole-record package and a
    silent subset are the same artifact."""
    layout, docs = full_matter(tmp_path)
    pkg = build_upload_package(layout, document_count=len(docs))
    assert pkg.scope_statement
    assert "covers ALL" in pkg.scope_statement
    assert pkg.readme.read_text(encoding="utf-8").startswith(
        "SCOPE OF THIS PACKAGE")


def test_doc_ids_selects_exactly_those_documents(tmp_path):
    layout, docs = full_matter(tmp_path)
    chosen = (docs[0].doc_id, docs[2].doc_id)
    pkg = build_upload_package(layout, doc_ids=chosen, scope_statement="S\n")
    texts = {n for n in pkg.files if n.endswith(".txt") and n != README_NAME}
    assert texts == {f"{d}.txt" for d in chosen}
    assert pkg.doc_count == 2
    assert not (layout.upload_package / f"{docs[1].doc_id}.txt").exists()


def test_a_subset_package_filters_sources_json_and_the_index(tmp_path):
    """FAIL-BEFORE: copying both whole hands the reader a manifest naming
    documents the folder does not contain — and §7 makes ``sources.json`` the
    thing Expert Assist reads to FIND text, so each of those names is a path
    that resolves to nothing."""
    layout, docs = full_matter(tmp_path)
    chosen = (docs[0].doc_id,)
    build_upload_package(layout, doc_ids=chosen, scope_statement="S\n")
    root = layout.upload_package

    sources = json.loads((root / "sources.json").read_text(encoding="utf-8"))
    assert set(sources) == set(chosen)

    rows = list(csv.reader((root / "document_index.csv").read_text(
        encoding="utf-8").splitlines()))
    assert rows[0] == list(INDEX_COLUMNS)
    assert [r[0] for r in rows[1:]] == list(chosen)

    # D-34 survives the filtering. The package's index is the matter's index
    # with rows removed, so the omission and the name of the person answerable
    # for it travel together into the folder the recipient actually opens —
    # a subset that kept "3 sections dropped" and lost the approver would be
    # the D-34 failure reintroduced by a copy step.
    cells = dict(zip(INDEX_COLUMNS, rows[1]))
    assert cells["Sections dropped"] == "3"
    assert cells["Omission approved by"] == APPROVER

    # The MATTER folder's copies are untouched — the filtering happens on the
    # way into the package, never to the deliverable itself.
    whole = json.loads(layout.sources_json.read_text(encoding="utf-8"))
    assert len(whole) == len(docs)


def test_a_whole_record_packages_index_is_byte_identical_to_the_matters(tmp_path):
    """Round-trip fidelity: when nothing is excluded, the package's index and
    manifest are byte-for-byte the matter's, so the two are comparable.

    **What this does NOT establish, stated because the probe was run and came
    back green:** it does not distinguish the copy path from the filter path.
    Rewriting a full selection through ``_filtered_sources`` /
    ``_filtered_index_csv`` also produces identical bytes — which is a property
    worth having and is what this asserts. It is not a test that the code took
    the copy branch, and it is not written as one."""
    layout, docs = full_matter(tmp_path)
    build_upload_package(layout, doc_ids=tuple(d.doc_id for d in docs),
                         scope_statement="S\n")
    assert (layout.upload_package / "document_index.csv").read_bytes() == \
        layout.index_csv.read_bytes()
    assert (layout.upload_package / "sources.json").read_bytes() == \
        layout.sources_json.read_bytes()


def test_a_doc_id_with_no_text_file_is_reported_not_swallowed(tmp_path):
    """A package one document short of the scope its own statement claims is a
    smaller version of the D-20 failure, and only the operator can judge it."""
    layout, docs = full_matter(tmp_path)
    pkg = build_upload_package(
        layout, doc_ids=(docs[0].doc_id, "LI-99999"), scope_statement="S\n")
    assert pkg.missing == ("LI-99999",)
    assert pkg.doc_count == 1


def test_the_unsupported_files_are_named_in_the_scope_statement():
    """§5 listed-only files can never be in a Path A package, so a package
    calling itself the complete production while they exist makes exactly the
    claim D-20 forbids, in the one file a reader would trust to know better."""
    plain = default_scope_statement(10, "P495")
    with_listed = default_scope_statement(10, "P495", unsupported=7)
    assert "complete production" in plain
    assert "complete production" not in with_listed
    assert "7 further files" in with_listed


def test_scope_statement_authors_agree_on_the_whole_record_wording():
    """The freeze forbids the GUI importing ``emit``, so §8's whole-record
    wording has two authors by construction. This binds them: if either drifts,
    an operator reads one sentence on screen and the recipient reads another."""
    from dociq.gui.view_models import PackageScope

    for count, matter, listed in ((3, "P495", 0), (368, "matter", 7),
                                  (1, "", 1), (0, "x", 0)):
        assert default_scope_statement(count, matter, listed) == \
            PackageScope().statement(count, count, matter, listed)


# --- §8's "only the sanctioned files" rule ---------------------------------


def test_an_audit_file_in_the_package_is_refused(tmp_path):
    """FAIL-BEFORE: without this the failure mode §8 exists to prevent is
    silent — ``processing_log.json`` uploaded into the evidence corpus, where an
    analyst can quote DocIQ's own internals back as a project record."""
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "LI-00001.txt").write_text("x", encoding="utf-8")
    (root / "processing_log.json").write_text("{}", encoding="utf-8")
    with pytest.raises(PackageContentError) as exc:
        assert_only_sanctioned(root)
    assert "processing_log.json" in str(exc.value)


@pytest.mark.parametrize("intruder", [
    "processing_log.json", "run_summary.pdf", "output_manifest.json",
    "doc_ids_issued.json", "reconciliation.csv", "profile/mpr.v1.yaml",
    "nested/notes.txt", ".dociq/staging_ready.json",
])
def test_every_non_sanctioned_output_is_caught(tmp_path, intruder):
    """The CLASS, enumerated: every §7 deliverable that is NOT one of §8's
    three, plus a nested text file (a top-level-only check would pass it) and
    DocIQ's own run state."""
    root = tmp_path / "pkg"
    (root / intruder).parent.mkdir(parents=True, exist_ok=True)
    (root / intruder).write_text("x", encoding="utf-8")
    with pytest.raises(PackageContentError):
        assert_only_sanctioned(root)


def test_the_sanctioned_set_is_exactly_section_8s_three(tmp_path):
    assert SANCTIONED_NAMES == {"sources.json", "document_index.csv", README_NAME}
    root = tmp_path / "pkg"
    root.mkdir()
    for name in (*SANCTIONED_NAMES, "LI-00001.txt"):
        (root / name).write_text("x", encoding="utf-8")
    assert len(assert_only_sanctioned(root)) == 4


def test_a_built_package_passes_its_own_rule(tmp_path):
    """The global probe, on the real emit path rather than on a planted tree."""
    layout, docs = full_matter(tmp_path)
    build_upload_package(layout, document_count=len(docs))
    assert_only_sanctioned(layout.upload_package)


def test_path_b_layout_is_verified_not_rearranged(tmp_path):
    layout, _ = full_matter(tmp_path)
    ea = expert_assist_layout(layout)
    assert ea.ready
    assert ea.missing == ()
    assert str(layout.root) in ea.instructions


def test_path_b_reports_what_is_missing(tmp_path):
    ea = expert_assist_layout(OutputLayout.at(tmp_path / "empty"))
    assert not ea.ready
    assert "sources.json" in ea.missing


def _pdf_text(path) -> str:
    import pypdf

    return " ".join(
        p.extract_text() or "" for p in pypdf.PdfReader(str(path)).pages
    ).replace("\n", " ")


def test_summary_renders_the_method_this_run_actually_used(tmp_path):
    """Codex review #1, finding B-6.

    The footer used to say token figures came from "a calibrated character
    ratio" on every run — including the default uncalibrated path and the path
    where the ratio band was not used on its own. A provenance line that is
    right by construction is the only kind worth printing on an evidentiary
    deliverable.
    """
    pytest.importorskip("pypdf")
    layout = OutputLayout.at(tmp_path)
    docs = assigned(3)
    data = summary_data(docs)
    text = _pdf_text(write_run_summary(data, layout))
    assert "calibrated character ratio" not in text
    assert data.tokens_after.method_short in text
    assert "How this figure was obtained" in text
    assert "no lower bound" in text.lower() or "no tokenizer was run" in text.lower()


def test_summary_never_calls_the_token_figure_a_floor(tmp_path):
    pytest.importorskip("pypdf")
    layout = OutputLayout.at(tmp_path)
    text = _pdf_text(write_run_summary(summary_data(assigned(3)), layout)).lower()
    for banned in ("at least", "hard floor", "cannot emit fewer",
                   "guaranteed", "a floor, not an estimate"):
        assert banned not in text, banned
    assert "no lower bound" in text, (
        "the absence of a floor is a fact the reader needs stated, not merely "
        "a phrase the PDF avoids"
    )


def test_the_subset_filter_keys_on_doc_ids_not_file_stems(tmp_path, monkeypatch):
    """FAIL-BEFORE: keying the filter on ``Path.stem`` works only while
    ``safe_component`` is the identity on a Doc ID. A future ID that needed
    sanitizing would filter ``sources.json`` and ``document_index.csv`` to
    nothing — a package with text files and an empty manifest, which is worse
    than either failing loudly."""
    import dociq.emit.handoff as h

    layout, docs = full_matter(tmp_path)
    # Force the sanitizer to bite, so stem != doc_id for every document.
    monkeypatch.setattr(h, "safe_component", lambda name: "x_" + name)
    for doc in docs:
        (layout.clean_text / f"x_{doc.doc_id}.txt").write_text("x", encoding="utf-8")

    pkg = h.build_upload_package(
        layout, doc_ids=(docs[0].doc_id,), scope_statement="S\n")
    sources = json.loads(
        (layout.upload_package / "sources.json").read_text(encoding="utf-8"))
    assert set(sources) == {docs[0].doc_id}, (
        "the filter dropped every row — it is keyed on the file name, not the ID"
    )
    assert pkg.missing == ()


# --- A3: a subset package may never ship the WHOLE matter's manifest --------
#
# The recovery branch reintroduced a fallback: when filtering failed for any
# reason, ``build_upload_package`` copied the whole matter's ``sources.json``
# and ``document_index.csv`` into the scoped package. The §8 content check
# cannot see it — ``sources.json`` is a sanctioned *name*, and the file that
# ships is correctly named and wrong inside.
#
# Enumeration of the fallback's triggers, all five of them, each asserted below:
#   sources.json        OSError (a lock — antivirus, or an open handle)
#   sources.json        invalid JSON
#   sources.json        a payload whose top level is not a dict
#   document_index.csv  OSError
#   document_index.csv  empty file (no header row)
# They were the ONLY paths in the package builder that could substitute whole
# content for scoped content: the ``.txt`` copies are per-selected-file, the
# README is generated, and the tree is rebuilt from empty every time.


def _subset_call(layout, docs):
    return lambda: build_upload_package(
        layout, doc_ids=(docs[0].doc_id,), document_count=1,
        scope_statement="S\n")


@pytest.mark.parametrize("break_it,fragment", [
    (lambda l: l.sources_json.write_bytes(b"{not json"), "sources.json"),
    (lambda l: l.sources_json.write_text("[1, 2]", encoding="utf-8"),
     "sources.json"),
    (lambda l: l.index_csv.write_text("", encoding="utf-8"),
     "document_index.csv"),
])
def test_an_unfilterable_manifest_refuses_it_never_copies_the_whole_one(
        tmp_path, break_it, fragment):
    """FAIL-BEFORE: each of these returned ``None`` from the filter and the
    caller fell through to ``shutil.copyfile`` — a 10-document manifest inside a
    1-document package, with no exception raised."""
    layout, docs = full_matter(tmp_path)
    break_it(layout)
    with pytest.raises(PackageContentError) as exc:
        _subset_call(layout, docs)()
    assert fragment in str(exc.value)
    assert "SUBSET" in str(exc.value)
    # And nothing unfiltered was left behind for someone to drag into a Project.
    root = layout.upload_package
    if (root / fragment).is_file():
        assert docs[1].doc_id not in (root / fragment).read_text(encoding="utf-8")


@pytest.mark.parametrize("attr", ["sources_json", "index_csv"])
def test_a_locked_manifest_refuses_rather_than_copying(tmp_path, attr, monkeypatch):
    """The realistic trigger on this machine: antivirus holding a lock, i.e. an
    ``OSError`` from ``read_text``. Simulated at the read, because a real lock
    is not portable — the code path exercised is the same one."""
    layout, docs = full_matter(tmp_path)
    target = getattr(layout, attr)
    real = Path.read_text

    def locked(self, *a, **kw):
        if self == target:
            raise PermissionError(13, "The process cannot access the file")
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", locked)
    with pytest.raises(PackageContentError) as exc:
        _subset_call(layout, docs)()
    assert "could not be read" in str(exc.value)


def test_no_manifest_may_name_a_document_the_package_does_not_hold(tmp_path):
    """The CLASS guard, independent of how the wrong manifest got there.

    ``assert_only_sanctioned`` polices file NAMES; this polices what is inside
    them. It runs on every package, subset or whole, so a future writer that
    reintroduces a whole-matter manifest by some other route is caught by the
    property rather than by a test of the route.
    """
    from dociq.emit.handoff import _assert_manifest_matches_folder

    root = tmp_path / "pkg"
    root.mkdir()
    (root / "LI-00001.txt").write_text("x", encoding="utf-8")
    (root / "sources.json").write_text(
        json.dumps({"LI-00001": "LI-00001.txt", "LI-00002": "LI-00002.txt"}),
        encoding="utf-8")
    with pytest.raises(PackageContentError) as exc:
        _assert_manifest_matches_folder(root, {"LI-00001"}, check_index=True)
    assert "LI-00002" in str(exc.value)
    _assert_manifest_matches_folder(root, {"LI-00001", "LI-00002"}, check_index=True)


def test_a_subset_package_never_copies_a_manifest_at_all(tmp_path, monkeypatch):
    """FAIL-BEFORE at the mechanism rather than at a trigger.

    The three triggers above are the ones that exist today. This one holds
    however the code is rearranged: while a package is scoped, no manifest is
    copied — it is written from filtered content or the build refuses. A new
    fallback would have to call ``copyfile`` to be a fallback.
    """
    import shutil as _sh

    from dociq.emit import handoff as mod

    import contextlib

    layout, docs = full_matter(tmp_path)
    layout.sources_json.write_bytes(b"{not json")
    layout.index_csv.write_text("", encoding="utf-8")

    sources = []
    real = _sh.copyfile

    def watched(src, dst, *a, **kw):
        sources.append(Path(src).name)
        return real(src, dst, *a, **kw)

    monkeypatch.setattr(mod.shutil, "copyfile", watched)
    with contextlib.suppress(PackageContentError):
        _subset_call(layout, docs)()
    assert "sources.json" not in sources and "document_index.csv" not in sources, (
        f"a scoped package copied {sources} — a manifest reached it whole"
    )


def test_the_README_and_the_mode_statement_cannot_disagree(tmp_path):
    """C1, reproduced then closed.

    ``build_upload_package`` computed its verdict against
    ``ProjectLimits.direct_context_tokens`` while ``render_readme`` called the
    bare ``estimate.capacity()``, which falls back to a DIFFERENT literal in
    ``verify.tokens``. With any override the two disagreed, and one reproduced
    package carried:

        mode_statement : "Fits directly in a Claude Project without retrieval
                          mode (about 20% of direct-context capacity...)"
        README         : "About 181-197% of direct-context capacity - the
                          Project will operate in retrieval (RAG) mode."

    The recipient reads the README. This asserts the two are renderings of ONE
    verdict rather than asserting they happen to coincide today, so it fails for
    any future limit that reaches only one of them.

    FAIL-BEFORE, watched RED: restoring the bare ``estimate.capacity()`` in
    ``render_readme`` puts the RAG sentence in the file while the package
    reports "Fits directly".
    """
    layout, docs = full_matter(tmp_path)

    # An estimate ABOVE the 200,000 default, because the defect is invisible
    # otherwise. The fixture matter measures ~500 tokens, so every limit says
    # "fits" and both sides agree however wrong the wiring is — a test built on
    # it would pass against the very defect it exists to catch. This measures a
    # corpus-scale body of text instead, which is the regime a real matter is in.
    big = ["The contractor issued a notice of delay on 15 March 2021 regarding "
           "topside module fabrication sequencing. " * 60 for _ in range(240)]
    estimate = estimate_for_texts(big)
    assert estimate.low > 200_000, (
        "the estimate must exceed the tokens.DIRECT_CONTEXT_TOKENS default, or "
        "neither limit below can disagree with it")

    # FITS under the package's limit; would NOT fit under the 200,000 default.
    generous = build_upload_package(
        layout, matter_name="P495", document_count=len(docs), estimate=estimate,
        limits=ProjectLimits(direct_context_tokens=50_000_000),
    )
    text = generous.readme.read_text(encoding="utf-8")
    assert generous.mode_statement in text, (
        "the package's own capacity verdict is not the one the recipient "
        f"reads.\n  package: {generous.mode_statement}\n  README has: "
        + next((ln.strip() for ln in text.splitlines()
                if "direct-context" in ln or "Fits directly" in ln), "<none>")
    )
    assert "Fits directly" in generous.mode_statement
    assert "retrieval (RAG) mode" not in text

    # And the other direction, so the test is not satisfied by a limit that
    # makes every package fit.
    tight = build_upload_package(
        layout, matter_name="P495", document_count=len(docs), estimate=estimate,
        # Does NOT fit, and at a percentage the 200,000 default cannot produce.
        limits=ProjectLimits(direct_context_tokens=max(1, estimate.low // 10)),
    )
    tight_text = tight.readme.read_text(encoding="utf-8")
    assert tight.mode_statement in tight_text
    assert "retrieval (RAG) mode" in tight.mode_statement
