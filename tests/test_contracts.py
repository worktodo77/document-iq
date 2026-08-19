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
    needs_ocr_review,
    OCR_REVIEW_MIN_LINES,
    OCR_REVIEW_MIN_CHARS,
    matter_key,
    CONTRACT_VERSION,
    ContractViolation,
    Disposition,
    DocumentRecord,
    EffectiveLimits,
    IdRegime,
    MasterIndexSnapshot,
    OmissionSnapshot,
    PageKind,
    PageRecord,
    ProcessingStatus,
    RecognitionTier,
    TerminalStatus,
    ReconciliationReport,
    ReconciliationRow,
    RunConfig,
    RunResult,
    TokenEstimate,
    canonical_json,
    content_hash,
    document_sort_key,
    run_identity,
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


def dropped_page(page_no: int = 1, text: str = "hello") -> PageRecord:
    """A DROP page as the contract requires one to be built since 1.7.0.

    Every DROP fixture in this file goes through here rather than spelling the
    four fields out, so a future rule about what a DROP page must carry lands in
    one place instead of being satisfied at three call sites and missed at a
    fourth. Amendment A-18 added the third of them — the tier — and the fixture
    that pre-dated it was still constructible and no longer valid.
    """
    return PageRecord(
        page_no=page_no,
        text=text,
        kind=PageKind.NATIVE,
        section="HSE STATISTICS",
        section_tier=RecognitionTier.OUTLINE,
        disposition=Disposition.DROP,
        drop_rule="progress-report:hse-statistics",
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


def test_drop_with_a_rule_and_a_tier_validates():
    """RE-POINTED at the guarantee that replaced this one.

    Until 1.7.0 a rule alone was the whole requirement, and this test asserted
    exactly that. Amendment A-18 raised the bar: a DROP must also record WHICH
    KIND OF EVIDENCE placed the page in its section, because "the document's own
    outline said so" and "a page-class rule matched" are different claims and an
    expert defending the omission has to say which one he is making. The old
    single-field construction is now refused — pinned just below, and pinned
    again from the recognition side in ``tests/test_sections.py``.
    """
    dropped_page().validate()

    rule_but_no_tier = native_page().evolve(
        disposition=Disposition.DROP, drop_rule="modec-mpr/v3#photo-log"
    )
    with pytest.raises(ContractViolation, match="DROP without a section_tier"):
        rule_but_no_tier.validate()


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
    d = doc((native_page(1), dropped_page(2), native_page(3)))
    d.validate()
    assert (d.pages_in, d.pages_kept, d.pages_dropped) == (3, 2, 1)
    assert d.pages_kept + d.pages_dropped == d.pages_in


def test_run_level_accounting_sums_documents():
    # The DROP page here goes through ``dropped_page`` too. It was the one site
    # A-18 did not break — nothing validates it — so it would have gone on
    # constructing a page the contract refuses, and the next test to reach for
    # a two-document fixture would have inherited it.
    cfg = RunConfig(source_root="s", output_root="o")
    d1 = doc((native_page(1), native_page(2)))
    d2 = doc((dropped_page(1),), rel_path="a/c.pdf", sha256="1" * 64)
    d2.validate()
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
    # D-04 mitigation (a): the index is a hashed run input, so it is a term of
    # the determinism contract. The contract used to be stated here as "same
    # folder + same profile + same index"; that clause is withdrawn, because
    # D-35 deleted the engine in which a profile decided which pages dropped
    # and A-19 put the input that replaced it — the approvals, and the project
    # tokens that decide which family a label reaches — into the identity. The
    # index's own membership is what this test is about and is unaffected.
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
    # 85 -> 80 on 2026-08-18, measured. This test is about the int/fraction
    # RELATIONSHIP, not the value; the value is pinned by
    # test_the_threshold_default_is_the_measured_one.
    assert cfg.ocr_conf_threshold_pct == 80
    assert cfg.ocr_conf_threshold == pytest.approx(0.80)
    assert "ocr_conf_threshold" not in json.loads(canonical_json(cfg))


def test_changing_the_ocr_threshold_changes_the_run_identity():
    a = RunConfig(source_root="s", output_root="o")
    b = dataclasses.replace(a, ocr_conf_threshold_pct=90)
    assert content_hash(a) != content_hash(b)


def test_contract_version_is_the_frozen_one_and_every_bump_is_written_up():
    """Bumping this is the amendment procedure's final step, not its first.

    The literal moved 1.6.0 -> 1.8.0 when A-18 (``PageRecord.section_tier``) and
    A-19 (approvals, project tokens and the template in :class:`RunConfig`)
    landed, then -> 1.9.0 when A-19 was extended with ``OmissionSnapshot
    .matter_root``: scope had been keyed on the matter's NAME, and two clients
    each with a `Production` folder are one string. The literal on its own is a weak guard — it fails on the bump and
    passes on the write-up, which is the wrong way round — so this also asserts
    that **every** MINOR up to the current one carries its own entry in the
    contract's amendment history. A bump with no entry now fails here, whatever
    the number is, rather than only the two numbers someone thought to list.
    """
    import pathlib

    assert CONTRACT_VERSION == "2.0.0"

    src = pathlib.Path(__file__).parent.parent / "src" / "dociq" / "contracts.py"
    history = src.read_text(encoding="utf-8")
    major, minor, _patch = (int(part) for part in CONTRACT_VERSION.split("."))
    undocumented = [
        f"1.{m}.0"
        for m in range(1, 10)
        if f"\n1.{m}.0 — amendment" not in history
    ]
    assert not undocumented, (
        f"contract versions bumped with no amendment entry: {undocumented} — "
        "the version literal is the last step of the procedure, and a bump "
        "nobody wrote up is a freeze that was relaxed in private"
    )
    # And the two this sprint added, named, so a renumbered entry is caught.
    assert "1.7.0 — amendment A-18" in history
    assert "1.8.0 — amendment A-19" in history
    assert "1.9.0 — amendment A-19, extended" in history
    assert "2.0.0 " + chr(0x2014) + " amendment A-21" in history, (
        "the first MAJOR bump and the first removal from the frozen "
        "contract is not written up")


# ---------------------------------------------------------------------------
# Amendment A-19 (contract 1.8.0) — the input that decides which pages drop
# ---------------------------------------------------------------------------


def omission(**kw: object) -> OmissionSnapshot:
    base = dict(
        family_id="progress-photographs",
        approved_by="abachowski",
        approved_at="2026-08-17T12:00:00Z",
        matter="Project 495",
        matter_root=matter_key("Project 495"),
        template_id="progress-report",
        template_version="1",
    )
    base.update(kw)
    return OmissionSnapshot(**base)  # type: ignore[arg-type]


def test_an_approved_omission_moves_the_run_identity():
    """A-19, and it is A-08's finding one design generation later.

    A-08 put profiles in the identity because they decided which pages dropped,
    and proved it with two measured counterexamples. D-35 deleted that engine and
    D-34 moved the decision to an approval a named person gives against a
    template family — so approvals are now the deciding input. Two runs over one
    folder, identical in every other recorded term, one of them missing a
    section: the same collision A-08 closed, on the field that replaced the one
    A-08 was about.
    """
    base = RunConfig(source_root="s", output_root="o")
    engaged = dataclasses.replace(base, omissions=(omission(),))
    assert content_hash(base) != content_hash(engaged)
    assert run_identity(base) != run_identity(engaged)


@pytest.mark.parametrize(
    "field",
    ["family_id", "approved_by", "approved_at", "matter", "template_id",
     "template_version"],
)
def test_every_field_of_an_approval_is_hashed(field: str):
    """Enumerated rather than sampled: each field, one at a time.

    ``approved_by`` and ``approved_at`` are in here deliberately and they narrow
    the determinism claim — two runs differing only in WHO approved the omission
    are not byte-identical, because the drop log names the approver. Recording
    the person is the whole of D-34; a claim that had to pretend otherwise would
    be the wrong claim to keep.
    """
    def cfg(**kw: object) -> RunConfig:
        return RunConfig(
            source_root="s", output_root="o", omissions=(omission(**kw),)
        )

    assert content_hash(cfg()) != content_hash(cfg(**{field: "other"}))


def test_the_order_approvals_were_given_in_is_part_of_the_identity():
    # A tuple, like `profiles`, because the log records them in order and two
    # logs that differ are two different artifacts.
    a = omission(family_id="progress-photographs")
    b = omission(family_id="hse-statistics")
    first = RunConfig(source_root="s", output_root="o", omissions=(a, b))
    second = dataclasses.replace(first, omissions=(b, a))
    assert content_hash(first) != content_hash(second)


def test_project_tokens_move_the_run_identity():
    """The half of A-19 that is easiest to miss.

    Tokens are not a display setting: they change which family a label
    normalizes to. With ``MV32`` supplied, ``MV32 APPENDICES`` keys to
    ``APPENDICES`` and an appendices approval reaches it; without, it does not
    and the page keeps. Same folder, same approvals, different corpus.
    """
    base = RunConfig(source_root="s", output_root="o")
    tokenized = dataclasses.replace(base, project_tokens=("MV32",))
    assert content_hash(base) != content_hash(tokenized)
    assert content_hash(tokenized) != content_hash(
        dataclasses.replace(base, project_tokens=("MV32", "BOMESC"))
    )


def test_the_template_is_recorded_even_when_nothing_was_engaged():
    """"The expert engaged nothing" and "no template was offered" are different
    facts about a run, and only one of them is a decision. Recording the
    template beside the (empty) approval set is what keeps them distinguishable
    — a run whose omissions are empty because the template shipped unengaged is
    the ORDINARY state of a freshly-installed DocIQ (D-34)."""
    none_offered = RunConfig(source_root="s", output_root="o")
    offered = dataclasses.replace(
        none_offered, section_template_id="progress-report",
        section_template_version="1",
    )
    assert none_offered.omissions == offered.omissions == ()
    assert content_hash(none_offered) != content_hash(offered)
    assert content_hash(offered) != content_hash(
        dataclasses.replace(offered, section_template_version="2")
    )


def test_the_contract_no_longer_claims_a_profile_rule_can_drop_a_page():
    """The CLAIM withdrawn, not only the code — modelled on
    ``test_the_withdrawn_token_floor_is_reserved_and_says_so``, which pins A-05's
    withdrawal the same way.

    D-35 deleted ``profiles/apply.py``, the only engine in which a profile rule
    could set a disposition. Three sentences in this contract went on asserting
    it afterwards — on :class:`Disposition`, on ``PageRecord.disposition`` and on
    ``PageRecord.drop_rule`` — and the contract is what a future implementer
    reads before writing a DROP. Under D-34 the only thing that can turn a KEEP
    into a DROP is an approval naming a person.

    Prose, deliberately. There is no identifier to grep for: the engine's name is
    gone and what survived it was the description.
    """
    import pathlib

    src = pathlib.Path(__file__).parent.parent / "src" / "dociq" / "contracts.py"
    text = src.read_text(encoding="utf-8")
    # From the first class declaration onward: the live description of the page
    # model. This deliberately excludes ``CONTRACT_VERSION``'s amendment history
    # above it, which SHOULD go on saying that profiles once decided which pages
    # dropped — A-08 is a record of what was true when it was raised, and
    # rewriting history to match the present is the opposite of an audit trail.
    live = text.split("\nclass ", 1)[-1]
    assert len(live) < len(text) and "ContractViolation" not in live.split("\n")[0]
    for claim in ("expert-approved profile rule",
                  "Identifier of the profile rule"):
        assert claim not in live, (
            f"the contract still says {claim!r} — D-35 removed the engine that "
            "made it true and D-34 replaced it with an approval that names a "
            "person"
        )
    assert "ApprovedOmission" in live, (
        "and the replacement must be named where the withdrawn claim stood, or "
        "the next implementer has a prohibition and no route"
    )


def test_a_19_is_additive_so_existing_construction_still_works():
    # The grading check, the same one A-01/A-02 got: MINOR, because every
    # pre-existing call site still constructs.
    cfg = RunConfig(source_root="s", output_root="o")
    assert (cfg.omissions, cfg.project_tokens) == ((), ())
    assert cfg.section_template_id is None
    assert cfg.section_template_version is None


def test_a_run_result_describes_a_completed_run_by_default():
    # A-06 additivity: every pre-existing construction site means "completed".
    r = RunResult(config=RunConfig(source_root="s", output_root="o"))
    assert r.terminal_status is TerminalStatus.COMPLETED
    assert r.terminal_status.complete


@pytest.mark.parametrize(
    "status", [TerminalStatus.BLOCKED, TerminalStatus.CANCELLED]
)
def test_an_aborted_run_is_not_complete(status: TerminalStatus):
    assert not status.complete


def test_completeness_is_carried_but_NOT_part_of_the_corpus_identity():
    """Reversed at 1.6.0 by amendment A-07, and the reversal is the point.

    1.5.0 hashed the terminal status on the reasoning that a cancelled partial
    set and a complete set must not hash identically. Codex's round-2 second
    opinion (B-R2-1) showed the premise does not hold: an incomplete run
    publishes no corpus and no corpus manifest, so the two can never be
    compared — the previous completed manifest simply survives. Hashing an
    invocation property into a corpus identity makes the byte-identical claim
    describe something other than the bytes it covers, and makes rewording an
    operator sentence change the identity of runs already produced.

    What must NOT be given up is that the status is carried at all; that is the
    other half of B-R2-1, and ``test_incomplete_runs.py`` asserts the pipeline
    actually sets it on every path.
    """
    cfg = RunConfig(source_root="s", output_root="o")
    done = RunResult(config=cfg)
    stopped = dataclasses.replace(
        done, terminal_status=TerminalStatus.CANCELLED,
        terminal_status_reason="operator stopped the run",
    )
    assert content_hash(done) == content_hash(stopped), (
        "termination is a property of the invocation, not of a corpus that was "
        "never published")
    # Carried, though — excluding it from identity must not mean dropping it.
    assert stopped.terminal_status is TerminalStatus.CANCELLED
    assert stopped.terminal_status_reason == "operator stopped the run"


def test_the_destination_is_not_part_of_the_run_identity():
    """Amendment A-08, from B-R2-2's internal inconsistency.

    Three parts of one system described the identity differently: this
    projection hashed ``output_root``, the manifest's claim named the output
    folder, and both the processing log and the acceptance harness treated the
    destination as irrelevant — the harness runs the same corpus to two
    different folders and requires one identity. Where a run's results are
    written is not an input that changes them.
    """
    a = RunConfig(source_root="s", output_root="/matters/alpha/out")
    b = RunConfig(source_root="s", output_root="/somewhere/else")
    assert content_hash(a) == content_hash(b)
    assert run_identity(a) == run_identity(b)
    # And the source folder, which IS an input, still moves it.
    assert run_identity(a) != run_identity(
        dataclasses.replace(a, source_root="other"))


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
        zip_max_members=2000, zip_max_depth=3, file_timeout_ms=3_600_000,
        retry_max=500, retry_budget_ms=1_800_000, recurse=True,
        ocr_model_id="rapidocr-onnxruntime 1.2.3/abcd", workers=14,
    )
    base.update(kw)
    return EffectiveLimits(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "field",
    ["xlsx_max_rows", "csv_max_rows", "zip_max_mb", "zip_max_members",
     "zip_max_depth", "file_timeout_ms", "retry_max", "retry_budget_ms",
     "recurse", "ocr_model_id"],
)
def test_every_output_affecting_limit_changes_the_run_identity(field: str):
    # A-04 / Codex B-2. When any of these bites, one identical set of hashed
    # inputs yields different evidence — so an identical hash across the two
    # would be agreement the bytes do not support. (Stated as "the same
    # folder+profile+index" until D-35 removed the profile's power to decide a
    # disposition; the argument never depended on which inputs those were.)
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


# ---------------------------------------------------------------------------
# The OCR review flag — one predicate, measured threshold, blank exclusion
# ---------------------------------------------------------------------------


def _ocr_page(conf: float, chars: int = 900, lines: int = 40) -> PageRecord:
    return PageRecord(page_no=1, text="x" * chars, kind=PageKind.OCR,
                      ocr_conf=conf, ocr_line_count=lines)


def test_the_screen_and_the_log_cannot_disagree_about_which_pages_need_review():
    """The GTG run showed 99 on screen and recorded 80 in the log.

    The screen compared the RAW float (``ocr_conf < 0.85``); the log compared the
    value ROUNDED to a whole percent (``85 < 85`` is false). Nineteen pages with
    confidences like 84.73% were put in front of the operator and left out of the
    audit record — in a tool whose argument is that the log is the auditable
    account of the run.

    Both now call :func:`needs_ocr_review`, so the disagreement is not fixed at
    two call sites, it is unrepresentable.
    """
    page = _ocr_page(0.8473)
    # The boundary that produced the 19: raw is below 85, rounded is not.
    assert page.ocr_conf < 0.85
    assert round(page.ocr_conf * 100) == 85
    assert needs_ocr_review(page, 85) is False, (
        "the predicate must decide on the percent it DISPLAYS, or a page is "
        "marked for review beside a rendered '85%'")
    assert needs_ocr_review(_ocr_page(0.844), 85) is True


def test_a_page_with_nothing_on_it_is_not_sent_for_review():
    """11 of the GTG run's 99 flagged pages carried fewer than 20 characters.

    Their confidences repeated exactly across different documents — 71.76% three
    times — which is one speck of scanner noise recognized as one token. A
    confidence over two glyphs measures nothing, and a review flag that fires on
    blank pages trains an operator to ignore review flags.
    """
    blank = PageRecord(page_no=1, text="ABC", kind=PageKind.OCR,
                       ocr_conf=0.7176, ocr_line_count=1)
    assert needs_ocr_review(blank, 80) is False
    # ...and it is excluded from REVIEW, not from the RECORD.
    assert len(blank.text.strip()) < OCR_REVIEW_MIN_CHARS


def test_a_dense_page_that_failed_to_read_still_flags():
    """The half the corpus does not exercise, and therefore the half worth
    pinning.

    Few characters across MANY lines is not a blank page — it is a page whose
    reading collapsed. All 84 low-character pages of the GTG run returned 0-2
    lines, so this case is absent there; "the corpus does not exercise it"
    selects nothing, and the failure would arrive on the first matter that scans
    worse than this one.
    """
    failed = PageRecord(page_no=1, text="x" * 30, kind=PageKind.OCR,
                        ocr_conf=0.70, ocr_line_count=40)
    assert failed.ocr_line_count > OCR_REVIEW_MIN_LINES
    assert needs_ocr_review(failed, 80) is True, (
        "a dense page that returned almost nothing was filed as blank")


def test_the_threshold_default_is_the_measured_one():
    """80, not 85. 85 was never calibrated against this engine: the measured
    distribution over 377 OCR pages is one population with a median of 86.3%,
    and 85 sat on the left edge of its modal band, flagging 26.3% of pages.

    Pinned as a literal because it is a hashed identity input — changing it
    changes the run identity of every run that does not set it, which is a
    ruling and not a tuning.
    """
    assert RunConfig(source_root="s", output_root="o").ocr_conf_threshold_pct == 80
