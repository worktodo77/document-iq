"""One inventory across every view — Codex review #1, finding B-7.

The finding: only supported ``documents`` went through ID assignment and
``build_index_rows``. Tier-2 records kept an empty Doc ID and never appeared in
``document_index.csv`` / ``.xlsx`` — yet §5 lists ``Unsupported`` as a required
Processing status *of that index*, and the GUI tells the operator unsupported
files are recorded there. On the real corpus the seven legacy ``.doc`` files
were counted in the log and the summary and were absent from the first-class
deliverable.

The fix runs one inventory — documents and unsupported together — through one
:class:`~dociq.docid.ids.DocIdMinter`, which is what keeps acceptance criterion
5 (distinct identifiers, disjoint LI/DIQ namespaces) true by construction
rather than by a second pass that has to be told what the first one used.

Every view that could disagree is asserted here, including the ones that were
already right: the index, the processing log, the run summary, the master-index
reconciliation, the accounting report, ``sources.json`` and the GUI's flag list.
"""

from __future__ import annotations

import csv
import json

import pytest

from dociq import pipeline
from dociq.contracts import ProcessingStatus, RunConfig
from dociq.docid.ids import parse_doc_id
from dociq.emit.indexbook import build_index_rows
from dociq.ingest import extract as ex
from dociq.ingest import walker
from dociq.profiles.model import OperatorStamp
from tests.test_docid_assign import write_index

from .conftest import FIXTURES

STAMP = OperatorStamp("test", "2026-07-30T00:00:00Z", "test-host")


def _run(tmp_path, name="out", *, index=None):
    cfg = RunConfig(
        source_root=str(FIXTURES),
        output_root=str(tmp_path / name),
        ocr_engine_version=ex.ocr_engine_version(),
    )
    return pipeline.run(
        cfg,
        pipeline.PipelineOptions(
            walk=walker.WalkOptions(ocr_enabled=False, resume=False),
            matter_name="fixture corpus",
            master_index=index,
            stamp=STAMP,
        ),
    )


@pytest.fixture(scope="module")
def outcome(tmp_path_factory):
    return _run(tmp_path_factory.mktemp("inventory"))


def _index_rows(outcome):
    return list(csv.DictReader(
        outcome.layout.index_csv.read_text(encoding="utf-8").splitlines()))


# --- the finding itself ----------------------------------------------------


def test_the_fixture_corpus_actually_contains_unsupported_files(outcome):
    """Guard on the guard: every assertion below is vacuous without these."""
    assert outcome.result.unsupported
    assert any(d.ext == ".doc" for d in outcome.result.unsupported)


def test_every_unsupported_file_has_a_document_id(outcome):
    missing = [d.rel_path for d in outcome.result.unsupported if not d.doc_id]
    assert missing == []


def test_every_unsupported_file_has_a_row_in_the_document_index(outcome):
    rows = {r["Doc ID"]: r for r in _index_rows(outcome)}
    for doc in outcome.result.unsupported:
        assert doc.doc_id in rows, f"{doc.rel_path} is missing from the index"
        assert rows[doc.doc_id]["Processing status"] == "Unsupported"
        assert rows[doc.doc_id]["Relative path"] == doc.rel_path
        assert rows[doc.doc_id]["Page count"] == "0"


def test_the_index_carries_the_whole_inventory_and_nothing_else(outcome):
    rows = _index_rows(outcome)
    result = outcome.result
    assert len(rows) == len(result.documents) + len(result.unsupported)
    assert {r["Doc ID"] for r in rows} == (
        {d.doc_id for d in result.documents}
        | {d.doc_id for d in result.unsupported})


def test_an_unsupported_row_carries_no_clean_text_reference(outcome):
    """"Recorded in the index" must not become "claims text we never read"."""
    sources = json.loads(
        outcome.layout.sources_json.read_text(encoding="utf-8"))
    ids = sources if isinstance(sources, dict) else {
        e["doc_id"]: e for e in sources["documents"]}
    for doc in outcome.result.unsupported:
        assert doc.doc_id not in ids
        assert not outcome.layout.clean_text_file(doc.doc_id).exists()


# --- the views that must agree --------------------------------------------


def test_the_index_log_summary_and_accounting_agree_on_one_inventory(outcome):
    result = outcome.result
    log = json.loads(
        outcome.layout.processing_log.read_text(encoding="utf-8"))
    content = log["content"]

    n_docs = len(result.documents)
    n_unsupported = len(result.unsupported)

    assert content["accounting"]["documents"] == n_docs
    assert content["accounting"]["unsupported"] == n_unsupported
    assert len(content["documents"]) == n_docs
    assert len(content["unsupported_files"]) == n_unsupported
    assert outcome.accounting.documents == n_docs
    assert outcome.accounting.unsupported == n_unsupported
    assert len(_index_rows(outcome)) == n_docs + n_unsupported


def test_the_log_records_the_identifier_it_issued_to_every_unsupported_file(outcome):
    content = json.loads(
        outcome.layout.processing_log.read_text(encoding="utf-8"))["content"]
    logged = {e["doc_id"]: e for e in content["unsupported_files"]}
    assert "" not in logged
    for doc in outcome.result.unsupported:
        assert logged[doc.doc_id]["rel_path"] == doc.rel_path
        assert logged[doc.doc_id]["status"] == ProcessingStatus.UNSUPPORTED.value
    # The identifiers are also in the issued-ID ledger, so the D-04 renumbering
    # check covers them: an unsupported file whose ID moved between runs is
    # exactly as citable, and exactly as breakable, as any other.
    ledger = json.loads(
        outcome.layout.issued_ids.read_text(encoding="utf-8"))
    issued = {e["doc_id"] for e in ledger["entries"]}
    assert {d.doc_id for d in outcome.result.unsupported} <= issued


def test_the_run_summary_names_unsupported_files_by_their_doc_id(outcome):
    from dociq.emit.summary import build_summary_data
    from dociq.verify.tokens import estimate_for_texts

    empty = estimate_for_texts(())
    data = build_summary_data(
        matter_name="m", source_root="s", output_root="o",
        generated_at="2026-07-30", operator="t",
        documents=outcome.result.documents,
        unsupported=outcome.result.unsupported,
        tokens_before=empty, tokens_after=empty,
        ocr_threshold_pct=85, id_regime="native")
    assert data.unsupported_files
    for line, doc in zip(data.unsupported_files,
                         sorted(outcome.result.unsupported,
                                key=lambda d: (d.rel_path, d.sha256))):
        assert line.startswith(doc.doc_id)


def test_the_gui_flag_list_shows_the_doc_id_it_promises_is_in_the_index(outcome):
    from dociq.gui.pipeline import Reconciliation, RunOutcome, TokenEstimate
    from dociq.gui.view_models import FLAG_UNSUPPORTED, build_summary

    est = TokenEstimate(chars=1, ratio_low=3.3, ratio_high=3.6)
    view = build_summary(RunOutcome(result=outcome.result, tokens_before=est,
                                    tokens_after=est, output_root="o"))
    group = view.flag(FLAG_UNSUPPORTED)
    assert "document_index.csv" in group.explanation
    ids = {d.doc_id for d in outcome.result.unsupported}
    assert all(any(item.primary.startswith(i) for i in ids)
               for item in group.items)


# --- criterion 5: identifiers stay distinct and the namespaces disjoint -----


def test_the_whole_inventory_gets_distinct_identifiers(outcome):
    ids = ([d.doc_id for d in outcome.result.documents]
           + [d.doc_id for d in outcome.result.unsupported])
    assert len(ids) == len(set(ids))
    li = {i for i in ids if i.startswith("LI-")}
    diq = {i for i in ids if i.startswith("DIQ-")}
    assert li | diq == set(ids)
    assert not (li & diq)
    assert len({parse_doc_id(i) for i in ids}) == len(ids)


def test_a_tier_2_archive_member_takes_a_parent_derived_identifier(outcome):
    """A .doc inside a .zip was previously the worst case: no identifier, no
    index row, and a ``parent_doc_id`` still holding the parent's path."""
    ids = ({d.doc_id for d in outcome.result.documents}
           | {d.doc_id for d in outcome.result.unsupported})
    children = [d for d in outcome.result.unsupported if d.parent_doc_id]
    assert children, "the fixture corpus has a Tier-2 file inside an archive"
    for child in children:
        assert child.parent_doc_id in ids
        assert "/" not in child.parent_doc_id
        assert child.doc_id.startswith(child.parent_doc_id + ".")


def test_an_indexed_unsupported_file_takes_its_li_identifier(tmp_path):
    """It is in the folder AND in the index, so reporting it as index-only was
    a false production gap — and giving it a DIQ number when the index names it
    would put two identifiers on one document across two runs."""
    index = write_index(tmp_path, [
        ["1", "13_legacy.doc", "doc", "P 495", "1", "", "", ""],
    ])
    got = _run(tmp_path, "li", index=index)

    legacy = [d for d in got.result.unsupported
              if d.rel_path == "13_legacy.doc"]
    assert legacy, "the fixture corpus has a top-level legacy .doc"
    assert legacy[0].doc_id == "LI-00001"
    assert legacy[0].li_file_no
    assert [r.filename for r in got.result.reconciliation.index_only] == []


def test_the_inventory_split_is_by_status_and_loses_nothing(outcome):
    """The pipeline splits the assignment result back into documents and
    unsupported on ``status``; no record may land on both sides or neither."""
    result = outcome.result
    assert all(d.status is not ProcessingStatus.UNSUPPORTED
               for d in result.documents)
    assert all(d.status is ProcessingStatus.UNSUPPORTED
               for d in result.unsupported)
    keys = ([(d.rel_path, d.sha256) for d in result.documents]
            + [(d.rel_path, d.sha256) for d in result.unsupported])
    assert len(keys) == len(set(keys))


def test_index_rows_are_built_from_the_inventory_in_canonical_order(outcome):
    """The emitter itself, not just the pipeline's call to it: an index whose
    unsupported rows were appended at the end rather than interleaved would
    sort differently from every other view of the same corpus."""
    rows = build_index_rows(outcome.result.documents + outcome.result.unsupported)
    paths = [r.values[3] for r in rows]
    assert paths == sorted(paths)
    assert len(rows) == len(_index_rows(outcome))


def test_a_rerun_issues_the_same_identifiers_to_the_unsupported_inventory(tmp_path):
    first = _run(tmp_path, "det")
    second = _run(tmp_path, "det")
    assert ({d.rel_path: d.doc_id for d in first.result.unsupported}
            == {d.rel_path: d.doc_id for d in second.result.unsupported})
    assert first.manifest.corpus_sha256 == second.manifest.corpus_sha256
