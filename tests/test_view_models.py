"""The summary projection: what the screen says, asserted without a window."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dociq.contracts import Disposition, PageKind  # noqa: E402
from dociq.gui.mock_pipeline import MockPipeline  # noqa: E402
from dociq.gui.pipeline import (  # noqa: E402
    DIRECT_CONTEXT_TOKENS,
    ReductionPlan,
    RunOutcome,
    RunRequest,
    TokenBasis,
    TokenEstimate,
)
from dociq.gui.view_models import (  # noqa: E402
    CAPACITY_LABEL,
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


def test_capacity_reading_is_conservative_at_the_boundary() -> None:
    """It "fits" only if the UPPER end of the D-03 range fits — a range whose
    top overflows must not be reported as fitting."""
    over = CapacityReading(TokenEstimate(
        chars=int(DIRECT_CONTEXT_TOKENS * 3.4), ratio_low=3.3, ratio_high=3.6))
    assert over.tokens.low < DIRECT_CONTEXT_TOKENS < over.tokens.high
    assert not over.fits

    under = CapacityReading(TokenEstimate(
        chars=int(DIRECT_CONTEXT_TOKENS * 2.0), ratio_low=3.3, ratio_high=3.6))
    assert under.fits


def test_no_screen_wording_tells_the_operator_to_get_under_the_line() -> None:
    """D-21: the reference line is never a budget or a target.

    ``CapacityReading.verdict()`` used to end "Drop more sections, or split the
    matter" — an instruction to reduce until the figure fits, which is what
    D-15 and D-21 both rule against. It and ``caption()`` are WITHDRAWN, not
    reworded, and this test is what stops the sentence coming back somewhere
    else. The claim, not just the code.
    """
    assert not hasattr(CapacityReading, "verdict")
    assert not hasattr(CapacityReading, "caption")

    # Scanned over string LITERALS that are not docstrings — the text that can
    # reach a screen. A raw substring scan of the source flags the very comment
    # recording the withdrawal, which would make the guard unmaintainable and
    # therefore short-lived.
    import ast

    banned = ("drop more sections", "reduce to fit", "reduced to fit",
              "in order to fit", "get under", "to fit within")
    src = Path(__file__).resolve().parents[1] / "src" / "dociq" / "gui"
    offenders: list[str] = []
    for path in sorted(src.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef))
            and node.body and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        }
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and id(node) not in docstrings):
                for phrase in banned:
                    if phrase in node.value.lower():
                        offenders.append(f"{path.name}:{node.lineno}: {phrase}")
    assert not offenders, offenders


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
    assert CAPACITY_LABEL in view.capacity_line()
    assert "×" in view.capacity_line()
    route = view.route_line()
    assert "Expert Assist" in route and "Cowork" in route
    for word in ("error", "failed", "cannot", "too large"):
        assert word not in route.lower()


def test_toggling_a_lever_reflows_the_whole_projection() -> None:
    outcome = _outcome()
    before = build_summary(outcome)
    after = build_summary(outcome, outcome.plan.with_toggled("Organization Charts"))
    charts = next(le for le in outcome.plan.levers
                  if le.key == "Organization Charts")
    assert after.tokens_after == before.tokens_after - charts.tokens
    assert after.tokens_before == before.tokens_before


def test_automatic_savings_are_locked_and_never_merged() -> None:
    """The profile system's whole point: "the expert approved this omission" and
    "the tool did this mechanically" are different claims.

    Selects on ``kind``, not on ``locked``: since A-20 ``locked`` spans two
    kinds and "the locked lever" is no longer a way of saying "the automatic
    lever". The mock fixture happens to emit no recognized rows, so this passed
    either way — which is precisely the reason to stop relying on it.
    """
    from dociq.gui.pipeline import LEVER_AUTOMATIC

    plan = _outcome().plan
    automatic = [le for le in plan.levers if le.kind == LEVER_AUTOMATIC]
    assert len(automatic) == 1
    assert automatic[0].locked
    assert plan.with_toggled(automatic[0].key) == plan  # a click changes nothing
    assert plan.expert_tokens and plan.automatic_tokens
    assert plan.expert_tokens != plan.remaining_tokens


def test_a_recognized_lever_is_locked_and_the_toggle_refuses_to_move_it():
    """A-20's third kind, at the MODEL layer where no screen can reach past it.

    ``LEVER_RECOGNIZED`` is the template's structural refusal to offer a
    section: the executive summary, the critical path narrative, the weather
    log, the timesheets. D-34 makes an :class:`ApprovedOmission` the only thing
    that can drop a page, and a row that cannot be engaged is how that refusal
    is expressed on screen. If ``with_toggled`` moved it, a click would engage
    an omission the template exists to forbid.

    Asserted directly rather than through the mock, which emits no recognized
    lever — so before this test the entire kind was unexercised at the seam.

    FAIL-BEFORE: watched red by widening ``ReductionLever.locked`` to
    ``kind == LEVER_AUTOMATIC``, the definition it carried before A-20.
    """
    from dociq.gui.pipeline import LEVER_RECOGNIZED, ReductionLever

    lever = ReductionLever(
        key="weather-logs", label="Weather logs", tokens=9_000, pages=140,
        kind=LEVER_RECOGNIZED, engaged=False, estimated=False,
        family_id="weather-logs", risk="high", tier="t1_outline",
        note="A weather log is trivial in tokens and decisive in a "
             "weather-delay claim.",
    )
    plan = ReductionPlan(full_tokens=900_000, levers=(lever,))

    assert lever.locked, "a kind that is not LEVER_EXPERT is not the expert's"
    assert plan.with_toggled("weather-logs") == plan, (
        "a click engaged a section the template refuses to offer")
    assert plan.with_toggled("weather-logs").levers[0].engaged is False

    # And it is counted into neither ledger — recognized pages are kept, so
    # they are not the expert's saving and not the tool's either.
    assert plan.expert_tokens == 0
    assert plan.automatic_tokens == 0
    assert plan.remaining_tokens == plan.full_tokens


def test_run_accounting_does_not_follow_a_toggle() -> None:
    """Toggling changes the estimate, not the files: the accounting figures must
    stay the run's own, or the screen would claim an output it never wrote."""
    outcome = _outcome()
    before = build_summary(outcome)
    after = build_summary(outcome, outcome.plan.with_toggled("Organization Charts"))
    assert after.pages_kept == before.pages_kept
    assert after.pages_dropped == before.pages_dropped


# ------------------------------------------------- provenance and hard bounds

def _view_with(estimate: TokenEstimate) -> object:
    """A summary whose whole basis is ``estimate`` and nothing else."""
    outcome = _outcome()
    plan = ReductionPlan(
        full_tokens=estimate.tokens or 1,
        levers=(),
        basis=TokenBasis.of(estimate),
    )
    return build_summary(
        RunOutcome(outcome.result, estimate, estimate,
                   reconciliation=outcome.reconciliation,
                   output_root=outcome.output_root, plan=plan),
    )


def test_the_screen_never_states_a_ratio_band() -> None:
    """The defect this test exists for: the summary printed "conservative end of
    a 3.3–3.6 characters-per-token estimate" as its provenance. Track B then
    measured 2.53 chars per pre-token on the real record. No figure of the
    GUI's own authorship belongs in a basis line — whatever the pipeline did is
    the only thing the screen may report."""
    note = build_summary(_outcome()).basis_note()
    assert not any(ch.isdigit() for ch in note), note


def test_the_basis_line_is_the_pipeline_s_words() -> None:
    est = TokenEstimate(1000, 3.3, 3.6, structural_tokens=400,
                        provenance="counted three ways on a Tuesday")
    assert "counted three ways on a Tuesday" in _view_with(est).basis_note()


def test_an_unrecorded_basis_says_so_rather_than_inventing_one() -> None:
    est = TokenEstimate(1000, 3.3, 3.6)
    assert _view_with(est).basis_note() == "basis not recorded"


def test_a_conditional_inconsistency_is_stated_without_being_promoted() -> None:
    """Codex review #1, finding B-6.

    The band sitting below what the text's structure allows is a conditional
    inconsistency under a stated assumption, not a proof the band is wrong. The
    screen must report the first and must not read as the second."""
    est = TokenEstimate(1000, 3.3, 3.6, structural_tokens=400,
                        provenance="counted from the text", ratio_refuted=True)
    note = _view_with(est).basis_note()
    assert "widened" in note
    assert "counted from the text" in note
    assert "impossible" not in note and "refuted" not in note


def test_a_structural_estimate_is_preferred_but_never_called_a_bound() -> None:
    """Derived from THIS text beats a ruled constant — and it is still an
    estimate. "tokens at least" asserted a floor DocIQ cannot support."""
    est = TokenEstimate(1_000_000, 3.3, 3.6, structural_tokens=400_000,
                        provenance="pre-token count")
    assert est.tokens == 400_000        # not 1_000_000 / 3.3 = 303_030
    assert est.is_structural
    view = _view_with(est)
    assert view.headline_unit() == "tokens"
    assert "at least" not in view.headline_unit()
    assert not view.capacity_line().startswith("at least ")
    assert view.capacity_line().startswith("about ")


def test_no_screen_wording_promises_a_lower_bound() -> None:
    est = TokenEstimate(1_000_000, 3.3, 3.6, provenance="chars over a ratio")
    view = _view_with(est)
    assert not view.is_structural
    assert view.headline_unit() == "tokens"
    assert "at least" not in view.capacity_line()
    assert "at least" not in view.basis_note()
    assert "floor" not in view.basis_note()


def test_the_multiplier_survives_three_digits() -> None:
    """The measured record is ~97× capacity and a larger matter is more. The
    factor is written without a decimal past 10× so three digits still fit."""
    est = TokenEstimate(0, 3.3, 3.6,
                        structural_tokens=250 * DIRECT_CONTEXT_TOKENS,
                        provenance="pre-token count")
    line = _view_with(est).capacity_line()
    assert f"250× the {CAPACITY_LABEL} reference line" in line
    assert "." not in line.split("×")[0].replace("about ", "")


def test_the_measured_scale_is_the_fixture_shape_at_the_real_magnitude() -> None:
    from dociq.gui.mock_pipeline import MEASURED_PRETOKENS, at_measured_scale

    plan = _outcome().plan
    scaled = at_measured_scale(plan)
    assert scaled.full_tokens == MEASURED_PRETOKENS
    assert len(scaled.levers) == len(plan.levers)
    assert [le.engaged for le in scaled.levers] == [le.engaged for le in plan.levers]
    assert round(scaled.over_capacity_factor) >= 50


def test_a_projected_lever_is_marked_as_projected() -> None:
    """The automatic saving is a fixture projection, not a count. Standing in
    the same column as counted figures, it has to say which it is."""
    levers = {le.key: le for le in _outcome().plan.levers}
    assert levers["automatic"].estimated
    assert not any(le.estimated for le in levers.values() if not le.locked)


def test_mock_is_deterministic() -> None:
    """A screen render is only reviewable if the fixture behind it is fixed."""
    a, b = build_summary(_outcome()), build_summary(_outcome())
    assert a == b


# ---------------------------------------------------------------------------
# Amendment A-11b: ``rule`` and ``note`` joined ``ReductionLever``.
#
# The interesting defect was not the missing fields, it was ``with_toggled``
# rebuilding the record by listing its fields positionally: correct on the day
# it was written, and silently lossy for every field added afterwards. An
# expert's stated reason for an omission would have been on screen before a
# click and gone after it.
#
# So the probe is over EVERY field, generated from the dataclass itself, not
# over the two fields this amendment happened to add. A future field is covered
# the moment it exists — which is the only version of this test that stays true.


def _lever_fields() -> tuple[str, ...]:
    import dataclasses

    from dociq.gui.pipeline import ReductionLever

    return tuple(f.name for f in dataclasses.fields(ReductionLever))


#: Fields the fixture below cannot give a non-default value, with the reason.
#: Not a convenience list — every name here is a field this probe cannot prove
#: anything about, so it is kept short and each entry has to be argued.
_UNDISTINGUISHABLE = {
    # The operation under test flips it; "different from its default" is not a
    # property it can hold on both sides of the call.
    "engaged",
    # ``with_toggled`` moves EXPERT levers only, so a fixture whose kind
    # differed from ``LEVER_EXPERT`` would be a fixture the method ignores —
    # the toggle would no-op and the whole assertion would pass vacuously.
    "kind",
}


def test_the_toggle_fixture_can_actually_detect_a_dropped_field():
    """The guard on the guard.

    ``test_toggling_preserves_every_lever_field_except_engaged`` is only
    evidence if every field it compares holds a value the DEFAULT would not
    also produce. Measured against d3cee24 it did not: A-20 added ``family_id``,
    ``risk``, ``tier`` and ``approved_by``, the fixture left all four unset, and
    a ``with_toggled`` mutated to drop all four stayed GREEN. ``approved_by``
    silently reverting to ``""`` on a click is D-34's own failure — the named
    person who approved an omission, gone at the next toggle, with the omission
    still engaged.

    So the fixture's adequacy is asserted here rather than claimed in a comment.
    A field added to ``ReductionLever`` next year and not given a value in the
    fixture fails THIS test, loudly, instead of quietly emptying the other one.
    """
    import dataclasses

    from dociq.gui.pipeline import ReductionLever

    lever = _toggle_fixture()
    for field in dataclasses.fields(ReductionLever):
        if field.name in _UNDISTINGUISHABLE:
            continue
        if field.default is dataclasses.MISSING:
            continue  # required: every call site must supply it, loudly
        assert getattr(lever, field.name) != field.default, (
            f"the fixture leaves ReductionLever.{field.name!r} at its default "
            f"{field.default!r}, so with_toggled could drop that field and the "
            f"preservation test would still pass"
        )


def _toggle_fixture():
    """One lever with every optional field set away from its default."""
    from dociq.gui.pipeline import LEVER_EXPERT, ReductionLever

    return ReductionLever(
        key="photo_logs", label="Photo logs", tokens=41_000, pages=612,
        kind=LEVER_EXPERT, engaged=True, estimated=True,
        rule="section:^PHOTO LOG", note="Dropped per J. Long, 2026-08-01.",
        # A-20's four. Present so the probe below is not vacuous over them.
        family_id="progress-photographs", risk="high", tier="t1_outline",
        approved_by="abachowski",
    )


def test_toggling_preserves_every_lever_field_except_engaged():
    lever = _toggle_fixture()
    plan = ReductionPlan(full_tokens=900_000, levers=(lever,))

    toggled = plan.with_toggled("photo_logs").levers[0]

    assert toggled.engaged is False, "the toggle must actually toggle"
    for name in _lever_fields():
        if name == "engaged":
            continue
        assert getattr(toggled, name) == getattr(lever, name), (
            f"with_toggled dropped ReductionLever.{name!r} — it rebuilds the "
            f"record and lost a field it did not name"
        )
    # And back again: two toggles are the identity, over every field.
    assert plan.with_toggled("photo_logs").with_toggled("photo_logs") == plan


SEAM_MODULE = "dociq.gui.pipeline"
FROZEN_SEAM_SOURCE = Path("src") / "dociq" / "gui" / "pipeline.py"


def _seam_records() -> dict[str, int]:
    """Every frozen presentation record the seam DEFINES, and how many fields
    each one requires.

    Generated from the module, so a record added to the seam tomorrow is
    policed the moment it exists. Records the seam merely re-exports
    (``RunConfig`` and ``RunResult`` from :mod:`dociq.contracts`,
    ``RunTermination`` from :mod:`dociq.runstate`) are excluded: they belong to
    the contract layer, which has its own rules and its own freeze.
    """
    import dataclasses
    import importlib

    mod = importlib.import_module(SEAM_MODULE)
    out: dict[str, int] = {}
    for name in dir(mod):
        obj = getattr(mod, name)
        if not (isinstance(obj, type) and dataclasses.is_dataclass(obj)):
            continue
        if getattr(obj, "__module__", "") != SEAM_MODULE:
            continue
        out[name] = sum(
            1 for f in dataclasses.fields(obj)
            if f.default is dataclasses.MISSING
            and f.default_factory is dataclasses.MISSING
        )
    return out


def _positional_rebuild_sites() -> list[str]:
    """Sites passing a seam record an OPTIONAL field positionally.

    Parsed, not pattern-matched. The first version of this probe used a regex
    and MISSED the very rebuild that motivated it — the offending call was
    ``ReductionLever(lever.key, ...)`` and the pattern stopped at the dot. A
    regex over source is a guess about syntax; the AST is the syntax.

    **Why "beyond the required fields" is the right line, rather than "any
    positional argument".** A record's required fields must be supplied at every
    call site, so a new REQUIRED field breaks every one of them loudly and can
    never vanish silently. Every field added to a frozen record after it ships
    carries a default — and a default is exactly what a rebuild that stopped
    listing fields falls back to. So the arguments that can silently take a
    stale or default value are the optional ones, and passing those by position
    is the defect. This also lets ``TokenEstimate(chars, low, high)`` stand as
    the plain three-argument construction it is, instead of forcing keywords on
    call sites that carry no risk.
    """
    import ast

    required = _seam_records()
    root = Path(__file__).resolve().parents[1]
    offenders: list[str] = []
    for path in sorted((root / "src").rglob("*.py")) + sorted(
        (root / "tests").rglob("*.py")
    ):
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            # Both `ReductionLever(...)` and `pipeline.ReductionLever(...)`.
            # The first version matched only ast.Name and would have missed
            # every qualified call — a probe blind to half the call sites it
            # claims to police, which is the same defect one level up from the
            # one it was written to catch.
            func = node.func
            name = (
                func.id if isinstance(func, ast.Name)
                else func.attr if isinstance(func, ast.Attribute)
                else None
            )
            if name in required and len(node.args) > required[name]:
                rel = path.relative_to(root).as_posix()
                offenders.append(
                    f"{rel}:{node.lineno} {name} "
                    f"({len(node.args)} positional, {required[name]} required)"
                )
    return offenders


def test_the_probe_polices_every_seam_record_not_just_the_reported_one():
    """The class-fix claim, asserted rather than stated.

    A-11b said "fix the class" and the probe it shipped named exactly one
    record. This asserts the enumeration is derived, so it cannot be one record
    behind the seam again.
    """
    records = _seam_records()
    assert "ReductionLever" in records and "ReductionPlan" in records
    assert "RunOutcome" in records and "ProgressEvent" in records
    assert len(records) >= 12, records
    # And nothing the seam only re-exports.
    assert "RunResult" not in records and "RunTermination" not in records


def test_no_seam_record_is_rebuilt_with_optional_fields_positionally():
    """The CLASS, not the repro.

    Every frozen presentation record in the seam, in the product AND in the
    tests — a lossy rebuild inside a fixture produces a passing test of the
    wrong record, which is worse than one in the product because it also hides
    the product's.

    The seam module itself is excluded here and asserted separately below; see
    that test for why.
    """
    offenders = [o for o in _positional_rebuild_sites()
                 if not o.startswith(FROZEN_SEAM_SOURCE.as_posix())]
    assert not offenders, (
        "a frozen seam record is built with an optional field passed by "
        "position at " + "; ".join(offenders)
        + " — use keywords (or dataclasses.replace() when rebuilding), so a "
          "field added later cannot be silently dropped"
    )


def test_the_frozen_seam_module_has_no_positional_rebuild():
    """CLOSED 2026-08-03. This carried a ``strict=True`` xfail while the seam
    was frozen mid-sprint: ``ReductionPlan.with_toggled`` rebuilt
    ``ReductionPlan`` positionally, inside the very method that had been fixed
    to stop rebuilding ``ReductionLever`` positionally — lossless at 4 of 4
    fields and silently lossy on the fifth.

    The seam owner applied ``replace(self, levers=levers)``, the strict marker
    turned red exactly as it was designed to, and it is removed here. The
    mechanism is worth keeping in mind: a strict xfail is how a reported-but-
    unfixable finding stays visible without blocking, and how it announces its
    own closure instead of being forgotten."""
    offenders = [o for o in _positional_rebuild_sites()
                 if o.startswith(FROZEN_SEAM_SOURCE.as_posix())]
    assert not offenders, "; ".join(offenders)


# ---------------------------------------------------------------------------
# B1: an aggregate sentence may never report a PROJECTED figure as a counted
# one — least of all a projected ZERO.
#
# ``ChecklistRow.scale()`` already appends "(projected, not counted)"; the
# aggregate summaries did not. ``RealPipeline.profile_rules`` returns
# ``tokens=0, pages=0, estimated=True`` for every row, so the sentence that
# GATES APPROVAL read "3 section types left out on your approval: 0 pages,
# about 0 tokens." An expert reads "these drops cost nothing", approves, and
# the run drops real pages.
#
# Enumeration of every aggregate over levers in this module — all four are
# asserted below, not just the one that was reported:
#   ProfileChecklistView.drop_summary        (the approval sentence)
#   ProfileChecklistView.automatic_summary
#   SummaryView.split_line                   (both halves)
#   SummaryView.drops_line                   (already carried a marker)


def _projected_checklist(n: int = 3):
    """The shape ``RealPipeline.profile_rules`` actually returns."""
    from dociq.gui.pipeline import LEVER_EXPERT, ProfileInfo, ReductionLever
    from dociq.gui.view_models import build_profile_checklist

    profile = ProfileInfo(profile_id="mpr", version="1", label="MPR",
                          section_rules=n)
    levers = tuple(
        ReductionLever(key=f"s{i}", label=f"Section {i}", tokens=0, pages=0,
                       kind=LEVER_EXPERT, engaged=True, estimated=True)
        for i in range(n)
    )
    return build_profile_checklist(profile, levers)


def test_the_approval_sentence_never_reports_a_projected_zero_as_a_zero() -> None:
    """FAIL-BEFORE: "3 section types left out on your approval: 0 pages, about
    0 tokens." — a flat statement that the drops cost nothing, on the one line
    approval is given against."""
    summary = _projected_checklist().drop_summary()
    assert "0 pages" in summary        # the figure is still shown
    assert "projected, not counted" in summary, summary
    assert "absence of a measurement" in summary, summary


def test_the_projection_marker_matches_the_rows_own_wording() -> None:
    """One vocabulary. A row saying "(projected, not counted)" and a summary
    saying something else would read as two different qualifications."""
    view = _projected_checklist()
    assert "projected, not counted" in view.rows[0].scale()
    assert "projected, not counted" in view.drop_summary()


def test_a_mixed_checklist_says_how_many_of_the_figures_are_projected() -> None:
    from dociq.gui.pipeline import LEVER_EXPERT, ProfileInfo, ReductionLever
    from dociq.gui.view_models import build_profile_checklist

    levers = (
        ReductionLever(key="a", label="A", tokens=1000, pages=10,
                       kind=LEVER_EXPERT, engaged=True, estimated=False),
        ReductionLever(key="b", label="B", tokens=0, pages=0,
                       kind=LEVER_EXPERT, engaged=True, estimated=True),
    )
    view = build_profile_checklist(
        ProfileInfo(profile_id="p", version="1", label="P", section_rules=2),
        levers)
    summary = view.drop_summary()
    assert "1 of these 2 are projected, not counted" in summary, summary
    # The zero caveat belongs only where the whole figure is a projected zero.
    assert "absence of a measurement" in summary


def test_a_counted_checklist_carries_no_projection_marker() -> None:
    """The marker must MEAN something: on a counted set it must be absent, or
    it degrades into decoration nobody reads."""
    from dociq.gui.pipeline import LEVER_EXPERT, ProfileInfo, ReductionLever
    from dociq.gui.view_models import build_profile_checklist

    levers = (ReductionLever(key="a", label="A", tokens=41_000, pages=612,
                             kind=LEVER_EXPERT, engaged=True, estimated=False),)
    view = build_profile_checklist(
        ProfileInfo(profile_id="p", version="1", label="P", section_rules=1),
        levers)
    assert "projected" not in view.drop_summary()


def test_the_locked_disposition_map_covers_every_locked_lever_kind() -> None:
    """The tripwire for the next kind, asserted rather than trusted.

    ``ChecklistRow.disposition_word`` subscripts
    ``view_models._LOCKED_DISPOSITION`` so an unhandled kind raises instead of
    printing a word chosen for a different one — but a ``KeyError`` raised at
    render time is raised in front of an expert, on the §6 approval screen. This
    is the same condition, checked where it is cheap.

    The kinds are DERIVED from the seam's ``LEVER_*`` constants, not listed
    here: a list would be one kind behind the module the day someone adds the
    fifth, which is exactly how ``LEVER_RECOGNIZED`` ended up rendering as
    "AUTOMATIC" for a whole sprint.
    """
    from dociq.gui import pipeline as seam
    from dociq.gui.view_models import _LOCKED_DISPOSITION

    kinds = {
        getattr(seam, name) for name in dir(seam)
        if name.startswith("LEVER_") and isinstance(getattr(seam, name), str)
    }
    assert len(kinds) >= 3, kinds  # expert, automatic, recognized
    locked_kinds = kinds - {seam.LEVER_EXPERT}
    assert set(_LOCKED_DISPOSITION) == locked_kinds, (
        f"lever kind(s) with no word on the §6 checklist: "
        f"{sorted(locked_kinds - set(_LOCKED_DISPOSITION))}; "
        f"word(s) for a kind that no longer exists: "
        f"{sorted(set(_LOCKED_DISPOSITION) - locked_kinds)}"
    )
    # And no two kinds render the same word — two kinds sharing one word is the
    # merge D-14 forbids, wearing the map's clothes.
    assert len(set(_LOCKED_DISPOSITION.values())) == len(_LOCKED_DISPOSITION)


def test_every_aggregate_over_levers_marks_a_projection() -> None:
    """The CLASS, not the reported instance.

    Four sentences add levers up. Each one is built here with an all-projected
    set and required to say so — so a fifth aggregate written later fails this
    test by omission rather than shipping an unmarked projection.
    """
    from dociq.gui.pipeline import (
        LEVER_AUTOMATIC,
        LEVER_EXPERT,
        ProfileInfo,
        ReductionLever,
        ReductionPlan,
    )
    from dociq.gui.view_models import build_profile_checklist

    expert = ReductionLever(key="e", label="E", tokens=0, pages=0,
                            kind=LEVER_EXPERT, engaged=True, estimated=True)
    auto = ReductionLever(key="a", label="A", tokens=0, pages=0,
                          kind=LEVER_AUTOMATIC, engaged=True, estimated=True)

    checklist = build_profile_checklist(
        ProfileInfo(profile_id="p", version="1", label="P", section_rules=1),
        (expert, auto))
    outcome = _outcome()
    view = build_summary(
        outcome,
        ReductionPlan(full_tokens=1_000_000, levers=(expert, auto)),
    )

    sentences = {
        "ProfileChecklistView.drop_summary": checklist.drop_summary(),
        "ProfileChecklistView.automatic_summary": checklist.automatic_summary(),
        "SummaryView.split_line": view.split_line(),
        "SummaryView.drops_line": view.drops_line(),
    }
    for name, text in sentences.items():
        assert "project" in text, (
            f"{name} adds projected levers up and does not say the figure is a "
            f"projection: {text!r}"
        )


# ---------------------------------------------------------------------------
# C4 — no GUI string may assert a removal the pipeline withdraws
# ---------------------------------------------------------------------------


def test_no_gui_string_claims_DocIQ_removes_duplicates_or_page_furniture():
    """CLASS assertion, not the one repro.

    ``adapter._plan`` withdraws the behavior in terms: DocIQ *detects*
    exact-hash duplicates and warns about them, and **removes neither them nor
    page furniture**. Three GUI strings asserted the opposite anyway — the
    locked ``ChecklistRow.attribution``, the waterfall row's tooltip in
    ``widgets.py``, and the mock's ``AUTOMATIC_SAVING_SHARE`` docstring. Two of
    them were unreachable under the real adapter, which is what let them survive
    review: reachability is a property of this month's adapter, not of the
    string, and these are strings an expert reads.

    So the assertion is over the SOURCE of every GUI module, not over a rendered
    widget — a rendering test only covers the branch it happens to reach.
    """
    import re
    from pathlib import Path

    gui = Path(__file__).resolve().parents[1] / "src" / "dociq" / "gui"
    # "removes/removed ... duplicates" or "... page furniture" in one sentence.
    claim = re.compile(
        r"remove[sd]?\b[^.]{0,120}?(duplicate|page furniture)"
        r"|(duplicate|page furniture)[^.]{0,60}?\bremov",
        re.IGNORECASE | re.DOTALL,
    )
    offenders = []
    for path in sorted(gui.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for match in claim.finditer(text):
            snippet = match.group(0).replace("\n", " ")
            # The withdrawal itself, and comments explaining it, are the whole
            # point — they must be allowed to name what is NOT done.
            window = text[max(0, match.start() - 260):match.end() + 120]
            if re.search(r"removes neither|not\s+remove|never\s+remove|"
                         r"NEVER A MECHANISM|NAMES THE CATEGORY|withdraw",
                         window, re.IGNORECASE):
                continue
            line = text[:match.start()].count("\n") + 1
            offenders.append(f"{path.name}:{line}: {snippet[:100]}")
    assert not offenders, (
        "GUI text asserts DocIQ removes duplicates or page furniture, which "
        "adapter._plan withdraws:\n" + "\n".join(offenders))
