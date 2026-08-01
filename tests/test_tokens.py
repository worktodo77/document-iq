"""Token estimation (D-03) — including the bounds that make it honest."""

from __future__ import annotations

from pathlib import Path

import pytest

from dociq.verify.tokens import (
    ASSUMPTIONS,
    DEFAULT_BASIS,
    DIRECT_CONTEXT_TOKENS,
    SOUND_BOUND,
    TOKENS_PER_PRETOKEN_HIGH_X100,
    TOKENS_PER_PRETOKEN_LOW_X100,
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


def test_an_extreme_structural_range_widens_the_band_upward():
    """Text far denser than the ruled band allows widens the reported range.

    Widening only, and only upward: the band understating the load is the error
    that matters for a capacity decision.
    """
    text = "\t".join(f"{n}.{n % 7}" for n in range(4000))
    est = estimate_tokens(text)
    assert est.ratio_refuted and est.widened
    assert est.low <= est.profile.chars * 100 // DEFAULT_BASIS.high_x100
    assert est.high > est.low
    assert est.high <= est.profile.token_ceiling


def test_the_pretoken_count_is_never_asserted_as_a_floor():
    """Codex review #1, finding B-6 — the sharp one.

    ``tokens >= pretokens`` holds only for a tokenizer's OWN pre-tokenization.
    DocIQ's regex invents boundaries (digit runs split every three digits) that
    a coarser real tokenizer merges straight across, so the pre-token count is
    not a bound and nothing may clamp the reported low to it.
    """
    assert not hasattr(measure("x"), "token_floor")
    text = "\t".join(f"{n}.{n % 7}" for n in range(4000))
    est = estimate_tokens(text)
    assert est.low < est.profile.pretokens, (
        "the reported low must be allowed below the pre-token count — a "
        "tokenizer with coarser pre-tokenization goes there"
    )


def test_corpus_density_does_not_refute_the_ruled_band():
    """The measured MPR density is CONSISTENT with D-03 once A1 is applied.

    2.53 chars per pre-token times the assumed coarse-pre-tokenization
    allowance lands at about 3.5 chars/token, inside D-03's 3.30–3.60 band. The
    earlier code declared the band refuted at this density.
    """
    text = "abc, " * 4000
    profile = measure(text)
    density = profile.chars / profile.pretokens
    assert 2.3 <= density <= 2.8, density
    est = estimate_tokens(text)
    assert not est.ratio_refuted, (
        "a density D-03's band covers once the pre-tokenization allowance is "
        "applied must not be reported as a refutation"
    )
    assert calibrate([text]).consistent


def test_the_sound_ceiling_is_the_only_bound_and_it_holds():
    for text in list(MPR_PAGES) + [PROSE, "x" * 5000, "1 " * 5000]:
        est = estimate_tokens(text)
        assert 1 <= est.low <= est.high <= est.profile.token_ceiling
        assert est.profile.token_ceiling == len(text.encode("utf-8"))


def test_provenance_carries_the_method_and_every_assumption():
    """A figure whose assumptions stay in the source file cannot be checked."""
    est = estimate_tokens(PROSE)
    text = est.provenance_text("before reduction")
    assert est.method in text
    assert SOUND_BOUND in text
    for assumption in ASSUMPTIONS:
        assert assumption in text
    assert "before reduction" in text
    assert "hard lower bound" not in text.lower()


def test_provenance_names_the_method_this_run_actually_used():
    plain = estimate_tokens(PROSE)
    dense = estimate_tokens("\t".join(f"{n}.{n % 7}" for n in range(4000)))
    assert plain.method != dense.method
    assert plain.method in plain.provenance_text()
    assert dense.method in dense.provenance_text()
    assert plain.method_short != dense.method_short


def test_the_mock_pipeline_shares_the_estimator_constant():
    """The GUI may not import ``verify`` (pagemodel freeze), so the constant is
    restated there. This is the test that keeps the copy honest."""
    from dociq.gui import mock_pipeline

    assert mock_pipeline._TOKENS_PER_PRETOKEN_LOW == (
        TOKENS_PER_PRETOKEN_LOW_X100 / 100
    )


def test_the_low_assumption_allows_fewer_tokens_than_pretokens():
    """The whole correction in one number: below 1.00, deliberately."""
    assert TOKENS_PER_PRETOKEN_LOW_X100 < 100 < TOKENS_PER_PRETOKEN_HIGH_X100


def test_prose_lands_near_the_widely_reported_four_chars_per_token():
    """The one case with a known external answer, used as a sanity check."""
    p = measure("The quick brown fox jumps over the lazy dog. " * 200)
    assert 4.2 <= p.chars / p.pretokens <= 4.8


def test_the_assumed_structural_range_never_exceeds_the_sound_ceiling():
    for text in list(MPR_PAGES) + [PROSE, "a", "!" * 500]:
        p = measure(text)
        assert p.assumed_token_low <= p.assumed_token_high
        assert p.assumed_token_low <= p.token_ceiling


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


def test_no_shipped_module_asserts_a_tokenizer_independent_floor():
    """The withdrawn claim, hunted by phrase across the whole package.

    Finding B-6 was a claim repeated in five places — the estimator, the
    contract projection, the GUI seam, the summary PDF and the register.
    Deleting it from the module that computes it is not enough; this test is
    what makes a reappearance anywhere in ``src/dociq`` go red.

    Lines that *withdraw* the claim necessarily quote it, so an occurrence is
    allowed only inside a withdrawal note: a marker ("B-6", "withdrawn", "used
    to", "no longer", "not a bound", "earlier") must appear within six lines of
    it. An assertion made in passing has no such neighbour and goes red.
    """
    src = Path(__file__).resolve().parents[1] / "src" / "dociq"
    banned = ("hard lower bound", "cannot emit fewer tokens than",
              "no tokenizer can go below", "tokens at least",
              "a floor, not an estimate")
    markers = ("b-6", "withdrawn", "used to", "no longer", "not a bound",
               "earlier", "must stay empty", "not one")
    offenders = []
    for path in sorted(src.rglob("*.py")):
        if path.name == "contracts.py":  # frozen — see amendments.md A-05
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for n, line in enumerate(lines, 1):
            low = line.lower()
            if not any(p in low for p in banned):
                continue
            window = " ".join(lines[max(0, n - 7):n + 6]).lower()
            if any(m in window for m in markers):
                continue
            offenders.append(f"{path.name}:{n}: {line.strip()}")
    assert not offenders, (
        "a withdrawn token-floor claim is back:\n" + "\n".join(offenders)
    )


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
