"""Stage 3b assignment and §5 reconciliation."""

from __future__ import annotations

import pytest

from dociq.contracts import IdRegime, content_hash, document_sort_key
from dociq.docid.assign import (
    MatchMethod,
    assign_doc_ids,
    index_row_key,
    infer_root_alignment,
    path_key,
)
from dociq.docid.masterindex import load_master_index
from dociq.docid.reconcile import (
    IssuedIdLedger,
    LedgerEntry,
    detect_renumbering,
    reconcile,
)
from tests.fixtures import document, page

HEADERS = [
    "Original Sort ",
    "Filename",
    "File Extension",
    "Filepath",
    "Size\n(KB)",
    "Date",
    "Source Received ",
    "Date Received",
]


def write_index(tmp_path, rows, headers=None, name="index.csv"):
    headers = headers or HEADERS
    lines = [",".join(f'"{h}"' for h in headers)]
    lines += [",".join(f'"{c}"' for c in row) for row in rows]
    p = tmp_path / name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return load_master_index(p)


ROWS = [
    ["1", "Letter 001.pdf", "pdf", r"P 495\20260521\LETTERS", "2", "", "", ""],
    ["2", "Letter 002.pdf", "pdf", r"P 495\20260521\LETTERS", "2", "", "", ""],
    ["3", "MPR June.pdf", "pdf", r"P 495\20260521\REPORTS", "2", "", "", ""],
]


def sample_docs():
    return (
        document("LETTERS/Letter 001.pdf", (page(1, "a"),)),
        document("LETTERS/Letter 002.pdf", (page(1, "b"),)),
        document("REPORTS/MPR June.pdf", (page(1, "c"),)),
    )


# --- path keys -------------------------------------------------------------


def test_path_key_normalizes_separators_and_case():
    assert path_key(r"A\B", "C.pdf") == "a/b/c.pdf"
    assert path_key("a//b/./c.PDF") == "a/b/c.pdf"


def test_index_row_key_joins_directory_and_leaf():
    class Row:
        filepath = r"P 495\20260521\LETTERS"
        filename = "Letter 001.pdf"

    assert index_row_key(Row()) == "p 495/20260521/letters/letter 001.pdf"


def test_index_row_key_does_not_double_a_full_path():
    class Row:
        filepath = r"P 495\LETTERS\Letter 001.pdf"
        filename = "Letter 001.pdf"

    assert index_row_key(Row()) == "p 495/letters/letter 001.pdf"


# --- root alignment --------------------------------------------------------


def test_root_alignment_finds_the_offset():
    index_keys = ["p 495/20260521/letters/a.pdf", "p 495/20260521/letters/b.pdf"]
    align = infer_root_alignment(index_keys, ["letters/a.pdf", "letters/b.pdf"])
    assert align.prefix == "p 495/20260521"
    assert align.matched == 2


def test_root_alignment_is_deterministic_under_a_tie():
    index_keys = ["x/a.pdf", "y/a.pdf"]
    first = infer_root_alignment(index_keys, ["a.pdf"])
    for _ in range(8):
        assert infer_root_alignment(index_keys, ["a.pdf"]) == first
    assert first.ambiguous


# --- assignment ------------------------------------------------------------


def test_matched_files_take_li_numbers(tmp_path):
    index = write_index(tmp_path, ROWS)
    result = assign_doc_ids(sample_docs(), index)
    assert result.regime is IdRegime.MASTER_INDEX
    assert [d.doc_id for d in result.documents] == ["LI-00001", "LI-00002", "LI-00003"]
    assert [d.li_file_no for d in result.documents] == ["1", "2", "3"]
    assert all(a.method == MatchMethod.PATH for a in result.assignments)


def test_unmatched_files_take_diq_numbers(tmp_path):
    index = write_index(tmp_path, ROWS)
    docs = sample_docs() + (document("LETTERS/Extra.pdf", (page(1, "z"),)),)
    result = assign_doc_ids(docs, index)
    ids = {d.rel_path: d.doc_id for d in result.documents}
    assert ids["LETTERS/Extra.pdf"].startswith("DIQ-")
    assert ids["LETTERS/Letter 001.pdf"] == "LI-00001"


def test_no_index_means_every_id_is_diq_native():
    result = assign_doc_ids(sample_docs(), None)
    assert result.regime is IdRegime.NATIVE
    assert all(d.doc_id.startswith("DIQ-") for d in result.documents)
    assert result.alignment is None


def test_assignment_is_stable_across_input_order(tmp_path):
    index = write_index(tmp_path, ROWS)
    docs = list(sample_docs())
    baseline = {
        d.rel_path: d.doc_id for d in assign_doc_ids(docs, index).documents
    }
    for rotation in range(len(docs)):
        shuffled = docs[rotation:] + docs[:rotation]
        got = {d.rel_path: d.doc_id for d in assign_doc_ids(shuffled, index).documents}
        assert got == baseline


def test_container_children_take_parent_derived_ids(tmp_path):
    index = write_index(
        tmp_path,
        [["6881", "bundle.zip", "zip", "P 495", "2", "", "", ""]],
    )
    parent = document("bundle.zip", (page(1, "x"),))
    kids = tuple(
        document(
            f"bundle.zip/member{i}.pdf",
            (page(1, f"m{i}"),),
            parent_doc_id="bundle.zip",
            container_order=i,
        )
        for i in range(3)
    )
    result = assign_doc_ids((parent,) + kids, index)
    ids = {d.rel_path: d.doc_id for d in result.documents}
    assert ids["bundle.zip"] == "LI-06881"
    assert ids["bundle.zip/member0.pdf"] == "LI-06881.01"
    assert ids["bundle.zip/member2.pdf"] == "LI-06881.03"


def test_container_children_have_their_parent_remapped_to_a_doc_id(tmp_path):
    """Stage 1 names the parent by rel_path; after Stage 3b it must be a Doc ID.

    Without the remap the index's "Parent doc" column ships a filesystem path
    while every other identifier column ships a Doc ID, and nothing resolves the
    two against each other.
    """
    index = write_index(
        tmp_path, [["6881", "bundle.zip", "zip", "P 495", "2", "", "", ""]]
    )
    parent = document("bundle.zip", (page(1, "x"),))
    kid = document(
        "bundle.zip/member0.pdf",
        (page(1, "m"),),
        parent_doc_id="bundle.zip",
        container_order=0,
    )
    by_path = {d.rel_path: d for d in assign_doc_ids((parent, kid), index).documents}
    assert by_path["bundle.zip"].parent_doc_id is None
    assert by_path["bundle.zip/member0.pdf"].parent_doc_id == "LI-06881"


def test_nested_children_are_remapped_at_every_level():
    parent = document("outer.zip", (page(1, "x"),))
    inner = document(
        "outer.zip/inner.zip", (page(1, "y"),), parent_doc_id="outer.zip",
        container_order=0,
    )
    leaf = document(
        "outer.zip/inner.zip/a.pdf",
        (page(1, "z"),),
        parent_doc_id="outer.zip/inner.zip",
        container_order=0,
    )
    by_path = {
        d.rel_path: d for d in assign_doc_ids((parent, inner, leaf), None).documents
    }
    assert by_path["outer.zip/inner.zip"].parent_doc_id == "DIQ-000001"
    assert by_path["outer.zip/inner.zip/a.pdf"].parent_doc_id == "DIQ-000001.01"


def test_assignment_is_idempotent_over_an_already_assigned_corpus():
    """Re-running Stage 3b must not orphan every member it previously linked."""
    parent = document("outer.zip", (page(1, "x"),))
    inner = document(
        "outer.zip/inner.zip", (page(1, "y"),), parent_doc_id="outer.zip",
        container_order=0,
    )
    once = assign_doc_ids((parent, inner), None)
    twice = assign_doc_ids(once.documents, None)
    assert not any("not among the scanned documents" in w for w in twice.warnings)
    assert {d.rel_path: d.doc_id for d in twice.documents} == {
        d.rel_path: d.doc_id for d in once.documents
    }


def test_a_detached_member_no_longer_points_at_a_document_that_is_not_there():
    orphan = document(
        "ghost.zip/a.pdf", (page(1, "z"),), parent_doc_id="missing.zip",
        container_order=0,
    )
    result = assign_doc_ids((orphan,), None)
    assert result.documents[0].parent_doc_id is None
    assert any("not among the scanned documents" in w for w in result.warnings)


def test_nested_container_children_nest_their_ids():
    parent = document("outer.zip", (page(1, "x"),))
    inner = document(
        "outer.zip/inner.zip", (page(1, "y"),), parent_doc_id="outer.zip", container_order=0
    )
    leaf = document(
        "outer.zip/inner.zip/a.pdf",
        (page(1, "z"),),
        parent_doc_id="outer.zip/inner.zip",
        container_order=0,
    )
    result = assign_doc_ids((parent, inner, leaf), None)
    ids = {d.rel_path: d.doc_id for d in result.documents}
    assert ids["outer.zip"] == "DIQ-000001"
    assert ids["outer.zip/inner.zip"] == "DIQ-000001.01"
    assert ids["outer.zip/inner.zip/a.pdf"] == "DIQ-000001.01.01"


def test_orphan_child_is_kept_and_reported():
    orphan = document(
        "ghost.zip/a.pdf", (page(1, "z"),), parent_doc_id="missing.zip", container_order=0
    )
    result = assign_doc_ids((orphan,), None)
    assert len(result.documents) == 1
    assert result.documents[0].doc_id.startswith("DIQ-")
    assert any("not among the scanned documents" in w for w in result.warnings)


def test_container_cycle_does_not_hang():
    a = document("a.zip", (page(1, "1"),), parent_doc_id="b.zip", container_order=0)
    b = document("b.zip", (page(1, "2"),), parent_doc_id="a.zip", container_order=0)
    result = assign_doc_ids((a, b), None)
    assert len(result.documents) == 2
    assert any("cycle" in w for w in result.warnings)


def test_hash_is_the_secondary_key(tmp_path):
    doc = document("MOVED/Letter 001.pdf", (page(1, "a"),))
    index = write_index(
        tmp_path,
        [["7", "Letter 001.pdf", "pdf", "SOMEWHERE ELSE", "2", "", "", "", doc.sha256]],
        headers=HEADERS + ["SHA256"],
    )
    result = assign_doc_ids((doc,), index)
    assert result.documents[0].doc_id == "LI-00007"
    assert result.assignments[0].method == MatchMethod.HASH


def test_two_files_cannot_claim_one_index_row(tmp_path):
    """Duplicate content under two names must not both become LI-00007."""
    a = document("A/Letter.pdf", (page(1, "same"),))
    b = document("B/Letter.pdf", (page(1, "same"),), sha256=a.sha256)
    index = write_index(
        tmp_path,
        [["7", "Letter.pdf", "pdf", "A", "2", "", "", "", a.sha256]],
        headers=HEADERS + ["SHA256"],
    )
    result = assign_doc_ids((a, b), index)
    ids = sorted(d.doc_id for d in result.documents)
    assert len(set(ids)) == 2
    assert any(i.startswith("DIQ-") for i in ids)
    assert any("already claimed" in w for w in result.warnings)


def test_duplicate_digests_on_both_sides_are_ambiguous_not_arbitrary(tmp_path):
    """Codex review #1, B-4.

    Two moved files share a digest and two index rows share that same digest.
    Neither pairing is knowable, so neither document may take a legacy ID and
    neither row may be consumed: an arbitrary LI File No. is worse than none.
    """
    a = document("MOVED/Letter A.pdf", (page(1, "same"),))
    b = document("MOVED/Letter B.pdf", (page(1, "same"),), sha256=a.sha256)
    index = write_index(
        tmp_path,
        [
            ["10", "Letter A.pdf", "pdf", "OLD", "2", "", "", "", a.sha256],
            ["11", "Letter B.pdf", "pdf", "OLD", "2", "", "", "", a.sha256],
        ],
        headers=HEADERS + ["SHA256"],
    )
    result = assign_doc_ids((a, b), index)
    ids = sorted(d.doc_id for d in result.documents)
    assert all(i.startswith("DIQ-") for i in ids), ids
    assert result.matched_rows == (), result.matched_rows
    assert all(d.li_file_no is None for d in result.documents)
    joined = " | ".join(result.warnings)
    assert a.sha256[:12] in joined, result.warnings
    assert "2 unmatched document" in joined, result.warnings
    assert "2 unclaimed master-index row" in joined, result.warnings


def test_one_document_against_duplicate_index_digests_is_ambiguous(tmp_path):
    """One side duplicated is enough: the row choice would still be arbitrary."""
    doc = document("MOVED/Letter 001.pdf", (page(1, "a"),))
    index = write_index(
        tmp_path,
        [
            ["10", "Letter 001.pdf", "pdf", "OLD", "2", "", "", "", doc.sha256],
            ["11", "Letter 001 copy.pdf", "pdf", "OLD", "2", "", "", "", doc.sha256],
        ],
        headers=HEADERS + ["SHA256"],
    )
    result = assign_doc_ids((doc,), index)
    assert result.documents[0].doc_id.startswith("DIQ-")
    assert result.matched_rows == ()


def test_the_collision_warning_names_the_method_that_claimed_the_row(tmp_path):
    """The message must not claim a *stronger* key when the key was the same."""
    a = document("A/Letter.pdf", (page(1, "one"),))
    b = document("a/letter.pdf", (page(1, "two"),))
    index = write_index(
        tmp_path,
        [["7", "Letter.pdf", "pdf", "A", "2", "", "", ""]],
    )
    result = assign_doc_ids((a, b), index)
    joined = " | ".join(result.warnings)
    assert "already claimed" in joined, result.warnings
    assert "stronger key" not in joined, result.warnings
    assert MatchMethod.PATH in joined, result.warnings


def test_duplicate_bates_ranges_on_both_sides_are_ambiguous(tmp_path):
    """The tertiary key carries the same defect class as the secondary one."""
    a = document("MOVED/A.pdf", (page(1, "a"),))
    b = document("MOVED/B.pdf", (page(1, "b"),))
    index = write_index(
        tmp_path,
        [
            ["20", "A.pdf", "pdf", "OLD", "2", "", "", "", "LI0001", "LI0009"],
            ["21", "B.pdf", "pdf", "OLD", "2", "", "", "", "LI0001", "LI0009"],
        ],
        headers=HEADERS + ["Bates Start", "Bates End"],
    )
    ranges = {
        document_sort_key(a): ("LI0001", "LI0009"),
        document_sort_key(b): ("LI0001", "LI0009"),
    }
    result = assign_doc_ids((a, b), index, bates_ranges=ranges)
    ids = sorted(d.doc_id for d in result.documents)
    assert all(i.startswith("DIQ-") for i in ids), ids
    assert result.matched_rows == (), result.matched_rows
    joined = " | ".join(result.warnings)
    assert "LI0001" in joined and "LI0009" in joined, result.warnings
    assert "2 unmatched document" in joined, result.warnings


def test_bates_still_matches_when_the_range_is_unique_on_both_sides(tmp_path):
    a = document("MOVED/A.pdf", (page(1, "a"),))
    index = write_index(
        tmp_path,
        [["20", "A.pdf", "pdf", "OLD", "2", "", "", "", "LI0001", "LI0009"]],
        headers=HEADERS + ["Bates Start", "Bates End"],
    )
    result = assign_doc_ids(
        (a,), index, bates_ranges={document_sort_key(a): ("LI0001", "LI0009")}
    )
    assert result.documents[0].doc_id == "LI-00020"
    assert result.assignments[0].method == MatchMethod.BATES


def test_ambiguous_fallback_warnings_are_deterministic(tmp_path):
    a = document("MOVED/Letter A.pdf", (page(1, "same"),))
    b = document("MOVED/Letter B.pdf", (page(1, "same"),), sha256=a.sha256)
    index = write_index(
        tmp_path,
        [
            ["10", "Letter A.pdf", "pdf", "OLD", "2", "", "", "", a.sha256],
            ["11", "Letter B.pdf", "pdf", "OLD", "2", "", "", "", a.sha256],
        ],
        headers=HEADERS + ["SHA256"],
    )
    baseline = assign_doc_ids((a, b), index)
    for _ in range(8):
        again = assign_doc_ids((b, a), index)
        assert again.warnings == baseline.warnings
        assert [d.doc_id for d in again.documents] == [
            d.doc_id for d in baseline.documents
        ]


def test_a_repeated_index_key_is_won_by_the_lowest_original_sort(tmp_path):
    """Sibling of B-4: the warning promises a tie-break the code must perform."""
    index = write_index(
        tmp_path,
        [
            ["9", "Letter 001.pdf", "pdf", r"P 495\20260521\LETTERS", "2", "", "", ""],
            ["4", "Letter 001.pdf", "pdf", r"P 495\20260521\LETTERS", "2", "", "", ""],
        ],
    )
    result = assign_doc_ids(
        (document("LETTERS/Letter 001.pdf", (page(1, "a"),)),), index
    )
    assert result.documents[0].doc_id == "LI-00004"
    assert any("repeat a filepath+filename" in w for w in result.warnings)


def test_an_ambiguous_container_parent_token_is_disclosed():
    """Sibling of B-4: ``token_to_sortkey`` is also a many-to-one map."""
    a = document("bundle.zip", (page(1, "x"),))
    b = document("bundle.zip", (page(1, "y"),))
    child = document(
        "bundle.zip/member.pdf",
        (page(1, "m"),),
        parent_doc_id="bundle.zip",
        container_order=1,
    )
    result = assign_doc_ids((a, b, child), None)
    assert len(result.documents) == 3
    assert any(
        "names more than one scanned document" in w for w in result.warnings
    ), result.warnings


def test_every_document_survives_assignment(tmp_path):
    index = write_index(tmp_path, ROWS)
    docs = sample_docs()
    result = assign_doc_ids(docs, index)
    assert len(result.documents) == len(docs)
    assert len({d.doc_id for d in result.documents}) == len(docs)


# --- reconciliation --------------------------------------------------------


def test_reconciliation_three_way_split(tmp_path):
    index = write_index(tmp_path, ROWS)
    docs = sample_docs()[:2] + (document("LETTERS/Extra.pdf", (page(1, "z"),)),)
    result = assign_doc_ids(docs, index)
    report = reconcile(result, index)
    assert report.totals["matched"] == 2
    assert report.totals["folder_only"] == 1
    assert report.totals["index_only"] == 1
    assert report.index_only[0].filename == "MPR June.pdf"


def test_size_discrepancy_is_flagged(tmp_path):
    index = write_index(
        tmp_path, [["1", "Letter 001.pdf", "pdf", r"P 495\20260521\LETTERS", "500", "", "", ""]]
    )
    doc = document("LETTERS/Letter 001.pdf", (page(1, "a"),), size_bytes=2048)
    report = reconcile(assign_doc_ids((doc,), index), index)
    assert report.discrepancy_count == 1
    assert report.matched[0].discrepancies[0].field == "size"


def test_kilobyte_rounding_is_not_a_discrepancy(tmp_path):
    index = write_index(
        tmp_path, [["1", "Letter 001.pdf", "pdf", r"P 495\20260521\LETTERS", "2", "", "", ""]]
    )
    doc = document("LETTERS/Letter 001.pdf", (page(1, "a"),), size_bytes=2100)
    report = reconcile(assign_doc_ids((doc,), index), index)
    assert report.discrepancy_count == 0


def test_case_only_filename_difference_is_flagged_as_such(tmp_path):
    index = write_index(
        tmp_path, [["1", "LETTER 001.PDF", "pdf", r"P 495\20260521\LETTERS", "2", "", "", ""]]
    )
    doc = document("LETTERS/Letter 001.pdf", (page(1, "a"),))
    report = reconcile(assign_doc_ids((doc,), index), index)
    assert report.matched[0].discrepancies[0].detail == "differs only in letter case"


def test_container_children_are_not_listed_as_folder_only(tmp_path):
    index = write_index(tmp_path, [["1", "bundle.zip", "zip", "P 495", "2", "", "", ""]])
    parent = document("bundle.zip", (page(1, "x"),))
    kid = document(
        "bundle.zip/a.pdf", (page(1, "y"),), parent_doc_id="bundle.zip", container_order=0
    )
    report = reconcile(assign_doc_ids((parent, kid), index), index)
    assert report.totals["folder_only"] == 0
    assert any("container member" in w for w in report.warnings)


def test_no_index_reports_that_reconciliation_did_not_run():
    report = reconcile(assign_doc_ids(sample_docs(), None), None)
    assert report.totals["matched"] == 0
    assert "no master index" in report.warnings[0]


# --- D-04 renumbering ------------------------------------------------------


def test_renumbering_is_warned_when_a_later_snapshot_moves_an_id(tmp_path):
    index_a = write_index(tmp_path, ROWS, name="a.csv")
    rows_b = [["1", "NEW FILE.pdf", "pdf", r"P 495\20260521\LETTERS", "2", "", "", ""]] + [
        [str(int(r[0]) + 1)] + r[1:] for r in ROWS
    ]
    index_b = write_index(tmp_path, rows_b, name="b.csv")

    docs = sample_docs()
    first = IssuedIdLedger.from_assignment(
        assign_doc_ids(docs, index_a), index_a.snapshot
    )
    second = IssuedIdLedger.from_assignment(
        assign_doc_ids(docs, index_b), index_b.snapshot
    )
    warnings = detect_renumbering(first, second)
    kinds = {w.kind for w in warnings}
    assert "id-moved" in kinds
    assert "id-reused" in kinds


def test_no_previous_ledger_means_no_warnings(tmp_path):
    index = write_index(tmp_path, ROWS)
    current = IssuedIdLedger.from_assignment(
        assign_doc_ids(sample_docs(), index), index.snapshot
    )
    assert detect_renumbering(None, current) == ()


def test_identical_reruns_produce_no_renumbering_warnings(tmp_path):
    index = write_index(tmp_path, ROWS)
    docs = sample_docs()
    a = IssuedIdLedger.from_assignment(assign_doc_ids(docs, index), index.snapshot)
    b = IssuedIdLedger.from_assignment(assign_doc_ids(docs, index), index.snapshot)
    assert detect_renumbering(a, b) == ()


def _twin_corpus():
    """Two copies of the same bytes at two paths — the ordinary case.

    A file that also appears inside an archive is the same content twice, and the
    walker detects and reports exactly that. ``document()`` derives the hash from
    path + text, so the hash is pinned explicitly here to make them true twins.
    """
    body = (page(1, "identical content"),)
    sha = "a" * 64
    return (
        document("loose/report.csv", body, sha256=sha),
        document(
            "bundle.zip/report.csv", body, sha256=sha,
            parent_doc_id="bundle.zip", container_order=0,
        ),
        document("bundle.zip", (page(1, "container"),)),
    )


def test_duplicate_content_does_not_manufacture_renumbering_warnings():
    """Two identical runs over a corpus with duplicate content must be silent.

    Keying the previous ledger by sha256 alone lets one twin overwrite the
    other, so every other twin reads as "this file's identifier changed" on a
    re-run where nothing changed. A mitigation that cries wolf on every re-run
    of a real matter record is not a mitigation.
    """
    docs = _twin_corpus()
    a = IssuedIdLedger.from_assignment(assign_doc_ids(docs, None), None)
    b = IssuedIdLedger.from_assignment(assign_doc_ids(docs, None), None)
    assert detect_renumbering(a, b) == ()


def test_a_real_move_is_still_reported_when_the_hash_is_unambiguous():
    """The fix must not buy silence by refusing to look."""
    before = (document("old/name.pdf", (page(1, "x"),), sha256="b" * 64),)
    after = (document("new/name.pdf", (page(1, "x"),), sha256="b" * 64),)
    a = IssuedIdLedger.from_assignment(assign_doc_ids(before, None), None)
    b = IssuedIdLedger.from_assignment(assign_doc_ids(after, None), None)
    a = IssuedIdLedger(
        snapshot=None,
        entries=(LedgerEntry("DIQ-000042", "b" * 64, "old/name.pdf", None),),
        contract_version=a.contract_version,
    )
    a = IssuedIdLedger(
        snapshot=None, entries=a.entries, contract_version=a.contract_version,
        content_sha256=content_hash(a),
    )
    warnings = detect_renumbering(a, b)
    assert [w.kind for w in warnings] == ["id-moved"]
    assert warnings[0].previous_doc_id == "DIQ-000042"


def test_ledger_round_trips_and_detects_tampering(tmp_path):
    index = write_index(tmp_path, ROWS)
    ledger = IssuedIdLedger.from_assignment(
        assign_doc_ids(sample_docs(), index), index.snapshot
    )
    path = ledger.write(tmp_path / "out" / "doc_ids_issued.json")
    reloaded = IssuedIdLedger.read(path)
    assert reloaded == ledger
    assert not reloaded.is_stale()

    tampered = path.read_text(encoding="utf-8").replace("LI-00001", "LI-09999")
    path.write_text(tampered, encoding="utf-8", newline="\n")
    assert IssuedIdLedger.read(path).is_stale()


def test_a_stale_ledger_is_refused_rather_than_trusted(tmp_path):
    index = write_index(tmp_path, ROWS)
    good = IssuedIdLedger.from_assignment(
        assign_doc_ids(sample_docs(), index), index.snapshot
    )
    stale = IssuedIdLedger(
        snapshot=good.snapshot,
        entries=good.entries,
        contract_version=good.contract_version,
        content_sha256="0" * 64,
    )
    warnings = detect_renumbering(stale, good)
    assert len(warnings) == 1
    assert warnings[0].kind == "ledger-unusable"
