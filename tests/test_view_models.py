"""The summary projection: what the screen says, asserted without a window."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dociq.contracts import Disposition, PageKind  # noqa: E402
from dociq.gui.mock_pipeline import MockPipeline  # noqa: E402
from dociq.gui.pipeline import (  # noqa: E402
    DIRECT_CONTEXT_TOKENS,
    RunRequest,
    TokenEstimate,
)
from dociq.gui.view_models import (  # noqa: E402
    FLAG_OCR,
    FLAG_RECONCILIATION,
    FLAG_UNSUPPORTED,
    CapacityReading,
    build_summary,
    format_tokens,
)


def _outcome(with_index: bool = True, profile_index: int = 0):
    mp = MockPipeline()
    request = RunRequest(
        source_root=r"D:\Matters\x",
        output_root=r"D:\Matters\x\out",
        profile=mp.profiles()[profile_index],
        master_index_path=r"D:\Matters\x\index.xlsx" if with_index else None,
    )
    return mp.run(request, lambda _e: None, lambda: False)


def test_page_accounting_is_read_from_the_contract_not_recomputed() -> None:
    """The freeze forbids Track C computing page accounting. The check that
    means anything is that the view's numbers ARE the contract's numbers."""
    outcome = _outcome()
    view = build_summary(outcome)
    result = outcome.result
    assert (view.pages_in, view.pages_kept, view.pages_dropped) == (
        result.pages_in, result.pages_kept, result.pages_dropped)
    assert view.pages_in == view.pages_kept + view.pages_dropped


def test_dropped_pages_all_carry_a_drop_rule() -> None:
    """Principle 1 through the GUI's own fixture: if the mock could produce an
    unattributable drop, every screen built on it would be lying."""
    outcome = _outcome()
    for doc in outcome.result.documents:
        for page in doc.pages:
            if page.disposition is Disposition.DROP:
                assert page.drop_rule


def test_flags_appear_only_when_they_have_content() -> None:
    with_index = build_summary(_outcome(with_index=True))
    assert {f.key for f in with_index.flags} == {
        FLAG_OCR, FLAG_UNSUPPORTED, FLAG_RECONCILIATION}

    without = build_summary(_outcome(with_index=False))
    assert FLAG_RECONCILIATION not in {f.key for f in without.flags}


def test_ocr_flag_counts_only_pages_under_the_run_threshold() -> None:
    outcome = _outcome()
    view = build_summary(outcome)
    threshold = outcome.result.config.ocr_conf_threshold
    expected = sum(
        1
        for doc in outcome.result.documents
        for page in doc.pages
        if page.kind is PageKind.OCR and page.ocr_conf is not None
        and page.ocr_conf < threshold
    )
    assert view.flag(FLAG_OCR).count == expected
    assert expected > 0, "the fixture must exercise the flag it claims to test"


def test_no_profile_keeps_every_page() -> None:
    """Principle 1's default, visible on the screen: profile "none" drops
    nothing, so the summary must show zero dropped."""
    view = build_summary(_outcome(profile_index=2))
    assert view.pages_dropped == 0
    assert view.pages_kept == view.pages_in


def test_capacity_verdict_is_conservative_at_the_boundary() -> None:
    """It "fits" only if the UPPER end of the D-03 range fits — a range whose
    top overflows must not be reported as fitting."""
    over = CapacityReading(TokenEstimate(
        chars=int(DIRECT_CONTEXT_TOKENS * 3.4), ratio_low=3.3, ratio_high=3.6))
    assert over.tokens.low < DIRECT_CONTEXT_TOKENS < over.tokens.high
    assert not over.fits
    assert "retrieval mode" in over.verdict()

    under = CapacityReading(TokenEstimate(
        chars=int(DIRECT_CONTEXT_TOKENS * 2.0), ratio_low=3.3, ratio_high=3.6))
    assert under.fits
    assert "Fits directly" in under.verdict()


def test_capacity_caption_is_the_one_d07_specifies() -> None:
    reading = CapacityReading(TokenEstimate(340_000, 3.3, 3.6))
    caption = reading.caption()
    assert caption.endswith("of direct-context capacity")
    assert "%" in caption


def test_token_headline_is_always_a_range() -> None:
    assert format_tokens(TokenEstimate(500_000, 3.3, 3.6)) == ("139–152",
                                                              "thousand tokens")
    value, unit = format_tokens(TokenEstimate(12_000_000, 3.3, 3.6))
    assert "–" in value and unit == "million tokens"


def test_id_regime_note_states_which_scheme_ran() -> None:
    assert "index.xlsx" in build_summary(_outcome(True)).id_regime_note
    assert "DIQ-" in build_summary(_outcome(False)).id_regime_note


# --------------------------------------------------------- reduction waterfall

def test_headline_is_before_and_after() -> None:
    view = build_summary(_outcome())
    assert "→" in view.headline()
    before, after = view.headline().split("→")
    assert before.strip() and after.strip()
    assert view.tokens_after < view.tokens_before


def test_the_expected_case_is_over_capacity_and_is_not_a_failure() -> None:
    """On the real corpus the record does not fit even fully reduced. The
    screen must state the shortfall and hand over a route, not an error."""
    view = build_summary(_outcome())
    assert not view.fits()
    assert "above direct-context capacity" in view.capacity_line()
    assert "×" in view.capacity_line()
    route = view.route_line()
    assert "Expert Assist" in route and "Cowork" in route
    for word in ("error", "failed", "cannot", "too large"):
        assert word not in route.lower()


def test_toggling_a_lever_reflows_the_whole_projection() -> None:
    outcome = _outcome()
    before = build_summary(outcome)
    after = build_summary(outcome, outcome.plan.with_toggled("Organisation Charts"))
    charts = next(le for le in outcome.plan.levers
                  if le.key == "Organisation Charts")
    assert after.tokens_after == before.tokens_after - charts.tokens
    assert after.tokens_before == before.tokens_before


def test_automatic_savings_are_locked_and_never_merged() -> None:
    """The profile system's whole point: "the expert approved this omission" and
    "the tool did this mechanically" are different claims."""
    plan = _outcome().plan
    automatic = [le for le in plan.levers if le.locked]
    assert len(automatic) == 1
    assert automatic[0].kind == "automatic"
    assert plan.with_toggled(automatic[0].key) == plan  # a click changes nothing
    assert plan.expert_tokens and plan.automatic_tokens
    assert plan.expert_tokens != plan.remaining_tokens


def test_run_accounting_does_not_follow_a_toggle() -> None:
    """Toggling changes the estimate, not the files: the accounting figures must
    stay the run's own, or the screen would claim an output it never wrote."""
    outcome = _outcome()
    before = build_summary(outcome)
    after = build_summary(outcome, outcome.plan.with_toggled("Organisation Charts"))
    assert after.pages_kept == before.pages_kept
    assert after.pages_dropped == before.pages_dropped


def test_mock_is_deterministic() -> None:
    """A screen render is only reviewable if the fixture behind it is fixed."""
    a, b = build_summary(_outcome()), build_summary(_outcome())
    assert a == b
