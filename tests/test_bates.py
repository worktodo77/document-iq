"""Bates detection, the confirmation flow, and the unstamped-matter path."""

from __future__ import annotations

import re

from dociq.contracts import document_sort_key
from dociq.identify.bates import (
    BatesDecision,
    BatesFormat,
    BatesZone,
    DecisionStatus,
    apply_bates,
    detect_candidates,
    document_ranges,
    parse_pattern,
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


def test_two_stray_lines_in_a_large_unstamped_record_propose_nothing():
    """The defect the real Petrobras run exposed.

    D-13 designates that record as the NEGATIVE case: detection must come back
    empty. It did not — two lines that happened to parse as ``CP0001``, in a
    368-document 18,521-page corpus, were enough to propose a format, because
    the only bar was an absolute count of two pages.
    """
    noise = document(
        "reports/long.pdf",
        tuple(
            page(i, f"Ordinary report body for page {i}.\nCP{i:04d}"
                 if i <= 2 else f"Ordinary report body for page {i}.")
            for i in range(1, 201)
        ),
    )
    assert propose_format((noise,)) is None


def test_a_fully_stamped_document_inside_a_large_record_is_still_proposed():
    """The fix must not suppress the case it exists to protect.

    A 306-page stamped disclosure inside an 18,000-page record is 1.7% of the
    corpus and 100% of itself. A corpus-wide coverage floor would discard it;
    the per-document test keeps it.
    """
    haystack = tuple(
        document(
            f"reports/bulk-{n:02d}.pdf",
            tuple(page(i, f"Unstamped body {n}-{i}.") for i in range(1, 31)),
        )
        for n in range(10)
    )
    proposal = propose_format(haystack + (stamped(count=8),))
    assert proposal is not None
    assert proposal.format.prefix == "MNFV"
    assert proposal.best_document_coverage_pct == 100
    assert proposal.coverage_pct <= 3


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


# --- B-5: the confirmed grammar is enforced, complete, and reusable --------


def test_a_confirmed_width_rejects_an_out_of_format_locator():
    """B-5 (i). A confirmed MNFV format allowing four or five digits must not
    accept ``MNFV 1234567890``."""
    train = document(
        "prod/vol1.pdf",
        tuple(page(i, f"text\nMNFV {n}") for i, n in enumerate(["0391", "02684"], 1)),
    )
    proposal = propose_format((train,))
    assert proposal is not None
    assert set(proposal.format.digit_widths) == {4, 5}
    decision = BatesDecision(DecisionStatus.CONFIRMED, proposal.format)

    intruder = document(
        "prod/vol2.pdf",
        (page(1, "text\nMNFV 1234567890"), page(2, "text\nMNFV 0392")),
    )
    out = apply_bates((intruder,), decision)
    assert [p.bates for p in out[0].pages] == [None, "MNFV 0392"]


def test_the_suffix_separator_survives_confirmation():
    """B-5 (ii). ``MNFV 000391-CONF`` must round-trip with its hyphen."""
    doc = document(
        "prod/conf.pdf",
        tuple(page(i, f"text\nMNFV {390 + i:06d}-CONF") for i in range(1, 4)),
    )
    proposal = propose_format((doc,))
    assert proposal is not None
    fmt = proposal.format
    assert fmt.suffix == "CONF"
    assert fmt.suffix_sep == "-"
    assert fmt.label.endswith("-CONF")
    rx = re.compile(fmt.pattern)
    assert rx.match("MNFV 000391-CONF")
    assert not rx.match("MNFV 000391CONF")
    out = apply_bates((doc,), BatesDecision(DecisionStatus.CONFIRMED, fmt))
    assert [p.bates for p in out[0].pages] == [
        "MNFV 000391-CONF",
        "MNFV 000392-CONF",
        "MNFV 000393-CONF",
    ]


def test_a_persisted_pattern_reconstructs_the_complete_format():
    """B-5 (iii). The persisted string is the whole grammar, not a lossy regex."""
    fmt = BatesFormat(
        prefix="MNFV",
        separator=" ",
        digit_widths=(4, 6),
        suffix="CONF",
        suffix_sep="-",
    )
    back = parse_pattern(fmt.pattern)
    assert back == fmt
    # A width span is exactly the loss the token exists to prevent: 4 and 6 are
    # confirmed, 5 is not, and the regex half must say so on its own.
    rx = re.compile(fmt.pattern)
    assert rx.match("MNFV 0391-CONF") and rx.match("MNFV 000391-CONF")
    assert not rx.match("MNFV 00391-CONF")
    assert parse_pattern("^MNFV \\d{4,6}CONF$") is None


def test_a_pattern_that_cannot_be_read_back_is_never_guessed_at():
    """Fail closed is a property of :func:`parse_pattern`, not of one caller."""
    good = BatesFormat("MNFV", " ", (6,)).pattern
    assert parse_pattern(good) is not None
    unreadable = (
        None,
        "",
        "^MNFV \\d{6}$",                       # a bare regex, no token
        good.replace("dociq-bates:1", "dociq-bates:2"),   # a version we cannot read
        good.replace(";w=6", ""),              # a field removed
        good.replace("w=6", "w=six"),          # a width that is not a number
        good.replace("w=6", "w=0"),            # a width no stamp can have
        good.replace(";p=MNFV", ";p=MNFV;p=OTHER"),       # a duplicated field
        good.replace("\\d{6}", "\\d{7}"),      # halves that disagree
        good.replace("p=MNFV", "p=OTHER"),     # halves that disagree, other way
    )
    for pattern in unreadable:
        assert parse_pattern(pattern) is None, pattern


def test_the_persisted_pattern_is_stable_over_repeated_builds():
    doc = document(
        "prod/conf.pdf",
        tuple(page(i, f"text\nMNFV {390 + i:06d}-CONF") for i in range(1, 4)),
    )
    first = propose_format((doc,)).format.pattern
    for _ in range(30):
        fmt = propose_format((doc,)).format
        assert fmt.pattern == first
        assert parse_pattern(fmt.pattern) == fmt


def test_two_suffix_separators_are_two_formats_not_one():
    """The suffix separator is part of the shape, so a production stamping
    ``-CONF`` and one stamping ``_CONF`` cannot be merged and cross-applied."""
    doc = document(
        "prod/mixed.pdf",
        (
            page(1, "text\nMNFV 000391-CONF"),
            page(2, "text\nMNFV 000392-CONF"),
            page(3, "text\nMNFV 000393_CONF"),
        ),
    )
    proposal = propose_format((doc,))
    assert proposal.format.suffix_sep == "-"
    out = apply_bates((doc,), BatesDecision(DecisionStatus.CONFIRMED, proposal.format))
    assert [p.bates for p in out[0].pages] == [
        "MNFV 000391-CONF",
        "MNFV 000392-CONF",
        None,
    ]
