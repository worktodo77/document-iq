"""Token estimation (D-03) — including the bounds that make it honest."""

from __future__ import annotations

import pytest

from dociq.verify.tokens import (
    DEFAULT_BASIS,
    DIRECT_CONTEXT_TOKENS,
    CalibrationBasis,
    calibrate,
    estimate_for_texts,
    estimate_tokens,
    measure,
)
from tests.fixtures import MPR_PAGES

PROSE = " ".join(MPR_PAGES)


def test_pretoken_regex_covers_every_character():
    """An uncovered character would silently weaken the lower bound."""
    for text in list(MPR_PAGES) + ["a_b", "—dash—", "naïve", "\t\ttabs", "12345", "!!!"]:
        assert measure(text).unmatched_chars == 0, text


def test_empty_text_estimates_zero():
    est = estimate_tokens("")
    assert (est.low, est.high) == (0, 0)


def test_estimate_is_a_range_around_the_ruled_band():
    est = estimate_tokens("x" * 36_000)
    assert est.low == 10_000  # 36000 / 3.60
    assert est.high == 10_910  # ceil(36000 / 3.30)
    assert est.low < est.high


def test_a_refuted_ratio_band_is_replaced_by_the_structural_range():
    """Table-dense text has more pre-tokens than the ruled band allows.

    Measured on the real MPR corpus, this is the normal case — the estimate
    must not silently report the ruled band's impossible figure.
    """
    text = "\t".join(f"{n}.{n % 7}" for n in range(4000))
    est = estimate_tokens(text)
    assert est.ratio_refuted
    assert est.low == est.profile.token_floor
    assert est.high > est.low
    assert est.high <= est.profile.token_ceiling


def test_hard_bounds_are_never_violated():
    for text in list(MPR_PAGES) + [PROSE, "x" * 5000, "1 " * 5000]:
        est = estimate_tokens(text)
        assert est.profile.token_floor <= est.low <= est.high <= est.profile.token_ceiling


def test_prose_lands_near_the_widely_reported_four_chars_per_token():
    """The one case with a known external answer, used as a sanity check."""
    p = measure("The quick brown fox jumps over the lazy dog. " * 200)
    assert 4.2 <= p.chars / p.pretokens <= 4.8


def test_token_floor_never_exceeds_the_ceiling():
    for text in list(MPR_PAGES) + [PROSE, "a", "!" * 500]:
        p = measure(text)
        assert p.token_floor <= p.token_ceiling


def test_estimate_is_monotonic_in_text_length():
    small = estimate_tokens(PROSE)
    big = estimate_tokens(PROSE * 4)
    assert big.low > small.low and big.high > small.high


def test_estimate_for_texts_matches_the_concatenation():
    joined = estimate_tokens("".join(MPR_PAGES))
    streamed = estimate_for_texts(MPR_PAGES)
    assert (streamed.low, streamed.high) == (joined.low, joined.high)


def test_capacity_statement_switches_at_the_limit():
    small = estimate_tokens("x" * 1000).capacity()
    assert small.fits_directly
    assert "without retrieval" in small.statement
    huge = estimate_tokens("x" * (DIRECT_CONTEXT_TOKENS * 8)).capacity()
    assert not huge.fits_directly
    assert "RAG" in huge.statement


def test_headline_is_compact():
    assert estimate_tokens("x" * 300_000).headline.endswith("tokens")
    assert "K" in estimate_tokens("x" * 300_000).headline


def test_provenance_does_not_claim_a_tokenizer_measurement():
    text = DEFAULT_BASIS.provenance
    assert "PROXY" in text
    assert "NOT A TOKENIZER MEASUREMENT" in text
    assert "calibrated against the real Claude tokenizer" not in text


def test_calibration_reports_overlap_rather_than_replacing_the_band():
    report = calibrate(list(MPR_PAGES))
    assert report.profile.chars > 0
    assert report.chars_per_pretoken_x100 > 0
    if report.consistent:
        assert report.recommended == DEFAULT_BASIS
    else:
        assert report.recommended.low_x100 <= DEFAULT_BASIS.low_x100
        assert report.recommended.high_x100 >= DEFAULT_BASIS.high_x100


def test_calibration_on_no_text_is_a_no_op():
    report = calibrate([])
    assert report.consistent
    assert report.recommended == DEFAULT_BASIS


def test_a_widening_calibration_never_narrows():
    narrow = CalibrationBasis(low_x100=1000, high_x100=1010, provenance="test")
    report = calibrate(list(MPR_PAGES), narrow)
    assert not report.consistent
    assert report.recommended.low_x100 <= narrow.low_x100
    assert report.recommended.high_x100 >= narrow.high_x100


def test_invalid_band_is_refused():
    with pytest.raises(ValueError):
        CalibrationBasis(low_x100=400, high_x100=300, provenance="x")


def test_measurement_is_stable_across_repeated_runs():
    first = measure(PROSE)
    for _ in range(30):
        assert measure(PROSE) == first
