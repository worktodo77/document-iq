"""Bates detection, the confirmation flow, and the unstamped-matter path."""

from __future__ import annotations

from dociq.contracts import document_sort_key
from dociq.identify.bates import (
    BatesDecision,
    BatesZone,
    DecisionStatus,
    apply_bates,
    detect_candidates,
    document_ranges,
    propose_format,
    ranges_by_sort_key,
)
from tests.fixtures import MPR_PAGES, corpus, document, page


def stamped(prefix="MNFV", start=391, count=4, sep=" ", width=6):
    pages = tuple(
        page(i, f"Body text for page {i}.\n{prefix}{sep}{str(start + i - 1).zfill(width)}")
        for i in range(1, count + 1)
    )
    return document("production/vol1.pdf", pages)


def test_unstamped_matter_proposes_nothing_and_flags_nothing():
    docs = corpus(3)
    assert propose_format(docs) is None
    out = apply_bates(docs, None)
    assert all(p.bates is None for d in out for p in d.pages)
    ranges = document_ranges(out)
    assert all(r.start is None and r.pages_with_bates == 0 for r in ranges.values())


def test_apply_with_no_decision_is_an_identity_on_the_records():
    docs = corpus(2)
    out = apply_bates(docs, None)
    assert out == tuple(sorted(docs, key=document_sort_key))


def test_proposal_reports_evidence():
    doc = stamped()
    proposal = propose_format((doc,))
    assert proposal is not None
    assert proposal.format.prefix == "MNFV"
    assert proposal.pages_matched == 4
    assert proposal.documents_matched == 1
    assert proposal.coverage_pct == 100
    assert proposal.samples[0].startswith("MNFV ")


def test_confirmed_decision_writes_page_bates():
    doc = stamped()
    proposal = propose_format((doc,))
    decision = BatesDecision(
        status=DecisionStatus.CONFIRMED,
        format=proposal.format,
        decided_by="abachowski",
        decided_at="2026-07-30T12:00:00Z",
    )
    out = apply_bates((doc,), decision)
    assert [p.bates for p in out[0].pages] == [
        "MNFV 000391",
        "MNFV 000392",
        "MNFV 000393",
        "MNFV 000394",
    ]


def test_pending_or_rejected_decisions_apply_nothing():
    doc = stamped()
    proposal = propose_format((doc,))
    for status in (DecisionStatus.PENDING, DecisionStatus.REJECTED):
        out = apply_bates((doc,), BatesDecision(status=status, format=proposal.format))
        assert all(p.bates is None for p in out[0].pages)


def test_mixed_digit_widths_in_one_production_are_one_format():
    pages = tuple(
        page(i, f"text\nMNFV {n}")
        for i, n in enumerate(["0391", "0392", "02684", "02685"], start=1)
    )
    doc = document("prod/mixed.pdf", pages)
    proposal = propose_format((doc,))
    assert proposal is not None
    assert set(proposal.format.digit_widths) == {4, 5}
    decision = BatesDecision(DecisionStatus.CONFIRMED, proposal.format)
    out = apply_bates((doc,), decision)
    assert [p.bates for p in out[0].pages] == [
        "MNFV 0391",
        "MNFV 0392",
        "MNFV 02684",
        "MNFV 02685",
    ]


def test_range_orders_numerically_not_lexicographically():
    pages = tuple(
        page(i, f"text\nMNFV {n}")
        for i, n in enumerate(["02684", "0391"], start=1)
    )
    doc = document("prod/mixed.pdf", pages)
    proposal = propose_format((doc,))
    out = apply_bates((doc,), BatesDecision(DecisionStatus.CONFIRMED, proposal.format))
    rng = document_ranges(out)[document_sort_key(out[0])]
    assert (rng.start, rng.end) == ("MNFV 0391", "MNFV 02684")


def test_page_numbers_and_dates_are_not_read_as_bates():
    pages = (
        page(1, "Report\n12"),
        page(2, "Report\n30 June 2019"),
        page(3, "Report\n$1,250.00"),
    )
    assert detect_candidates((document("a.pdf", pages),)) == ()
    assert propose_format((document("a.pdf", pages),)) is None


def test_zone_bounds_are_respected_and_reported():
    pages = tuple(
        page(i, "\n".join(["filler"] * 20 + [f"MNFV 00039{i}"] + ["filler"] * 20))
        for i in (1, 2)
    )
    doc = document("a.pdf", pages)
    assert propose_format((doc,)) is None
    wide = BatesZone(head_lines=25, tail_lines=25)
    assert propose_format((doc,), wide) is not None


def test_single_occurrence_is_not_promoted_to_a_format():
    doc = document("a.pdf", (page(1, "text\nABC 000123"), page(2, "text")))
    assert propose_format((doc,)) is None


def test_ranges_feed_the_stage_3b_tertiary_key():
    doc = stamped()
    proposal = propose_format((doc,))
    out = apply_bates((doc,), BatesDecision(DecisionStatus.CONFIRMED, proposal.format))
    pairs = ranges_by_sort_key(document_ranges(out))
    assert pairs[document_sort_key(out[0])] == ("MNFV 000391", "MNFV 000394")


def test_detection_is_stable_over_repeated_runs():
    doc = stamped(count=12)
    first = propose_format((doc,))
    for _ in range(30):
        assert propose_format((doc,)) == first
