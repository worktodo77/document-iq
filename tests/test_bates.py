"""Bates detection, the confirmation flow, and the unstamped-matter path."""

from __future__ import annotations

import pytest
import re

from dociq.contracts import document_sort_key
from dociq.identify.bates import (  # noqa: F401
    _parse_line,
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


# ---------------------------------------------------------------------------
# Mixed-case production prefixes — the criterion-4 acceptance finding
# ---------------------------------------------------------------------------
#
# The detector was uppercase-only. The MNFV disclosure's own production prefix
# is `iiCON`, and on 280 sampled pages of it the detector proposed NOTHING and
# scored 0% — every stamp present, correctly placed, and rejected on case
# alone. Worse than a wrong answer, because "no format proposed" is the
# ORDINARY outcome on an unstamped set (D-13), so nothing in the run said a
# word about it. These tests are the class, not the one prefix.


def _seq(prefix, sep, start, count, width=6):
    return document(
        f"prod/{prefix.lower()}.pdf",
        tuple(page(i, f"body text for page {i}\n"
                      f"{prefix}{sep}{start + i - 1:0{width}d}")
              for i in range(1, count + 1)),
    )


def test_a_lowercase_production_prefix_is_detected():
    """`iiCON000001` — the real prefix that scored 0%."""
    doc = _seq("iiCON", "", 1483, 6)
    proposal = propose_format((doc,))
    assert proposal is not None, "a real, fully stamped production proposed nothing"
    assert proposal.format.prefix == "iiCON"
    out = apply_bates((doc,), BatesDecision(DecisionStatus.CONFIRMED,
                                            proposal.format))
    assert [p.bates for p in out[0].pages] == [
        f"iiCON{1483 + i:06d}" for i in range(6)]


@pytest.mark.parametrize("prefix,sep", [
    ("iiCON", ""),          # lowercase-leading, no separator
    ("Def", "-"),           # title case with a separator
    ("PltfBates", " "),     # camel case
    ("mnfv", " "),          # all lowercase
    ("MNFV", " "),          # the uppercase case must not regress
    ("Vol2.Def", "-"),      # digit-bearing prefix, separator required
])
def test_the_prefix_case_class_is_covered_not_just_the_one_prefix(prefix, sep):
    doc = _seq(prefix, sep, 391, 5)
    proposal = propose_format((doc,))
    assert proposal is not None, f"{prefix!r} + {sep!r} proposed nothing"
    assert proposal.format.prefix == prefix
    assert proposal.format.separator == sep


def test_case_is_preserved_and_never_folded():
    """Two productions differing only in case are two formats.

    Folding case would be the lazy fix and it would cross-apply one party's
    numbering to another's pages — a locator pointing at a record that does not
    exist, which §4 rates worse than no locator at all.
    """
    lower = _seq("iiCON", "", 1000, 6)
    upper = document(
        "prod/upper.pdf",
        tuple(page(i, f"text\nIICON{2000 + i:06d}") for i in range(1, 7)),
    )
    proposal = propose_format((lower, upper))
    assert proposal is not None
    out = apply_bates((lower, upper), BatesDecision(DecisionStatus.CONFIRMED,
                                                    proposal.format))
    by_path = {d.rel_path: d for d in out}
    chosen = proposal.format.prefix
    other = "IICON" if chosen == "iiCON" else "iiCON"
    other_doc = by_path[f"prod/{other.lower()}.pdf"]
    assert all(p.bates is None for p in other_doc.pages), (
        f"the {chosen!r} format was applied to {other!r} pages")


def test_a_lowercase_prefix_round_trips_through_the_persisted_pattern():
    doc = _seq("iiCON", "", 1483, 4)
    fmt = propose_format((doc,)).format
    assert parse_pattern(fmt.pattern) == fmt


def test_widening_the_case_class_did_not_open_the_page_number_hole():
    """The widening's cost, measured rather than assumed.

    Allowing lowercase means more footer text parses as stamp-shaped. What
    keeps it from becoming a false positive is unchanged and is asserted here:
    the whole-line anchor, the three-digit floor, and the per-document coverage
    bar.
    """
    for line in ("page 12", "Page 3 of 8", "Exhibit 4", "2024", "1,234.56",
                 "Rev 3", "revised 2024-01-01", "$12,500.00"):
        assert _parse_line(line) is None, line


# ---------------------------------------------------------------------------
# The confirmed stamp folded into a longer OCR line
# ---------------------------------------------------------------------------
#
# From the criterion-4 acceptance run: on OCR'd pages the production's burned-in
# stamp lands inside a longer line, correct and complete, and the whole-line
# anchor rejected it. 72 of 648 sampled pages were missed and every one was an
# OCR page. Detection stays anchored (an open grammar unanchored reads dates and
# dollar figures as Bates numbers); APPLICATION of an already-confirmed format
# does not need to be.
#
# It was a FALLBACK when it was written — an anchored line won outright and only
# a page with none was searched for a folded stamp. D-25 made that unsafe and it
# is now the single rule; see ``test_an_anchored_line_does_not_outrank_a_folded_one``.


def _confirmed(prefix="iiCON", sep="", widths=(6,), suffix=None, suffix_sep=""):
    fmt = BatesFormat(prefix=prefix, separator=sep, digit_widths=widths,
                      suffix=suffix, suffix_sep=suffix_sep)
    return BatesDecision(DecisionStatus.CONFIRMED, fmt)


def test_a_confirmed_stamp_inside_a_longer_ocr_line_is_applied():
    """The two real shapes, verbatim in form from the acceptance run."""
    doc = document("prod/ocr.pdf", (
        page(1, "body\nuntij isfiyed iiCON003944"),
        page(2, "body\niicon Ryan McAllister Project Manager "
                "ryan@example.com 76 S. Sierra Madre Street in iiCON003961"),
    ))
    out = apply_bates((doc,), _confirmed())
    assert [p.bates for p in out[0].pages] == ["iiCON003944", "iiCON003961"]


def test_the_embedded_search_records_the_STAMP_not_the_whole_line():
    doc = document("prod/ocr.pdf",
                   (page(1, "body\nsome noise iiCON003944 more noise"),))
    out = apply_bates((doc,), _confirmed())
    assert out[0].pages[0].bates == "iiCON003944"


def test_the_embedded_search_cannot_match_inside_a_longer_run():
    """`iiCON001483` must not be found inside `XiiCON0014837`."""
    doc = document("prod/ocr.pdf", (
        page(1, "body\ngarbage XiiCON0014837 garbage"),
        page(2, "body\ngarbage iiCON0014831 garbage"),
    ))
    out = apply_bates((doc,), _confirmed())
    assert [p.bates for p in out[0].pages] == [None, None]


def test_two_different_confirmed_stamps_in_one_zone_leave_the_page_unstamped():
    """An ambiguous locator is worse than none (§4). Not a guess, a refusal."""
    doc = document("prod/ocr.pdf",
                   (page(1, "body\nfooter iiCON003944 and iiCON003945"),))
    out = apply_bates((doc,), _confirmed())
    assert out[0].pages[0].bates is None


def test_the_same_stamp_twice_in_one_zone_is_not_ambiguous():
    doc = document("prod/ocr.pdf",
                   (page(1, "header iiCON003944\nbody\nfooter iiCON003944"),))
    out = apply_bates((doc,), _confirmed())
    assert out[0].pages[0].bates == "iiCON003944"


def test_the_embedded_search_honours_the_confirmed_WIDTH():
    """It is the confirmed format that is searched for, not a looser one."""
    doc = document("prod/ocr.pdf", (
        page(1, "body\nnoise iiCON1234567890 noise"),   # 10 digits, not 6
        page(2, "body\nnoise iiCON003944 noise"),       # 6 digits, confirmed
    ))
    out = apply_bates((doc,), _confirmed(widths=(6,)))
    assert [p.bates for p in out[0].pages] == [None, "iiCON003944"]


def test_the_embedded_search_honours_the_confirmed_SUFFIX():
    doc = document("prod/ocr.pdf", (
        page(1, "body\nnoise MNFV 000391 noise"),        # no -CONF
        page(2, "body\nnoise MNFV 000392-CONF noise"),
    ))
    out = apply_bates((doc,), _confirmed(prefix="MNFV", sep=" ", widths=(6,),
                                         suffix="CONF", suffix_sep="-"))
    assert [p.bates for p in out[0].pages] == [None, "MNFV 000392-CONF"]


def test_the_embedded_search_is_confined_to_the_ZONE():
    """A number in the middle of the page is not a footer stamp."""
    body = "\n".join(["header"] * 3 + ["mid-page iiCON003944 mid-page"]
                     + ["filler"] * 8 + ["footer"] * 4)
    doc = document("prod/ocr.pdf", (page(1, body),))
    out = apply_bates((doc,), _confirmed())
    assert out[0].pages[0].bates is None


def test_the_embedded_search_does_not_fire_without_a_confirmed_decision():
    doc = document("prod/ocr.pdf",
                   (page(1, "body\nnoise iiCON003944 noise"),))
    for decision in (None,
                     BatesDecision(DecisionStatus.PENDING),
                     BatesDecision(DecisionStatus.REJECTED)):
        out = apply_bates((doc,), decision)
        assert out[0].pages[0].bates is None


def test_a_zone_line_that_IS_the_stamp_is_recorded_unchanged():
    """No regression: a page whose footer line is the stamp behaves as before."""
    doc = document("prod/native.pdf", (page(1, "body\niiCON003944"),))
    out = apply_bates((doc,), _confirmed())
    assert out[0].pages[0].bates == "iiCON003944"


def test_an_anchored_line_does_not_outrank_a_folded_one():
    """The claim this file used to make — "the anchored path still WINS" — is
    withdrawn, and this is what replaced it.

    Anchored-first was safe only while nothing could add an anchored line to a
    page that already held a folded stamp. D-25's footer re-OCR does exactly
    that, so a band pass that misread one digit would have produced an anchored
    ``iiCON003945`` that beat the folded, correct ``iiCON003944``. There is one
    rule now and it refuses on disagreement.
    """
    doc = document("prod/ocr.pdf", (
        page(1, "body\nfooter iiCON003944 read badly\niiCON003945"),   # disagree
        page(2, "body\nfooter iiCON003944 read badly\niiCON003944"),   # agree
    ))
    out = apply_bates((doc,), _confirmed())
    assert [p.bates for p in out[0].pages] == [None, "iiCON003944"]


# ---------------------------------------------------------------------------
# D-25 — the targeted footer re-OCR, on the detection side
# ---------------------------------------------------------------------------
#
# The extractor crops the stamp band of a page whose ordinary reading yielded
# nothing stamp-shaped, re-reads it at 400 dpi, and APPENDS the stamp-shaped
# tokens it recovers to the tail of the page text. Everything below is about
# the two properties that makes or breaks:
#
#   * the appended block must not evict what was already in the zone — a page
#     that ``apply_bates`` gets right today through the confirmed-token
#     fallback must not become a miss because a second pass added lines;
#   * a recovered token is TEXT, never a locator. It is judged by the same
#     grammar and the same confirmed format as anything else, so a misread
#     footer stays a flagged miss and cannot become a wrong number.


def test_the_tail_zone_reserves_exactly_the_footer_block():
    """The invariant, asserted rather than remembered.

    Raising :data:`FOOTER_BLOCK_MAX_LINES` without raising ``tail_lines`` is
    the change that would silently reintroduce eviction, and it is a one-line
    change someone will make.
    """
    from dociq.identify.bates import _TAIL_LINES_BASE, FOOTER_BLOCK_MAX_LINES

    assert BatesZone().tail_lines == _TAIL_LINES_BASE + FOOTER_BLOCK_MAX_LINES


@pytest.mark.parametrize("original_tail", range(1, 5))
@pytest.mark.parametrize("appended", range(1, 5))
def test_an_appended_footer_block_never_evicts_an_original_zone_line(
        original_tail, appended):
    """The class, over every shape of page the merge can produce.

    Not one repro: every combination of "lines the ordinary pass put in the
    tail" x "tokens the second pass appended", up to both bounds.
    """
    from dociq.identify.bates import FOOTER_BLOCK_MAX_LINES

    assert appended <= FOOTER_BLOCK_MAX_LINES
    body = ["body"] * 12
    tail = [f"original tail {i}" for i in range(original_tail)]
    before = "\n".join(body + tail)
    after = "\n".join(body + tail + [f"iiCON{900000 + i}" for i in range(appended)])
    zone = BatesZone()
    kept = {line for _, line in zone.slice_lines(after)}
    for _, line in zone.slice_lines(before):
        assert line in kept, (
            f"{line!r} fell out of the zone when {appended} token(s) were "
            f"appended — the footer block evicted the ordinary reading")


def test_a_recovered_token_lets_a_folded_stamp_page_be_located():
    """The whole point: the ordinary pass read no stamp, the band pass did."""
    doc = document("prod/ocr.pdf", (page(1, "body\nphotograph, no footer read"),))
    assert apply_bates((doc,), _confirmed())[0].pages[0].bates is None
    recovered = document("prod/ocr.pdf",
                         (page(1, "body\nphotograph, no footer read\niiCON003944"),))
    assert apply_bates((recovered,), _confirmed())[0].pages[0].bates == "iiCON003944"


def test_a_misread_recovered_token_stays_a_MISS_and_never_a_WRONG_number():
    """The failure direction §4 requires, through the D-25 path specifically.

    A band pass that reads ``iCON003944`` for ``iiCON003944`` appends a token
    that is stamp-SHAPED and is not the confirmed format. The page must come
    back unstamped, not stamped with a locator that is not in the production.
    """
    doc = document("prod/ocr.pdf", (page(1, "body\nnothing read\niCON003944"),))
    assert apply_bates((doc,), _confirmed())[0].pages[0].bates is None


def test_a_recovered_token_that_disagrees_with_the_page_leaves_it_unstamped():
    """Two different confirmed-format stamps in the zone is a refusal, and the
    band pass is a new way to produce that state."""
    doc = document("prod/ocr.pdf",
                   (page(1, "body\nfooter iiCON003944 read badly\niiCON003945"),))
    assert apply_bates((doc,), _confirmed())[0].pages[0].bates is None


# --- the trigger ----------------------------------------------------------


def test_zone_has_candidate_is_the_trigger_and_matches_detection():
    """One grammar. Whatever ``detect_candidates`` would accept on a page is
    exactly what makes the second pass unnecessary for that page."""
    from dociq.identify.bates import zone_has_candidate

    for text in ("body\niiCON003944", "MNFV 000391\nbody", "body\nABC-000123"):
        doc = document("a.pdf", (page(1, text),))
        assert zone_has_candidate(text) is True
        assert detect_candidates((doc,)) != ()
    for text in ("body\nno stamp here", "body\n12", "body\n30 June 2019",
                 "body\nuntij isfiyed iiCON003944", ""):
        doc = document("a.pdf", (page(1, text),))
        assert zone_has_candidate(text) is False
        assert detect_candidates((doc,)) == ()


def test_the_trigger_respects_the_zone():
    """A stamp buried mid-page does not spare the page a second look."""
    from dociq.identify.bates import zone_has_candidate

    text = "\n".join(["head"] * 3 + ["iiCON003944"] + ["filler"] * 12
                     + ["foot"] * 8)
    assert zone_has_candidate(text) is False


# --- the token reducer ----------------------------------------------------


def test_stamp_tokens_keeps_a_separated_stamp_whole_or_not_at_all():
    """A separated stamp survives only as a whole line, and that is deliberate.

    Scanned word by word, ``Page 3 of 12 MNFV 000391`` yields the bare
    ``000391`` — the same page, a different locator, and a wrong one. Refusing
    is the failure direction §4 asks for; guessing is not.
    """
    from dociq.identify.bates import stamp_tokens

    assert stamp_tokens("MNFV 000391") == ("MNFV 000391",)
    assert stamp_tokens("MNFV 000391-CONF") == ("MNFV 000391-CONF",)
    # The line is taken exactly as detection would have taken it — prefix and
    # all — and NOT reduced to the bare number. The junk prefix cannot match a
    # confirmed format, so the page stays a miss; ``000391`` would have matched
    # a bare-number production and located the page wrongly.
    assert stamp_tokens("Page 3 of 12    MNFV 000391") == \
        ("Page 3 of 12 MNFV 000391",)
    assert _parse_line("Page 3 of 12 MNFV 000391") is not None
    assert "000391" not in stamp_tokens("Page 3 of 12    MNFV 000391")


def test_stamp_tokens_finds_a_stamp_folded_into_a_line():
    from dociq.identify.bates import stamp_tokens

    assert stamp_tokens("... 76 S. Sierra Madre Street in iiCON003961") == \
        ("iiCON003961",)


def test_stamp_tokens_rejects_what_detection_rejects():
    """Nothing may be recovered here that detection would not have accepted."""
    from dociq.identify.bates import stamp_tokens

    for junk in ("Page 3 of 12", "30 June 2019", "$1,250.00", "12", "", "  ",
                 "Rev. 2 dated 5 May", "issued 30 June 2019 to the Engineer"):
        assert stamp_tokens(junk) == (), junk


def test_stamp_tokens_is_bounded_deduplicated_and_ordered():
    from dociq.identify.bates import FOOTER_BLOCK_MAX_LINES, stamp_tokens

    text = "\n".join(f"iiCON{900000 + i}" for i in range(10))
    got = stamp_tokens(text)
    assert len(got) == FOOTER_BLOCK_MAX_LINES
    assert got == tuple(f"iiCON{900000 + i}" for i in range(FOOTER_BLOCK_MAX_LINES))
    assert stamp_tokens("iiCON003944\niiCON003944") == ("iiCON003944",)


def test_stamp_tokens_is_stable_over_repeated_calls():
    from dociq.identify.bates import stamp_tokens

    text = "noise iiCON003944 noise\nMNFV 000391 tail\niiCON003945"
    assert len({stamp_tokens(text) for _ in range(30)}) == 1


# ---------------------------------------------------------------------------
# D-28 — prefix repair, and the two gates that make it safe
# ---------------------------------------------------------------------------
#
# rapidocr reads this production's stamp DIGITS correctly and cannot resolve its
# PREFIX (D-25, measured). The prefix carries no per-page information — every
# page of a confirmed production has the same one — so D-28 permits repairing
# it, but ONLY where a wrong-series locator is structurally impossible: a matter
# with exactly one proposable prefix has no second series to file a page under.

from dociq.contracts import PageKind  # noqa: E402
from dociq.identify.bates import (  # noqa: E402
    NEAR_MISS_DOUBLED,
    NEAR_MISS_SINGLE_DOUBLED,
    NEAR_MISS_SUBSTITUTION,
    apply_bates_reported,
    matter_prefixes,
    near_miss_rule,
)


def _ocr(n, text):
    return page(n, text).evolve(kind=PageKind.OCR, ocr_conf=0.9)


def _clean_production(prefix="iiCON", n=6, start=900000):
    """A matter whose every page carries the same prefix — the single-prefix
    case D-28 permits repair in."""
    return document("prod/clean.pdf",
                    tuple(_ocr(i, f"body\n{prefix}{start + i:06d}")
                          for i in range(1, n + 1)))


# --- the distance rule, stated and enumerated ------------------------------


@pytest.mark.parametrize("read,rule", [
    ("jiCON", NEAR_MISS_SUBSTITUTION),        # measured on the corpus
    ("liCON", NEAR_MISS_SUBSTITUTION),        # measured
    ("TiCON", NEAR_MISS_SUBSTITUTION),        # measured
    ("IiCON", None),                          # a pure CASE change is not a
    ("iiCOn", None),                          # near miss: iiCON and IICON are
                                              # two formats, deliberately
    ("iCON", NEAR_MISS_DOUBLED),              # measured
    ("iiiCON", NEAR_MISS_SINGLE_DOUBLED),
])
def test_the_near_miss_rule_is_the_stated_class_not_the_measured_strings(
        read, rule):
    assert near_miss_rule(read, "iiCON") == rule


@pytest.mark.parametrize("read", [
    "iiCON",        # exact — a read is not a repair
    "",
    "xxCON",        # two edits
    "iiCOM",        # M/N is not a listed confusion
    "CON",          # two deletions
    "iiCONX",       # an insertion that is not a doubling
    "iiXCON",       # ditto, interior
    "iiCON0",       # THE DANGEROUS ONE — would steal a digit from the number
    "0iCON",        # a digit substitution
    "iiCO",         # deletion of a character that was not doubled
])
def test_the_near_miss_rule_refuses_everything_else(read):
    assert near_miss_rule(read, "iiCON") is None


def test_the_rule_never_edits_a_DIGIT():
    """With a separator-less format the prefix abuts the number. A rule that
    collapsed ``iiCON0`` to ``iiCON`` would move a digit out of a seven-digit
    number and produce a locator for a page that does not exist."""
    assert near_miss_rule("iiCON0", "iiCON") is None
    assert near_miss_rule("VOL22", "VOL2") is None
    assert near_miss_rule("VOL2", "VOL22") is None


# --- gate 1: the matter must carry exactly one proposable prefix -----------


def test_a_single_prefix_matter_permits_repair():
    doc = _clean_production()
    misread = document("prod/x.pdf", (_ocr(1, "photo\njiCON900099"),))
    app = apply_bates_reported((doc, misread), _confirmed())
    assert app.matter_prefixes == ("iiCON",)
    assert app.normalization_available is True
    assert app.refused_reason is None
    assert [p.bates for p in app.documents[1].pages] == ["iiCON900099"]


def test_a_MULTI_prefix_matter_refuses_outright():
    """The ruling. Not "unlikely" — structurally impossible, because the second
    series exists and DocIQ cannot tell which one the page belongs to."""
    a = _clean_production()
    b = document("prod/other.pdf",
                 tuple(_ocr(i, f"body\niCON{800000 + i:06d}") for i in range(1, 6)))
    misread = document("prod/x.pdf", (_ocr(1, "photo\njiCON900099"),))
    app = apply_bates_reported((a, b, misread), _confirmed())
    assert len(app.matter_prefixes) > 1
    assert app.normalization_available is False
    assert app.normalized == ()
    assert app.documents[2].pages[0].bates is None      # flagged, as today
    assert "REFUSED" in (app.refused_reason or "")
    assert "iCON" in (app.refused_reason or "")


def test_the_refusal_survives_the_near_miss_prefix_looking_like_a_series():
    """The case that decides the acceptance corpus, asserted as behaviour.

    Twenty one-page documents each misread as ``jiCON…`` clear the proposal
    bars on their own, so the census sees two prefixes and D-28 refuses — even
    though every one of them IS a misreading of the confirmed prefix. That is
    the ruled outcome, not a defect: DocIQ cannot tell that case apart from a
    genuine second production.
    """
    clean = _clean_production()
    noise = [document(f"prod/n{i}.pdf", (_ocr(1, f"photo\njiCON{910000 + i:06d}"),))
             for i in range(20)]
    app = apply_bates_reported((clean, *noise), _confirmed())
    assert app.matter_prefixes == ("iiCON", "jiCON")
    assert app.normalization_available is False
    assert app.normalized == ()


def test_the_census_ignores_stray_lines_the_way_propose_format_does():
    """A prefix on two pages of an eighteen-thousand-page record is not a
    series, and must not switch repair off for a matter that has only one."""
    clean = _clean_production(n=40)
    strays = document("reports/long.pdf", tuple(
        _ocr(i, f"Ordinary report body.\nCP{i:06d}" if i <= 2
             else "Ordinary report body, no stamp.")
        for i in range(1, 201)))
    assert matter_prefixes((clean, strays)) == ("iiCON",)


def test_the_census_is_as_permissive_as_the_grammar_and_that_is_recorded():
    """A finding, asserted so it cannot quietly stop being true.

    The census uses the SAME grammar as detection, and that grammar reads
    ``sheet 137`` as prefix ``sheet`` + number ``137``. Where such lines fall in
    the Bates zone often enough to clear the per-document bar, the census sees a
    second prefix and D-28 refuses. That is the conservative direction and it is
    the ruled one — but it means ordinary page text, not only a real second
    production, can switch repair off, and an operator reading
    ``refused_reason`` should not be surprised by a "prefix" that is a word.
    """
    clean = _clean_production(n=6)
    numbered = document("reports/numbered.pdf", tuple(
        _ocr(i, f"Section text.\nsheet {100 + i}") for i in range(1, 21)))
    assert matter_prefixes((clean, numbered)) == ("iiCON", "sheet")


def test_a_streaming_caller_must_supply_the_matter_census():
    """The gate asks about the MATTER. Handed one document, the census would
    answer about that document — which is how a single-document view reports
    "one prefix" for a matter that has four."""
    a = _clean_production()
    b = document("prod/other.pdf",
                 tuple(_ocr(i, f"body\niCON{800000 + i:06d}") for i in range(1, 6)))
    # a document that, seen ALONE, looks like a clean single-prefix matter
    streamed = document("prod/x.pdf", (
        _ocr(1, "body\niiCON900201"),
        _ocr(2, "body\niiCON900202"),
        _ocr(3, "photo\njiCON900203"),
    ))
    census = matter_prefixes((a, b, streamed))
    assert len(census) > 1
    # streamed, WITHOUT the census: the gate is asked about one document
    naive = apply_bates_reported((streamed,), _confirmed())
    assert naive.matter_prefixes == ("iiCON",)           # the wrong population
    assert naive.normalization_available is True
    assert naive.documents[0].pages[2].bates == "iiCON900203"
    # streamed, WITH it: the matter's answer governs and repair is refused
    correct = apply_bates_reported((streamed,), _confirmed(),
                                   matter_prefix_census=census)
    assert correct.normalization_available is False
    assert correct.documents[0].pages[2].bates is None


# --- gate 2: only pages DocIQ had to OCR -----------------------------------


def test_a_NATIVE_page_is_never_repaired():
    """A text layer is exact. A prefix that differs there is a real difference
    in the document, not a misreading of it."""
    clean = _clean_production()
    native = document("prod/native.pdf", (page(1, "body\njiCON900099"),))
    app = apply_bates_reported((clean, native), _confirmed())
    assert app.normalization_available is True
    assert app.normalized == ()
    assert app.documents[1].pages[0].bates is None


# --- it can never produce a WRONG locator ---------------------------------


@pytest.mark.parametrize("bad", [
    "jiCON90009",        # 5 digits, not 6
    "jiCON9000999",      # 7 digits
    "jiCON 900099",      # a separator the format does not have
])
def test_repair_never_touches_anything_but_the_prefix(bad):
    clean = _clean_production()
    d = document("prod/x.pdf", (_ocr(1, f"photo\n{bad}"),))
    app = apply_bates_reported((clean, d), _confirmed())
    assert app.documents[1].pages[0].bates is None, bad
    assert app.normalized == ()


def test_repair_honours_the_confirmed_SUFFIX():
    fmt_dec = _confirmed(prefix="MNFV", sep=" ", widths=(6,), suffix="CONF",
                         suffix_sep="-")
    clean = document("prod/clean.pdf", tuple(
        _ocr(i, f"body\nMNFV {900000 + i:06d}-CONF") for i in range(1, 6)))
    no = document("prod/y.pdf", (_ocr(1, "photo\nMNFY 900098"),))
    app = apply_bates_reported((clean, no), fmt_dec)
    assert app.documents[1].pages[0].bates is None       # no suffix -> refused


def test_two_different_repairs_in_one_zone_leave_the_page_unstamped():
    """The ambiguity rule is the same one and for the same reason."""
    clean = _clean_production()
    d = document("prod/x.pdf", (_ocr(1, "photo\njiCON900099 and liCON900098"),))
    app = apply_bates_reported((clean, d), _confirmed())
    assert app.documents[1].pages[0].bates is None
    assert app.normalized == ()


def test_the_same_page_read_twice_is_not_ambiguous():
    clean = _clean_production()
    d = document("prod/x.pdf", (_ocr(1, "jiCON900099\nbody\nliCON900099"),))
    app = apply_bates_reported((clean, d), _confirmed())
    assert app.documents[1].pages[0].bates == "iiCON900099"


def test_an_exact_read_beats_a_repair_and_is_not_REPORTED_as_one():
    clean = _clean_production()
    d = document("prod/x.pdf", (_ocr(1, "photo\niiCON900099\njiCON900099"),))
    app = apply_bates_reported((clean, d), _confirmed())
    assert app.documents[1].pages[0].bates == "iiCON900099"
    assert app.normalized == ()


# --- disclosure ------------------------------------------------------------


def test_every_repair_is_disclosed_with_what_the_page_actually_READS():
    """A bare "this page was repaired" is not a useful disclosure unless an
    expert can see what it was repaired FROM and by which rule."""
    clean = _clean_production()
    d = document("prod/x.pdf", (_ocr(1, "photo\njiCON900099"),))
    app = apply_bates_reported((clean, d), _confirmed())
    assert len(app.normalized) == 1
    rec = app.normalized[0]
    assert rec.read == "jiCON900099"
    assert rec.applied == "iiCON900099"
    assert rec.rule == NEAR_MISS_SUBSTITUTION
    assert rec.page_no == 1


def test_the_repair_leaves_no_trace_on_the_page_beyond_the_locator():
    """Determinism: nothing about the repair may enter hashed content beyond
    the locator itself. The page record carries a Bates number and no story."""
    clean = _clean_production()
    d = document("prod/x.pdf", (_ocr(1, "photo\njiCON900099"),))
    before = d.pages[0]
    app = apply_bates_reported((clean, d), _confirmed())
    after = app.documents[1].pages[0]
    assert after.bates == "iiCON900099"
    assert after.text == before.text
    assert after.notes == before.notes
    assert after.evolve(bates=None) == before.evolve(bates=None)


@pytest.mark.parametrize("run", range(30))
def test_repair_is_identical_run_to_run(run):
    clean = _clean_production()
    d = document("prod/x.pdf", (_ocr(1, "photo\njiCON900099"),
                                _ocr(2, "photo\nliCON900100")))
    app = apply_bates_reported((clean, d), _confirmed())
    assert [p.bates for p in app.documents[1].pages] == \
        ["iiCON900099", "iiCON900100"]
    assert [(n.read, n.applied, n.rule) for n in app.normalized] == [
        ("jiCON900099", "iiCON900099", NEAR_MISS_SUBSTITUTION),
        ("liCON900100", "iiCON900100", NEAR_MISS_SUBSTITUTION),
    ]


def test_apply_bates_is_the_documents_half_of_apply_bates_reported():
    """One pass, two views. Two implementations would eventually disagree."""
    clean = _clean_production()
    d = document("prod/x.pdf", (_ocr(1, "photo\njiCON900099"),))
    plain = apply_bates((clean, d), _confirmed())
    full = apply_bates_reported((clean, d), _confirmed())
    assert plain == full.documents


def test_reducing_a_page_to_its_zone_is_lossless_for_stage_3():
    """The acceptance harness holds every sampled document at once, so that the
    D-28 census can be asked about the MATTER. It can only afford to do that by
    keeping each page's Bates ZONE and dropping the rest, and that is only
    honest if re-slicing a zone gives the zone back.
    """
    import sys
    from pathlib import Path as _P
    sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "tools"))
    import bates_acceptance as BA
    from dociq.identify.bates import BatesZone

    long_page = "\n".join(
        ["head one", "head two", "head three"]
        + [f"body line {i}" for i in range(40)]
        + ["tail a", "tail b", "tail c", "tail d",
           "tail e", "tail f", "tail g", "iiCON900042"])
    doc = document("prod/long.pdf", (
        _ocr(1, long_page),
        _ocr(2, "short\niiCON900043"),
        _ocr(3, ""),
    ))
    reduced = BA.zone_only(doc)
    z = BatesZone()
    for a, b in zip(doc.pages, reduced.pages):
        assert [ln for _, ln in z.slice_lines(a.text)] == \
            [ln for _, ln in z.slice_lines(b.text)]
    # Everything detection USES is preserved. ``line_index`` is not, and cannot
    # be — it is a position in a text stream that has had its middle removed —
    # so it is named here rather than glossed over. Nothing downstream reads it;
    # it exists to tell an operator where in the page a candidate sat.
    def _load_bearing(cands):
        return [(c.sort_key, c.page_no, c.raw, c.format_key, c.number,
                 c.digit_width) for c in cands]

    assert _load_bearing(detect_candidates((doc,))) == \
        _load_bearing(detect_candidates((reduced,)))
    assert matter_prefixes((doc,)) == matter_prefixes((reduced,))
    assert [p.bates for p in apply_bates((doc,), _confirmed())[0].pages] == \
        [p.bates for p in apply_bates((reduced,), _confirmed())[0].pages]
