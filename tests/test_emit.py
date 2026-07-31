"""The §7 deliverables: clean text, index, log, summary, handoff."""

from __future__ import annotations

import csv
import hashlib
import json

import pytest

from dociq.contracts import (
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
    ProjectLimits,
    build_upload_package,
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
from dociq.profiles.apply import apply_profiles
from dociq.profiles.model import OperatorStamp
from dociq.verify.tokens import estimate_for_texts, estimate_tokens
from tests.fixtures import corpus, document, ocr_page, page
from tests.test_profiles import mpr_profile


def assigned(count=3):
    return assign_doc_ids(corpus(count), None).documents


# --- markers ---------------------------------------------------------------


def test_marker_forms():
    assert page_marker(12) == "===== PAGE 12 ====="
    assert page_marker(12, "MNFV 000391") == "===== PAGE 12 [BATES: MNFV 000391] ====="
    assert page_marker(12, "") == "===== PAGE 12 ====="


def test_marker_rejects_a_zero_page():
    with pytest.raises(ContractViolation):
        page_marker(0)


def test_dropped_pages_vanish_but_original_numbers_survive():
    doc = apply_profiles(corpus(1), (mpr_profile(),)).documents[0]
    doc = assign_doc_ids((doc,), None).documents[0]
    body = render_document(doc)
    assert "===== PAGE 3 =====" in body
    assert "===== PAGE 4 =====" not in body  # HSE STATISTICS dropped
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
    docs = (
        document("scan.pdf", (ocr_page(1, "a", 0.60), ocr_page(2, "b", 0.99))),
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
    applied = apply_profiles(corpus(2), (mpr_profile(),))
    docs = assign_doc_ids(applied.documents, None).documents
    drops = tuple(
        d for d in applied.drops
    )
    bundle = build_log(config(tmp_path), docs, drops=drops, profiles=(mpr_profile(),),
                       stamp=OperatorStamp("a", "t"))
    assert len(bundle.content["drops"]) == sum(d.pages_dropped for d in docs)
    assert all(e["rule_id"] for e in bundle.content["drops"])


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
    docs = assign_doc_ids(apply_profiles(corpus(4), (mpr_profile(),)).documents, None).documents
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
    docs = assign_doc_ids(apply_profiles(corpus(3), (mpr_profile(),)).documents, None).documents
    result = write_clean_text(docs, layout)
    write_sources_json(result, layout)
    write_index_csv(build_index_rows(docs), layout)
    write_processing_log(
        build_log(config(tmp_path), docs, stamp=OperatorStamp("a", "t")), layout
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
