"""The six stages composed — the seam Sprint 1 left uncovered.

Track A proved Stages 1-2, Track B proved Stages 3-5 from stub records, and
nothing proved the join. These tests run :func:`dociq.pipeline.run` over the
real fixture corpus, so every assertion here is about records that came out of a
real extractor and went into the shipped emitters.

OCR is disabled except where a test is about OCR: a full OCR pass over the
fixture corpus costs minutes, and the OCR path has its own proofs in
``test_extract`` and in the self-test.
"""

from __future__ import annotations

import csv
import json

import pytest

from dociq import pipeline
from dociq.contracts import PageKind, RunConfig
from dociq.ingest import extract as ex
from dociq.ingest import walker
from dociq.profiles.model import OperatorStamp
from tests.test_docid_assign import write_index

from .conftest import FIXTURES

STAMP = OperatorStamp("test", "2026-07-30T00:00:00Z", "test-host")

INDEX_ROWS = [
    ["1", "01_native_report.pdf", "pdf", "P 495", "1", "", "", ""],
    ["2", "11_production.zip", "zip", "P 495", "1", "", "", ""],
    ["3", "never_delivered.pdf", "pdf", "P 495", "1", "", "", ""],
]


def _run(tmp_path, name="out", *, index=None, ocr=False, **kw):
    cfg = RunConfig(
        source_root=str(FIXTURES),
        output_root=str(tmp_path / name),
        ocr_engine_version=ex.ocr_engine_version(),
    )
    return pipeline.run(
        cfg,
        pipeline.PipelineOptions(
            walk=walker.WalkOptions(ocr_enabled=ocr, resume=False),
            matter_name="fixture corpus",
            master_index=index,
            stamp=STAMP,
            **kw,
        ),
    )


@pytest.fixture(scope="module")
def outcome(tmp_path_factory):
    return _run(tmp_path_factory.mktemp("pipe"))


# --- deliverables ----------------------------------------------------------


def test_every_section_7_output_is_written(outcome):
    lay = outcome.layout
    for path in (
        lay.clean_text,
        lay.sources_json,
        lay.index_csv,
        lay.index_xlsx,
        lay.processing_log,
        lay.run_summary,
        lay.upload_package,
        lay.issued_ids,
    ):
        assert path.exists(), path


def test_the_accounting_gate_passes_on_a_real_extraction(outcome):
    assert outcome.accounting.ok, outcome.accounting.render()
    r = outcome.result
    assert r.pages_in == r.pages_kept + r.pages_dropped > 0


def test_no_output_is_left_unclassified(outcome):
    assert outcome.manifest.unclassified == []
    assert outcome.manifest.deterministic
    assert outcome.manifest.adjacent


def test_the_claim_covers_the_four_named_artifacts_and_no_more(outcome):
    covered = set(outcome.manifest.deterministic)
    assert "sources.json" in covered
    assert "document_index.csv" in covered
    assert all(
        k.startswith("clean_text/") or k in {"sources.json", "document_index.csv"}
        for k in covered
    )
    assert outcome.manifest.log_content_sha256
    for name in ("run_summary.pdf", "document_index.xlsx"):
        assert name in outcome.manifest.excluded


def test_the_log_records_the_hashes_of_the_deliverables_it_describes(outcome):
    hashes = outcome.log.content["output_hashes"]
    assert hashes["sources.json"] == outcome.manifest.deterministic["sources.json"]
    assert "processing_log.json" not in hashes


# --- the amended contract fields (A-01, A-02, A-03) ------------------------


def test_token_estimates_are_populated_and_carry_their_provenance(outcome):
    r = outcome.result
    assert r.tokens_before is not None and r.tokens_after is not None
    assert r.tokens_before.chars > 0
    assert r.tokens_before.floor_tokens > 0
    assert "PROXY, NOT A TOKENIZER MEASUREMENT" in r.tokens_before.provenance
    assert "Measured on this run" in r.tokens_before.provenance


def test_ratio_refuted_is_the_pipelines_own_verdict_not_a_consumers_guess(outcome):
    """The flag must equal what the estimator decided, not a re-derivation."""
    from dociq.verify.tokens import estimate_for_texts

    measured = estimate_for_texts(
        p.text for d in outcome.result.documents for p in d.pages
    )
    assert outcome.result.tokens_before.ratio_refuted == measured.ratio_refuted
    if measured.ratio_refuted:
        assert "THE RATIO BAND WAS NOT USED" in outcome.result.tokens_before.provenance


def test_reconciliation_is_none_without_a_master_index(outcome):
    assert outcome.result.reconciliation is None


def test_reconciliation_is_populated_with_a_master_index(tmp_path):
    index = write_index(tmp_path, INDEX_ROWS)
    got = _run(tmp_path, "li", index=index)
    rec = got.result.reconciliation
    assert rec is not None
    assert rec.matched == 2
    assert [r.filename for r in rec.index_only] == ["never_delivered.pdf"]
    assert rec.folder_only
    ids = {d.rel_path: d.doc_id for d in got.result.documents}
    assert ids["01_native_report.pdf"] == "LI-00001"
    assert ids["11_production.zip"] == "LI-00002"


# --- the cross-track item: parent_doc_id -----------------------------------


def test_zip_members_carry_a_remapped_parent_doc_id_end_to_end(outcome):
    docs = {d.rel_path: d for d in outcome.result.documents}
    parent = docs["11_production.zip"]
    kids = [d for d in outcome.result.documents
            if d.rel_path.startswith("11_production.zip/")]
    assert kids
    assert all(k.parent_doc_id == parent.doc_id for k in kids)
    assert parent.doc_id and not parent.doc_id.endswith("/")


def test_email_attachments_carry_a_remapped_parent_doc_id_end_to_end(outcome):
    """The attachment case, added by Track A's critic — a different producer in
    the walker from the archive case, so proving one does not prove the other."""
    docs = {d.rel_path: d for d in outcome.result.documents}
    parent = docs["14_transmittal.eml"]
    kids = [d for d in outcome.result.documents
            if d.rel_path.startswith("14_transmittal.eml/")]
    assert kids, "the fixture email carries an attachment"
    assert all(k.parent_doc_id == parent.doc_id for k in kids)
    assert all(k.container_order is not None for k in kids)
    # The attachment is a document in its own right, not text folded into the
    # message body.
    assert sum(k.pages_in for k in kids) == 2


def test_no_document_names_a_parent_that_is_not_in_the_run(outcome):
    """The whole class, not the two instances above."""
    ids = {d.doc_id for d in outcome.result.documents}
    dangling = [
        d.rel_path
        for d in outcome.result.documents
        if d.parent_doc_id is not None and d.parent_doc_id not in ids
    ]
    assert dangling == []


def test_the_index_deliverable_shows_a_doc_id_in_the_parent_column(outcome):
    rows = list(csv.DictReader(
        outcome.layout.index_csv.read_text(encoding="utf-8").splitlines()))
    by_id = {r["Doc ID"]: r for r in rows}
    children = [r for r in rows if r["Parent doc"]]
    assert children
    for row in children:
        assert row["Parent doc"] in by_id
        assert "/" not in row["Parent doc"]


def test_the_processing_log_agrees_with_the_records(outcome):
    payload = json.loads(
        outcome.layout.processing_log.read_text(encoding="utf-8"))
    entries = {e["doc_id"]: e for e in payload["content"]["documents"]}
    for doc in outcome.result.documents:
        assert entries[doc.doc_id]["parent_doc_id"] == doc.parent_doc_id
    assert payload["content"]["accounting"]["pages_in"] == outcome.result.pages_in


# --- re-running a matter into its own folder (D-04 (b)) --------------------


def test_a_rerun_into_the_same_folder_is_byte_identical_to_the_first(tmp_path):
    """The re-run case the determinism contract is actually about.

    Two runs into two fresh folders are the easy version. D-04 (b) re-runs a
    matter into the folder that already holds the previous run's deliverables,
    and residue there must not reach the hashed content.
    """
    first = _run(tmp_path, "same")
    second = _run(tmp_path, "same")
    assert second.stale_removed, "the second run found nothing to replace"
    assert first.log.content_sha256 == second.log.content_sha256
    assert first.manifest.corpus_sha256 == second.manifest.corpus_sha256
    assert first.manifest.deterministic == second.manifest.deterministic


def test_the_purge_is_recorded_outside_the_hashed_content(tmp_path):
    _run(tmp_path, "record")
    second = _run(tmp_path, "record")
    payload = json.loads(
        second.layout.processing_log.read_text(encoding="utf-8"))
    assert payload["run"]["stale_outputs_removed"]
    assert "stale_outputs_removed" not in json.dumps(payload["content"])


def test_the_state_of_the_destination_cannot_change_the_hashed_content(tmp_path):
    """The determinism contract's inputs are the folder, the profile and the
    index. What happens to be sitting in the destination is none of them.

    A corrupt issued-ID ledger left in the output folder produces a real,
    wanted warning — and that warning must not travel inside the hashed content,
    or the byte-identical claim becomes hostage to the destination's history.
    """
    clean = _run(tmp_path, "fresh")
    _run(tmp_path, "used")
    (tmp_path / "used" / "doc_ids_issued.json").write_text(
        "{}", encoding="utf-8", newline="")
    tampered = _run(tmp_path, "used")
    assert any(w.kind == "ledger-unusable" for w in tampered.renumbering)
    assert any("ledger" in w for w in tampered.result.warnings)
    assert tampered.log.content_sha256 == clean.log.content_sha256


def test_a_stale_clean_text_file_from_a_previous_run_cannot_survive(tmp_path):
    """A leftover file under a Doc ID this run gave to a different document is
    worse than a missing one: it sits in the folder Expert Assist reads."""
    first = _run(tmp_path, "stale")
    ghost = first.layout.clean_text / "DIQ-999999.txt"
    ghost.write_text("text from a run that no longer describes this matter\n",
                     encoding="utf-8", newline="")
    second = _run(tmp_path, "stale")
    assert not ghost.exists()
    assert "clean_text/DIQ-999999.txt" not in second.manifest.deterministic
    assert first.manifest.corpus_sha256 == second.manifest.corpus_sha256


def test_a_run_that_skipped_ocr_says_so_in_the_configuration_it_records(tmp_path):
    """RunConfig must describe anything that changed the output bytes.

    Its own docstring makes that the definition of a determinism bug, and
    ``WalkOptions.ocr_enabled`` was one: measured on the real corpus, the same
    folder with the same RunConfig produced 400 OCR pages one way and 400 more
    EMPTY pages the other, while both runs recorded 'rapidocr 1.2.3'.
    """
    with_ocr = _run(tmp_path, "on", ocr=True)
    without = _run(tmp_path, "off", ocr=False)

    assert with_ocr.result.config.ocr_engine == "rapidocr"
    assert without.result.config.ocr_engine == pipeline.OCR_DISABLED
    assert without.result.config.ocr_engine_version == ""
    assert with_ocr.log.content["config"] != without.log.content["config"]
    assert with_ocr.log.content_sha256 != without.log.content_sha256

    kinds = lambda o: {p.kind for d in o.result.documents for p in d.pages}
    assert PageKind.OCR in kinds(with_ocr)
    assert PageKind.OCR not in kinds(without)


# --- Bates: the unstamped corpus is the normal case ------------------------


def test_an_unstamped_corpus_produces_no_bates_and_no_error(outcome):
    assert all(
        p.bates is None for d in outcome.result.documents for p in d.pages
    )
    assert outcome.log.content["bates"]["status"] == "not-run"
    assert outcome.log.content["bates"]["pages_with_bates"] == 0
