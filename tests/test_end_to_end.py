"""Track B end to end: Stage 3 -> 3b -> 4 -> 5, on stub records.

Track A owns Stages 1-2, so this starts from fixtures. What it proves is the
part Track B is accountable for: that the stages compose, that page accounting
survives them, and that the outputs inside the byte-identical claim really are
byte-identical across repeated runs.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from dociq.contracts import Disposition, RunConfig, document_sort_key
from dociq.docid.assign import assign_doc_ids
from dociq.docid.masterindex import load_master_index
from dociq.docid.reconcile import IssuedIdLedger, detect_renumbering, reconcile
from dociq.emit.cleantext import write_clean_text, write_sources_json
from dociq.emit.handoff import build_upload_package, expert_assist_layout
from dociq.emit.indexbook import (
    build_index_rows,
    write_index_csv,
    write_index_xlsx,
    write_reconciliation_csv,
)
from dociq.emit.log import build_log, write_processing_log
from dociq.emit.paths import OutputLayout
from dociq.emit.summary import build_summary_data, write_run_summary
from dociq.identify.bates import (
    BatesDecision,
    DecisionStatus,
    apply_bates,
    document_ranges,
    propose_format,
    ranges_by_sort_key,
)
from dociq.profiles.apply import apply_profiles
from dociq.profiles.model import OperatorStamp, write_matter_copy
from dociq.verify.tokens import estimate_for_texts
from tests.fixtures import corpus, document, page
from tests.test_docid_assign import write_index
from tests.test_profiles import mpr_profile

STAMP = OperatorStamp("abachowski", "2026-07-30T12:00:00Z", "LI-PC")

INDEX_ROWS = [
    ["1", "MPR-01.pdf", "pdf", r"P 495\reports", "1", "", "", ""],
    ["2", "MPR-02.pdf", "pdf", r"P 495\reports", "1", "", "", ""],
    ["3", "MPR-03.pdf", "pdf", r"P 495\reports", "1", "", "", ""],
    ["4", "absent.pdf", "pdf", r"P 495\reports", "1", "", "", ""],
]


def run_track_b(tmp_path, out_name="out", index=None):
    layout = OutputLayout.at(tmp_path / out_name)
    docs = corpus(3) + (document("loose/note.txt", (page(1, "A loose note."),)),)

    # Stage 3 — Bates (this corpus is unstamped, the normal case)
    proposal = propose_format(docs)
    decision = (
        BatesDecision(DecisionStatus.CONFIRMED, proposal.format, "abachowski", STAMP.saved_at)
        if proposal
        else None
    )
    docs = apply_bates(docs, decision)
    ranges = document_ranges(docs)

    # Stage 3b — identity
    assignment = assign_doc_ids(docs, index, bates_ranges=ranges_by_sort_key(ranges))
    docs = assignment.documents
    report = reconcile(assignment, index, bates_ranges=ranges)

    # Stage 4 — KEEP/DROP
    profile = mpr_profile()
    applied = apply_profiles(docs, (profile,))
    docs = applied.documents

    # Stage 5 — emit
    text_result = write_clean_text(docs, layout)
    write_sources_json(text_result, layout)
    rows = build_index_rows(docs, bates_ranges=ranges)
    write_index_csv(rows, layout)
    write_reconciliation_csv(report, layout)
    write_index_xlsx(rows, layout, report, matter_name="Project 495")
    write_matter_copy(profile, layout.root)

    config = RunConfig(
        source_root=str(tmp_path / "native"),
        output_root=str(layout.root),
        profile_id=profile.profile_id,
        profile_version=profile.version,
        master_index=index.snapshot if index else None,
        bates_pattern=decision.pattern() if decision else None,
    )
    ledger = IssuedIdLedger.from_assignment(assignment, config.master_index)
    ledger.write(layout.issued_ids)

    bundle = build_log(
        config,
        docs,
        assignment=assignment,
        reconciliation=report,
        drops=applied.drops,
        profiles=(profile,),
        bates_decision=decision,
        bates_ranges=ranges,
        token_estimate=estimate_for_texts(
            p.text for d in docs for p in d.pages if p.disposition is Disposition.KEEP
        ),
        warnings=assignment.warnings + applied.warnings,
        stamp=STAMP,
    )
    write_processing_log(bundle, layout)

    before = estimate_for_texts(p.text for d in docs for p in d.pages)
    after = estimate_for_texts(
        p.text for d in docs for p in d.pages if p.disposition is Disposition.KEEP
    )
    write_run_summary(
        build_summary_data(
            matter_name="Project 495 — QDCPC v Domopan",
            source_root=config.source_root,
            output_root=config.output_root,
            generated_at=STAMP.saved_at,
            operator=STAMP.username,
            documents=docs,
            unsupported=(),
            tokens_before=before,
            tokens_after=after,
            ocr_threshold_pct=config.ocr_conf_threshold_pct,
            id_regime=config.id_regime.value,
            master_index=index.snapshot.filename if index else None,
            bates_note="No Bates stamps detected — absence is normal (§4 Stage 3).",
            warnings=bundle.content["warnings"],
        ),
        layout,
    )
    build_upload_package(
        layout,
        matter_name="Project 495",
        document_count=len(docs),
        page_count=sum(d.pages_in for d in docs),
        estimate=after,
        id_regime=config.id_regime.value,
    )
    return layout, docs, bundle, report, assignment, ledger


def test_every_output_is_produced(tmp_path):
    layout, docs, bundle, report, _, _ = run_track_b(tmp_path)
    for path in (
        layout.clean_text,
        layout.sources_json,
        layout.index_csv,
        layout.index_xlsx,
        layout.processing_log,
        layout.run_summary,
        layout.upload_package,
        layout.issued_ids,
    ):
        assert path.exists(), path
    assert expert_assist_layout(layout).ready


def test_page_accounting_reconciles_to_zero_discrepancy(tmp_path):
    _, docs, bundle, _, _, _ = run_track_b(tmp_path)
    for doc in docs:
        doc.validate()
    acc = bundle.content["accounting"]
    assert acc["pages_in"] == acc["pages_kept"] + acc["pages_dropped"]
    assert acc["pages_dropped"] == len(bundle.content["drops"]) > 0


def test_markers_use_original_page_numbers(tmp_path):
    layout, docs, _, _, _, _ = run_track_b(tmp_path)
    mpr = next(d for d in docs if d.filename.startswith("MPR"))
    body = layout.clean_text_file(mpr.doc_id).read_text(encoding="utf-8")
    numbers = [int(l.split()[2]) for l in body.split("\n") if l.startswith("===== PAGE")]
    assert numbers == [1, 2, 3]
    assert max(int(p.page_no) for p in mpr.pages) == 6


def _fingerprint(layout) -> str:
    """Hash only what the byte-identical claim actually covers."""
    h = hashlib.sha256()
    for path in sorted(layout.clean_text.glob("*.txt")):
        h.update(path.name.encode("utf-8"))
        h.update(path.read_bytes())
    h.update(layout.sources_json.read_bytes())
    h.update(layout.index_csv.read_bytes())
    log = json.loads(layout.processing_log.read_text(encoding="utf-8"))
    h.update(log["content_sha256"].encode("utf-8"))
    return h.hexdigest()


@pytest.mark.slow
def test_outputs_are_byte_identical_over_repeated_runs(tmp_path):
    digests = {_fingerprint(run_track_b(tmp_path, f"run{i}")[0]) for i in range(8)}
    assert len(digests) == 1


def test_master_index_regime_assigns_li_numbers(tmp_path):
    index = write_index(tmp_path, INDEX_ROWS)
    layout, docs, bundle, report, assignment, _ = run_track_b(tmp_path, "li", index)
    ids = {d.filename: d.doc_id for d in docs}
    assert ids["MPR-01.pdf"] == "LI-00001"
    assert ids["note.txt"].startswith("DIQ-")
    assert report.totals["index_only"] == 1
    assert report.totals["folder_only"] == 1
    assert bundle.content["doc_ids"]["regime"] == "master-index"


def test_rerunning_against_a_renumbered_index_warns(tmp_path):
    index_a = write_index(tmp_path, INDEX_ROWS, name="a.csv")
    shifted = [["1", "inserted.pdf", "pdf", r"P 495\reports", "1", "", "", ""]] + [
        [str(int(r[0]) + 1)] + r[1:] for r in INDEX_ROWS
    ]
    index_b = write_index(tmp_path, shifted, name="b.csv")
    _, _, _, _, _, first = run_track_b(tmp_path, "r1", index_a)
    _, _, _, _, _, second = run_track_b(tmp_path, "r2", index_b)
    warnings = detect_renumbering(first, second)
    assert any(w.kind == "id-moved" for w in warnings)
    assert all("citations" in w.message or "resolves to" in w.message for w in warnings)


def test_upload_package_excludes_the_audit_trail(tmp_path):
    layout, _, _, _, _, _ = run_track_b(tmp_path)
    names = {p.name for p in layout.upload_package.iterdir()}
    assert "processing_log.json" not in names
    assert "run_summary.pdf" not in names
    assert "sources.json" in names
