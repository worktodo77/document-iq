"""The §7 deliverables: clean text, index, log, summary, handoff."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

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
