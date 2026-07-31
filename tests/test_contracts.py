"""Tests for the frozen pipeline contract.

These are the freeze's teeth. Every invariant listed in
``docs/contracts/pagemodel_freeze.md`` has a test here that fails if a track
quietly relaxes it. A track that needs different behavior must go through the
amendment procedure — which starts by changing one of these tests, in the open.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from dociq.contracts import (
    CONTRACT_VERSION,
    ContractViolation,
    Disposition,
    DocumentRecord,
    EffectiveLimits,
    IdRegime,
    MasterIndexSnapshot,
    PageKind,
    PageRecord,
    ProcessingStatus,
    ReconciliationReport,
    ReconciliationRow,
    RunConfig,
    RunResult,
    TokenEstimate,
    canonical_json,
    content_hash,
    document_sort_key,
    to_jsonable,
)


def native_page(page_no: int = 1, text: str = "hello") -> PageRecord:
    return PageRecord(page_no=page_no, text=text, kind=PageKind.NATIVE)


def ocr_page(page_no: int = 1, conf: float = 0.91) -> PageRecord:
    return PageRecord(
        page_no=page_no,
        text="scanned",
        kind=PageKind.OCR,
        ocr_conf=conf,
        ocr_line_count=10,
        ocr_low_conf_lines=1,
    )


def doc(pages: tuple[PageRecord, ...], **kw: object) -> DocumentRecord:
    base = dict(
        doc_id="DIQ-000001",
        rel_path="a/b.pdf",
        filename="b.pdf",
        sha256="0" * 64,
        size_bytes=123,
        ext=".pdf",
        pages=pages,
    )
    base.update(kw)
    return DocumentRecord(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Immutability — Track B enriches by copy, never by mutation
# ---------------------------------------------------------------------------


def test_page_record_is_frozen():
    p = native_page()
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.text = "mutated"  # type: ignore[misc]


def test_document_record_is_frozen():
    d = doc((native_page(),))
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.doc_id = "LI-1"  # type: ignore[misc]


def test_evolve_returns_a_new_record_and_leaves_the_original_alone():
    p = native_page()
    q = p.evolve(bates="MNFV 000391")
    assert q.bates == "MNFV 000391"
    assert p.bates is None
    assert q is not p


# ---------------------------------------------------------------------------
# Invariant 2 — KEEP is free, DROP must be attributable (Principle 1)
# ---------------------------------------------------------------------------


def test_keep_is_the_default_disposition():
    assert native_page().disposition is Disposition.KEEP


def test_drop_without_a_rule_is_a_contract_violation():
    p = native_page().evolve(disposition=Disposition.DROP)
    with pytest.raises(ContractViolation, match="unattributable drop"):
        p.validate()


def test_drop_with_a_rule_validates():
    native_page().evolve(
        disposition=Disposition.DROP, drop_rule="modec-mpr/v3#photo-log"
    ).validate()


def test_drop_rule_on_a_kept_page_is_a_contract_violation():
    p = native_page().evolve(drop_rule="modec-mpr/v3#photo-log")
    with pytest.raises(ContractViolation, match="KEEP page"):
        p.validate()


# ---------------------------------------------------------------------------
# OCR confidence coupling
# ---------------------------------------------------------------------------


def test_ocr_page_without_confidence_is_a_contract_violation():
    p = PageRecord(page_no=1, text="x", kind=PageKind.OCR)
    with pytest.raises(ContractViolation, match="must carry ocr_conf"):
        p.validate()


def test_confidence_on_a_native_page_is_a_contract_violation():
    p = PageRecord(page_no=1, text="x", kind=PageKind.NATIVE, ocr_conf=0.9)
    with pytest.raises(ContractViolation, match="non-OCR page"):
        p.validate()


@pytest.mark.parametrize("conf", [-0.01, 1.01])
def test_confidence_outside_the_unit_interval_is_rejected(conf: float):
    with pytest.raises(ContractViolation, match="outside"):
        ocr_page(conf=conf).validate()


def test_low_confidence_lines_cannot_exceed_total_lines():
    p = dataclasses.replace(ocr_page(), ocr_line_count=3, ocr_low_conf_lines=4)
    with pytest.raises(ContractViolation, match="more low-confidence lines"):
        p.validate()


# ---------------------------------------------------------------------------
# Invariant 1 — gapless 1..N page numbering (Principle 2)
# ---------------------------------------------------------------------------


def test_page_numbering_must_start_at_one():
    with pytest.raises(ContractViolation, match="1-based"):
        PageRecord(page_no=0, text="", kind=PageKind.EMPTY).validate()


def test_a_missing_page_is_a_contract_violation():
    # Page 2 dropped from the sequence — exactly the silent page loss that
    # Principle 1 forbids, caught structurally rather than by a later count.
    with pytest.raises(ContractViolation, match="gapless"):
        doc((native_page(1), native_page(3))).validate()


def test_pages_out_of_order_is_a_contract_violation():
    # The OCR pool returns pages by completion order; reassembling by that
    # order instead of page index produces exactly this.
    with pytest.raises(ContractViolation, match="gapless"):
        doc((native_page(2), native_page(1))).validate()


def test_empty_pages_still_count_as_pages():
    d = doc((native_page(1), PageRecord(page_no=2, text="", kind=PageKind.EMPTY)))
    d.validate()
    assert d.pages_in == 2
    assert d.pages_kept == 2


# ---------------------------------------------------------------------------
# Invariant 3 — page accounting
# ---------------------------------------------------------------------------


def test_accounting_balances_across_keep_and_drop():
    d = doc(
        (
            native_page(1),
            native_page(2).evolve(
                disposition=Disposition.DROP, drop_rule="p/v1#hse"
            ),
            native_page(3),
        )
    )
    d.validate()
    assert (d.pages_in, d.pages_kept, d.pages_dropped) == (3, 2, 1)
    assert d.pages_kept + d.pages_dropped == d.pages_in


def test_run_level_accounting_sums_documents():
    cfg = RunConfig(source_root="s", output_root="o")
    d1 = doc((native_page(1), native_page(2)))
    d2 = doc(
        (native_page(1).evolve(disposition=Disposition.DROP, drop_rule="r"),),
        rel_path="a/c.pdf",
        sha256="1" * 64,
    )
    r = RunResult(config=cfg, documents=(d1, d2))
    assert (r.pages_in, r.pages_kept, r.pages_dropped) == (3, 2, 1)


def test_container_child_without_an_order_is_a_contract_violation():
    with pytest.raises(ContractViolation, match="container_order"):
        doc((native_page(),), parent_doc_id="LI-06881").validate()


# ---------------------------------------------------------------------------
# Invariants 4-5 — one serializer; no floats in identity
# ---------------------------------------------------------------------------


def test_identity_projection_drops_ocr_confidence():
    a = ocr_page(conf=0.9101)
    b = ocr_page(conf=0.7702)
    # Same text, different OCR confidence: identical identity, different
    # persisted form. The byte-identical claim must not be hostage to float
    # jitter from the OCR engine.
    assert content_hash(a) == content_hash(b)
    assert canonical_json(a) != canonical_json(b)


def test_persistence_keeps_ocr_confidence():
    assert json.loads(canonical_json(ocr_page(conf=0.9101)))["ocr_conf"] == 0.9101


def test_a_stray_float_in_the_identity_projection_raises():
    # Guards the future: if someone adds a float field to a contract type, the
    # hash path fails loudly instead of silently absorbing the instability.
    with pytest.raises(ContractViolation, match="floats must not appear"):
        to_jsonable({"jitter": 0.1}, for_identity=True)


def test_canonical_json_is_key_sorted_and_compact():
    s = canonical_json({"b": 1, "a": 2})
    assert s == '{"a":2,"b":1}'


def test_canonical_json_is_stable_across_equal_objects():
    assert canonical_json(native_page()) == canonical_json(native_page())


def test_enum_values_serialize_as_their_disk_strings():
    d = json.loads(canonical_json(native_page()))
    assert d["kind"] == "native"
    assert d["disposition"] == "keep"


def test_unserializable_object_is_rejected_rather_than_coerced():
    with pytest.raises(ContractViolation, match="not serializable"):
        canonical_json(object())


# ---------------------------------------------------------------------------
# Invariant 6 — one document order
# ---------------------------------------------------------------------------


def test_documents_sort_by_path_then_hash_then_container_order():
    a = doc((), rel_path="a.pdf", sha256="b" * 64)
    b = doc((), rel_path="a.pdf", sha256="a" * 64)
    c = doc((), rel_path="b.pdf", sha256="a" * 64)
    assert [d.rel_path + d.sha256[0] for d in sorted([c, a, b], key=document_sort_key)] == [
        "a.pdfa",
        "a.pdfb",
        "b.pdfa",
    ]


def test_sort_is_total_for_container_members_sharing_a_path():
    m0 = doc((), rel_path="z.zip", parent_doc_id="LI-1", container_order=0)
    m1 = doc((), rel_path="z.zip", parent_doc_id="LI-1", container_order=1)
    assert [d.container_order for d in sorted([m1, m0], key=document_sort_key)] == [0, 1]


# ---------------------------------------------------------------------------
# D-04 — the ID regime is derived, never set by hand
# ---------------------------------------------------------------------------


def test_id_regime_follows_the_presence_of_a_master_index():
    assert RunConfig(source_root="s", output_root="o").id_regime is IdRegime.NATIVE
    with_index = RunConfig(
        source_root="s",
        output_root="o",
        master_index=MasterIndexSnapshot(
            filename="idx.xlsx", sha256="f" * 64, row_count=9259
        ),
    )
    assert with_index.id_regime is IdRegime.MASTER_INDEX


def test_master_index_participates_in_the_run_identity():
    # D-04 mitigation (a): the index is a hashed run input, so the determinism
    # contract is "same folder + same profile + same index".
    base = RunConfig(source_root="s", output_root="o")
    with_index = dataclasses.replace(
        base,
        master_index=MasterIndexSnapshot(
            filename="idx.xlsx", sha256="f" * 64, row_count=9259
        ),
    )
    assert content_hash(base) != content_hash(with_index)


def test_a_different_index_snapshot_changes_the_run_identity():
    def cfg(h: str) -> RunConfig:
        return RunConfig(
            source_root="s",
            output_root="o",
            master_index=MasterIndexSnapshot("idx.xlsx", h, 9259),
        )

    assert content_hash(cfg("a" * 64)) != content_hash(cfg("b" * 64))


# ---------------------------------------------------------------------------
# Freeze guards
# ---------------------------------------------------------------------------


def test_ocr_threshold_is_an_int_percent_with_a_fraction_view():
    # Stored as int so it can sit in the run identity without putting a float
    # there; exposed as a fraction so it compares directly against ocr_conf.
    cfg = RunConfig(source_root="s", output_root="o")
    assert cfg.ocr_conf_threshold_pct == 85
    assert cfg.ocr_conf_threshold == pytest.approx(0.85)
    assert "ocr_conf_threshold" not in json.loads(canonical_json(cfg))


def test_changing_the_ocr_threshold_changes_the_run_identity():
    a = RunConfig(source_root="s", output_root="o")
    b = dataclasses.replace(a, ocr_conf_threshold_pct=90)
    assert content_hash(a) != content_hash(b)


def test_contract_version_is_the_frozen_one():
    # Bumping this is the amendment procedure's final step, not its first.
    assert CONTRACT_VERSION == "1.4.0"


def test_the_withdrawn_token_floor_is_reserved_and_says_so():
    # A-05(a). A consumer reading 0 must learn "no lower bound was
    # established" — the true state — rather than "the text is empty".
    import pathlib

    src = (pathlib.Path(__file__).parent.parent / "src" / "dociq" / "contracts.py")
    doc = src.read_text(encoding="utf-8")
    assert "RESERVED — do not populate" in doc
    assert estimate().floor_tokens == 0


def test_the_only_asserted_bound_is_the_ceiling():
    e = estimate(chars=1000, structural_tokens=400, token_ceiling=1000)
    assert e.token_ceiling >= e.structural_tokens
    assert e.floor_tokens == 0


def test_structural_and_ceiling_are_ints_and_stay_in_identity():
    assert content_hash(estimate(structural_tokens=400)) != content_hash(
        estimate(structural_tokens=401)
    )
    assert content_hash(estimate(token_ceiling=900)) != content_hash(
        estimate(token_ceiling=901)
    )


# ---------------------------------------------------------------------------
# Amendments A-01 / A-02 (contract 1.1.0)
# ---------------------------------------------------------------------------


def estimate(**kw: object) -> TokenEstimate:
    base = dict(chars=1000, ratio_low=2.3, ratio_high=3.0)
    base.update(kw)
    return TokenEstimate(**base)  # type: ignore[arg-type]


def test_amendment_is_additive_so_existing_construction_still_works():
    # The whole justification for MINOR rather than MAJOR: no existing call
    # site changes. If this fails, the amendment was mis-graded.
    r = RunResult(config=RunConfig(source_root="s", output_root="o"))
    assert (r.tokens_before, r.tokens_after, r.reconciliation) == (None, None, None)


def test_token_ratio_floats_are_excluded_from_identity():
    # Re-ruling D-03 must not invalidate the identity of runs already produced.
    a = estimate(ratio_low=2.3, ratio_high=3.0)
    b = estimate(ratio_low=3.3, ratio_high=3.6)
    assert content_hash(a) == content_hash(b)
    assert canonical_json(a) != canonical_json(b)


def test_token_estimate_still_persists_its_band_and_provenance():
    d = json.loads(canonical_json(estimate(provenance="pre-token proxy")))
    assert d["ratio_low"] == 2.3
    assert d["provenance"] == "pre-token proxy"


def test_char_count_and_floor_stay_in_identity():
    # Ints, and genuinely properties of the text rather than beliefs about it.
    assert content_hash(estimate(chars=1000)) != content_hash(estimate(chars=1001))
    assert content_hash(estimate(floor_tokens=0)) != content_hash(
        estimate(floor_tokens=400)
    )


@pytest.mark.parametrize("lo,hi", [(0.0, 3.0), (-1.0, 3.0), (3.6, 3.3)])
def test_a_nonsensical_ratio_band_is_rejected_at_construction(lo: float, hi: float):
    with pytest.raises(ContractViolation, match="ordered"):
        estimate(ratio_low=lo, ratio_high=hi)


def limits(**kw: object) -> EffectiveLimits:
    base = dict(
        xlsx_max_rows=50000, csv_max_rows=50000, zip_max_mb=500,
        zip_max_members=2000, zip_max_depth=3, file_timeout_s=3600,
        retry_max=500, retry_budget_s=1800, recurse=True,
        ocr_model_id="rapidocr-onnxruntime 1.2.3/abcd", workers=14,
    )
    base.update(kw)
    return EffectiveLimits(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "field",
    ["xlsx_max_rows", "csv_max_rows", "zip_max_mb", "zip_max_members",
     "zip_max_depth", "file_timeout_s", "retry_max", "retry_budget_s",
     "recurse", "ocr_model_id"],
)
def test_every_output_affecting_limit_changes_the_run_identity(field: str):
    # A-04 / Codex B-2. When any of these bites, the same folder+profile+index
    # yields different evidence — so an identical hash across the two would be
    # agreement the bytes do not support.
    base = RunConfig(source_root="s", output_root="o", limits=limits())
    other = dataclasses.replace(base.limits, **{field: _perturb(getattr(base.limits, field))})
    assert content_hash(base) != content_hash(
        dataclasses.replace(base, limits=other)
    )


def _perturb(v: object) -> object:
    if isinstance(v, bool):
        return not v
    if isinstance(v, int):
        return v + 1
    return str(v) + "x"


def test_worker_count_is_recorded_but_not_hashed():
    # Thread-pool width must not change output. If it ever does that is a
    # determinism defect to fix, not a value to absorb into the identity.
    a = RunConfig(source_root="s", output_root="o", limits=limits(workers=4))
    b = RunConfig(source_root="s", output_root="o", limits=limits(workers=16))
    assert content_hash(a) == content_hash(b)
    assert json.loads(canonical_json(a))["limits"]["workers"] == 4


def test_refutation_is_a_field_not_an_inference():
    # A-03. Two estimates identical but for the flag: a consumer cannot tell
    # them apart by arithmetic, which is the whole reason the field exists.
    honest = estimate(chars=1000, floor_tokens=400, ratio_refuted=False)
    refuted = estimate(chars=1000, floor_tokens=400, ratio_refuted=True)
    assert honest.ratio_refuted is False
    assert refuted.ratio_refuted is True
    assert (honest.chars, honest.floor_tokens) == (refuted.chars, refuted.floor_tokens)


def test_refutation_participates_in_identity():
    # It is a bool describing what the run actually did, not a belief about
    # the text — so unlike the ratio band it belongs in the hash.
    assert content_hash(estimate(ratio_refuted=False)) != content_hash(
        estimate(ratio_refuted=True)
    )


def test_reconciliation_categories_partition_the_rows():
    rows = (
        ReconciliationRow("folder-only", "DIQ-1", "a.pdf", ""),
        ReconciliationRow("index-only", "LI-2", "b.pdf", ""),
        ReconciliationRow("field-mismatch", "LI-3", "c.pdf", "size differs"),
        ReconciliationRow("folder-only", "DIQ-4", "d.pdf", ""),
    )
    rep = ReconciliationReport(matched=370, rows=rows)
    assert len(rep.folder_only) == 2
    assert len(rep.index_only) == 1
    assert len(rep.field_mismatch) == 1
    assert len(rep.folder_only) + len(rep.index_only) + len(rep.field_mismatch) == 4


def test_no_index_is_distinguishable_from_an_index_that_found_nothing():
    # These must not render identically: "not checked" and "checked, all clean"
    # are different evidentiary claims.
    cfg = RunConfig(source_root="s", output_root="o")
    assert RunResult(config=cfg).reconciliation is None
    clean = RunResult(config=cfg, reconciliation=ReconciliationReport(matched=375))
    assert clean.reconciliation is not None
    assert clean.reconciliation.rows == ()


def test_contract_imports_nothing_third_party():
    # The contract must stay importable in a bare interpreter so no track
    # inherits another track's dependency set.
    import pathlib

    src = pathlib.Path(__file__).parent.parent / "src" / "dociq" / "contracts.py"
    text = src.read_text(encoding="utf-8")
    forbidden = ("import fitz", "import pypdf", "import PySide6", "import numpy",
                 "import cv2", "import openpyxl", "import yaml")
    assert not [f for f in forbidden if f in text]


@pytest.mark.parametrize(
    "enum_cls,expected",
    [
        (PageKind, {"native", "ocr", "empty", "photo", "synthetic"}),
        (Disposition, {"keep", "drop"}),
        (ProcessingStatus, {"full", "partial-ocr-flagged", "unsupported", "failed"}),
        (IdRegime, {"master-index", "native"}),
    ],
)
def test_enum_disk_values_are_frozen(enum_cls, expected):
    # These strings reach disk. Renaming one changes output bytes and breaks
    # the byte-identical claim against every prior run — a MAJOR contract
    # change however cosmetic it looks.
    assert {m.value for m in enum_cls} == expected
