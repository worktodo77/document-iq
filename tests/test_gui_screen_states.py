"""Every screen × every state, proven as a grid rather than one at a time.

The standing rule this file exists for: *fix the class, not the repro.* A defect
found on one screen in one state says nothing until every screen has been driven
through every state it can reach and the whole grid asserted. So the states are
enumerated here as data, the grid is walked, and the global properties — no
sideways scroll at the product's minimum window, no widget left over from the
previous state, every figure that is a projection saying so — are asserted on
every cell rather than on the cell that happened to break.

Runs under the offscreen platform plugin.
"""

from __future__ import annotations

import os
import sys
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QLabel,
    QPushButton,
    QScrollArea,
)

from dociq.gui.main_window import (  # noqa: E402
    CHECKLIST,
    DETAIL,
    HANDOFF,
    PROGRESS,
    SETUP,
    SUMMARY,
    MainWindow,
)
from dociq.gui.mock_pipeline import MockPipeline, at_measured_scale  # noqa: E402
from dociq.gui.pipeline import (  # noqa: E402
    LEVER_AUTOMATIC,
    LEVER_EXPERT,
    ProfileInfo,
    ReductionLever,
    ReductionPlan,
    RunOutcome,
    RunRequest,
    TokenBasis,
)
from dociq.gui.view_models import (  # noqa: E402
    CAPACITY_LABEL,
    CHECKLIST_NO_RULES,
    FLAG_OCR,
    FLAG_RECONCILIATION,
    PATH_A_UNAVAILABLE,
    SCOPE_ALL,
    SCOPE_DATES,
    SCOPE_TYPES,
    PackageScope,
    build_handoff,
    build_profile_checklist,
    build_summary,
)
from dociq.runstate import RunTermination, TerminalStatus  # noqa: E402

MIN_WINDOW = (1040, 720)
"""The product's minimum window (``MainWindow.setMinimumSize``). Every state is
asserted at the SMALLEST size the operator can produce, because that is where a
layout fails and it is not the size a render is usually taken at."""


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app):
    w = MainWindow(pipeline=MockPipeline())
    w.resize(*MIN_WINDOW)
    w.show()
    yield w
    w.close()


def _request(profile_index: int = 0, with_index: bool = True) -> RunRequest:
    mp = MockPipeline()
    return RunRequest(
        r"D:\m", r"D:\m\out", profile=mp.profiles()[profile_index],
        master_index_path=r"D:\m\index.xlsx" if with_index else None)


@lru_cache(maxsize=None)
def _outcome(profile_index: int = 0, with_index: bool = True) -> RunOutcome:
    """Cached, and safe to cache: the mock is deterministic by construction and
    everything it returns is frozen. The grid drives ~30 cells across four
    parametrized tests, and an uncached 8,400-page fixture run per cell is two
    minutes of wall clock for no additional coverage."""
    return MockPipeline().run(_request(profile_index, with_index),
                              lambda _e: None, lambda: False)


@lru_cache(maxsize=None)
def _cancelled() -> RunOutcome:
    """A run that stopped early: figures describing part of a corpus, and an
    output folder still holding the previous run's deliverables (finding B-1)."""
    seen: list = []
    outcome = MockPipeline().run(
        _request(), seen.append, lambda: len(seen) >= 3)
    return replace(
        outcome,
        termination=RunTermination(TerminalStatus.CANCELLED, "Stopped."),
        published=False,
    )


def _all_text(widget) -> str:
    return "\n".join(lab.text() for lab in widget.findChildren(QLabel))


def _no_sideways_scroll(screen) -> bool:
    areas = screen.findChildren(QScrollArea)
    return all(a.horizontalScrollBar().maximum() == 0 for a in areas)


# ---------------------------------------------------------------------------
# The grid itself
# ---------------------------------------------------------------------------


def _drive_setup(window, state: str) -> int:
    if state == "empty":
        pass
    elif state == "filled":
        window.setup.set_paths(r"D:\m", r"D:\m\out", r"D:\m\index.xlsx")
        window.setup.set_preview(MockPipeline().preview_folder(r"D:\m"))
    elif state == "source-only":
        window.setup.set_paths(source=r"D:\m")
    return SETUP


def _drive_progress(window, state: str) -> int:
    window.progress.reset()
    if state != "fresh":
        pipeline = MockPipeline()
        seen: list = []

        def on_progress(event) -> None:
            seen.append(event)
            if state == "part-way" and len(seen) > 9:
                return
            window.progress.append(event)

        pipeline.run(_request(), on_progress, lambda: False)
    return PROGRESS


def _drive_summary(window, state: str) -> int:
    if state == "cancelled":
        window.show_outcome(_cancelled())
        return SUMMARY
    if state == "no-profile":
        window.show_outcome(_outcome(profile_index=2, with_index=False))
        return SUMMARY
    outcome = _outcome()
    window.show_outcome(outcome)
    if state == "toggled":
        window.summary.plan_changed.emit(outcome.plan.with_toggled("Photo logs"))
    elif state == "measured-scale":
        window.summary.plan_changed.emit(at_measured_scale(outcome.plan))
    elif state == "nothing-dropped":
        plan = outcome.plan
        for lever in outcome.plan.levers:
            if not lever.locked and lever.engaged:
                plan = plan.with_toggled(lever.key)
        window.summary.plan_changed.emit(plan)
    elif state == "everything-dropped":
        plan = outcome.plan
        for lever in outcome.plan.levers:
            if not lever.locked and not lever.engaged:
                plan = plan.with_toggled(lever.key)
        window.summary.plan_changed.emit(plan)
    elif state == "no-plan":
        window.show_outcome(replace(outcome, plan=None))
    elif state == "huge":
        window.summary.plan_changed.emit(ReductionPlan(
            full_tokens=250 * outcome.plan.capacity,
            levers=outcome.plan.levers,
            capacity=outcome.plan.capacity,
            basis=outcome.plan.basis,
        ))
    elif state == "fits":
        window.summary.plan_changed.emit(ReductionPlan(
            full_tokens=outcome.plan.capacity // 4,
            levers=(),
            capacity=outcome.plan.capacity,
            basis=outcome.plan.basis,
        ))
    return SUMMARY


def _drive_detail(window, state: str) -> int:
    window.show_outcome(_outcome())
    window.show_flag(FLAG_OCR if state == "ocr" else FLAG_RECONCILIATION)
    return DETAIL


def _drive_checklist(window, state: str) -> int:
    profiles = MockPipeline().profiles()
    if state == "complete":
        window.show_profile_checklist(profiles[0])
    elif state == "two-rules":
        window.show_profile_checklist(profiles[1])
    elif state == "keeps-everything":
        window.show_profile_checklist(profiles[2])
    elif state == "unavailable":
        window.checklist.show_checklist(build_profile_checklist(
            ProfileInfo("mystery", "0.1", "A profile whose rules did not load",
                        section_rules=4)))
        window.stack.setCurrentIndex(CHECKLIST)
    elif state == "mismatch":
        levers = MockPipeline().profile_rules(profiles[0])[0]
        window.checklist.show_checklist(build_profile_checklist(
            ProfileInfo("modec-mpr", "1.3", "MODEC monthly progress report",
                        section_rules=9),
            levers))
        window.stack.setCurrentIndex(CHECKLIST)
    return CHECKLIST


def _drive_handoff(window, state: str) -> int:
    outcome = _cancelled() if state == "unpublished" else _outcome()
    window.show_outcome(outcome)
    window.show_handoff()
    if state == "dates":
        window._rescope(PackageScope(SCOPE_DATES, "2021-01-01", "2021-12-31"))
    elif state == "types":
        window._rescope(PackageScope(SCOPE_TYPES,
                                     doc_types=("Monthly progress report",)))
    elif state == "empty-scope":
        window._rescope(PackageScope(SCOPE_DATES, "1900-01-01", "1900-12-31"))
    return HANDOFF


GRID: tuple[tuple[str, str, object], ...] = tuple(
    (screen, state, driver)
    for screen, states, driver in (
        ("setup", ("empty", "filled", "source-only"), _drive_setup),
        ("progress", ("fresh", "part-way", "complete"), _drive_progress),
        ("summary", ("plain", "toggled", "measured-scale", "nothing-dropped",
                     "everything-dropped", "no-profile", "no-plan", "cancelled",
                     "huge", "fits"), _drive_summary),
        ("detail", ("ocr", "reconciliation"), _drive_detail),
        ("checklist", ("complete", "two-rules", "keeps-everything",
                       "unavailable", "mismatch"), _drive_checklist),
        ("handoff", ("all", "dates", "types", "empty-scope", "unpublished"),
         _drive_handoff),
    )
    for state in states
)

SCREENS = ("setup", "progress", "summary", "detail", "checklist", "handoff")


def test_every_button_kind_has_a_disabled_appearance(app) -> None:
    """Takes ``app`` because ``build_theme()`` resolves fonts, and resolving a
    font without a QApplication is an ACCESS VIOLATION, not an exception — it
    took the whole suite down between ``test_extract`` and this file with no
    traceback and a bare exit code, while passing when this file was run alone.

    Class fix. Only ``#primary`` had a ``:disabled`` rule, so a refused
    secondary or link action rendered identically to a live one — full-strength
    label, crisp border. The reason for the refusal is always stated beside it,
    but a control that looks pressable invites the press before the reason is
    read.

    **This test used to check that the SELECTOR STRING existed** — and a
    rehearsal review proved it vacuous by mutation: setting every
    ``#secondary:disabled`` colour identical to the enabled rule left a disabled
    button visually indistinguishable from a live one, and the test still
    passed. Presence of a rule is not evidence of an appearance. It now compares
    the resolved declarations and requires the rendered result to actually
    differ."""
    import re

    from dociq.gui.theme import build_theme, stylesheet

    qss = stylesheet(build_theme())

    def declarations(selector: str) -> dict[str, str]:
        m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", qss)
        assert m, f"no rule for {selector}"
        out: dict[str, str] = {}
        for decl in m.group(1).split(";"):
            if ":" in decl:
                prop, _, val = decl.partition(":")
                out[prop.strip()] = val.strip()
        return out

    def appearance(rule: dict[str, str]) -> dict[str, str]:
        """The three things that decide whether a control reads as pressable,
        canonicalized so the same colour written two ways compares equal.

        ``border: 1px solid #AAC3D7`` and ``border-color: #AAC3D7`` render the
        same edge. Comparing raw property names would score those as a
        difference and call an identical-looking button "disabled" — the exact
        false pass this test exists to prevent, reintroduced one level down.
        """
        def colour_of(*props: str) -> str:
            for prop in props:
                if prop in rule:
                    found = re.findall(r"#[0-9A-Fa-f]{3,8}|\b[a-z]+\b",
                                       rule[prop])
                    hexes = [t for t in found if t.startswith("#")]
                    if hexes:
                        return hexes[-1].lower()
                    if found and found[-1] in ("transparent", "none"):
                        return found[-1]
            return ""

        return {
            "color": colour_of("color"),
            "background": colour_of("background", "background-color"),
            "border": colour_of("border-color", "border"),
        }

    for kind in ("primary", "secondary", "link"):
        enabled = appearance(declarations(f"QPushButton#{kind}"))
        disabled = appearance(declarations(f"QPushButton#{kind}:disabled"))
        assert enabled != disabled, (
            f"QPushButton#{kind}:disabled renders identically to the enabled "
            f"rule — same text colour, same fill, same edge. A refused control "
            f"that looks pressable invites the press before the reason stated "
            f"beside it is read. enabled={enabled} disabled={disabled}"
        )


def test_the_grid_covers_every_screen() -> None:
    """A grid that quietly stopped covering a screen would pass forever."""
    assert {cell[0] for cell in GRID} == set(SCREENS)
    assert len(GRID) == 28


@pytest.mark.parametrize("screen,state,driver", GRID,
                         ids=[f"{s}-{t}" for s, t, _d in GRID])
def test_every_screen_in_every_state_lays_out_at_the_minimum_window(
    window, screen: str, state: str, driver
) -> None:
    index = driver(window, state)
    window.stack.setCurrentIndex(index)
    QApplication.processEvents()
    widget = window.stack.widget(index)
    assert _no_sideways_scroll(widget), f"{screen}/{state} scrolls sideways"


@pytest.mark.parametrize("screen,state,driver", GRID,
                         ids=[f"{s}-{t}" for s, t, _d in GRID])
def test_no_state_leaves_the_previous_state_on_screen(
    window, screen: str, state: str, driver
) -> None:
    """Every screen is driven twice — once into a *different* state of the same
    screen, then into this one — and nothing from the first may survive.

    The Sprint-1 defect this generalizes: a widget taken out of a layout keeps
    its parent and goes on painting until the event loop deletes it.
    """
    others = [c for c in GRID if c[0] == screen and c[1] != state]
    if not others:
        pytest.skip("only one state")
    _s, _t, other_driver = others[0]
    other_driver(window, others[0][1])
    QApplication.processEvents()
    driver(window, state)
    QApplication.processEvents()
    widget = window.stack.widget(driver(window, state))
    orphans = [lab for lab in widget.findChildren(QLabel)
               if lab.parent() is None]
    assert not orphans


@pytest.mark.parametrize("screen,state,driver", GRID,
                         ids=[f"{s}-{t}" for s, t, _d in GRID])
def test_no_state_says_the_reference_line_is_a_target(
    window, screen: str, state: str, driver
) -> None:
    """D-21, asserted over the whole grid rather than over the one screen that
    happens to draw the waterfall."""
    index = driver(window, state)
    text = _all_text(window.stack.widget(index)).lower()
    for phrase in ("reduce to fit", "reduced to fit", "get under",
                   "drop more sections", "to fit within", "budget", "target"):
        if phrase == "target" and "not a target" in text:
            continue
        assert phrase not in text, f"{screen}/{state}: {phrase!r}"


@pytest.mark.parametrize("screen,state,driver", GRID,
                         ids=[f"{s}-{t}" for s, t, _d in GRID])
def test_no_state_promises_a_token_floor(
    window, screen: str, state: str, driver
) -> None:
    """Codex finding B-6 over the whole grid: DocIQ asserts no lower bound."""
    index = driver(window, state)
    text = _all_text(window.stack.widget(index)).lower()
    for phrase in ("at least", "no fewer than", "floor", "guaranteed"):
        assert phrase not in text, f"{screen}/{state}: {phrase!r}"


# ---------------------------------------------------------------------------
# E-1 — the §6 profiling checklist
# ---------------------------------------------------------------------------


def test_every_drop_on_the_checklist_is_attributable(window) -> None:
    """Principle 3: an omission an expert cannot attribute is, downstream,
    indistinguishable from a document that went missing."""
    profile = MockPipeline().profiles()[0]
    window.show_profile_checklist(profile)
    view = window.checklist._view
    assert view.dropped_rows
    text = _all_text(window.checklist)
    for row in view.rows:
        assert row.lever.label in text
        attribution = row.attribution()
        assert attribution in text
        if row.locked:
            assert "No expert approved this" in attribution
            # Never drawn as an expert DROP: D-14 forbids merging the tool's
            # mechanical savings with the omissions the expert signs for, and
            # an identically-styled "DROP" merges them at a glance.
            assert row.disposition_word() == "AUTOMATIC"
            assert not row.expert_drop
        else:
            assert f"{profile.profile_id} v{profile.version}" in attribution
            assert row.disposition_word() in attribution
            assert row.expert_drop == row.dropped
    marks = {r.disposition_word() for r in view.rows}
    assert marks == {"DROP", "KEEP", "AUTOMATIC"}


def test_the_checklist_shows_every_rule_the_profile_declares(window) -> None:
    for profile in MockPipeline().profiles():
        window.show_profile_checklist(profile)
        view = window.checklist._view
        assert len(view.expert_rows) == profile.section_rules, profile.profile_id
        assert view.approvable
        assert view.counts_agree


def test_a_profile_whose_rules_did_not_load_cannot_be_approved(window) -> None:
    """The dangerous empty state. An empty list rendered as a tidy blank page
    reads as "nothing is dropped"; the truth is "not known"."""
    _drive_checklist(window, "unavailable")
    view = window.checklist._view
    assert view.empty and not view.keeps_everything
    assert not view.approvable
    assert CHECKLIST_NO_RULES in _all_text(window.checklist)
    accept = [b for b in window.checklist.findChildren(QPushButton)
              if b.objectName() == "primary"]
    assert len(accept) == 1 and not accept[0].isEnabled()
    # And the summary line must not assert an absence it cannot know. "Nothing
    # is being left out" over an unreadable profile is the operator's own
    # summary telling them an invisible omission does not exist.
    summary = view.drop_summary()
    assert summary.startswith("Not known")
    assert "must not be assumed to be nothing" in summary
    assert summary in _all_text(window.checklist)


def test_a_rule_count_that_disagrees_blocks_approval(window) -> None:
    _drive_checklist(window, "mismatch")
    view = window.checklist._view
    assert not view.counts_agree and not view.approvable
    text = _all_text(window.checklist)
    assert "declares 9 section rules" in text
    accept = [b for b in window.checklist.findChildren(QPushButton)
              if b.objectName() == "primary"]
    assert not accept[0].isEnabled()


def test_a_profile_with_no_rules_is_benign_not_alarming(window) -> None:
    """"Keeps every page" and "rules unreadable" both render an empty list. One
    is a fact about the profile; the other is an absence of knowledge."""
    _drive_checklist(window, "keeps-everything")
    view = window.checklist._view
    assert view.keeps_everything and view.approvable
    assert "carries no section rules" in _all_text(window.checklist)
    accept = [b for b in window.checklist.findChildren(QPushButton)
              if b.objectName() == "primary"]
    assert accept[0].isEnabled()


def test_the_checklist_never_merges_the_two_kinds_of_omission(window) -> None:
    window.show_profile_checklist(MockPipeline().profiles()[0])
    view = window.checklist._view
    text = _all_text(window.checklist)
    assert view.drop_summary() in text
    assert view.automatic_summary() in text
    assert "on your approval" in view.drop_summary()
    assert "never added to the figure above" in view.automatic_summary()


def test_a_projected_checklist_figure_says_so_beside_the_figure(window) -> None:
    """Not in a tooltip: a projection standing in the same column, in the same
    type, as a counted figure is a claim the run cannot support."""
    window.show_profile_checklist(MockPipeline().profiles()[0])
    view = window.checklist._view
    for row in view.rows:
        assert row.lever.estimated
        assert "(projected, not counted)" in row.scale()
        assert row.scale() in _all_text(window.checklist)


def test_no_disposition_word_is_silently_clipped(window) -> None:
    """A hand-picked column width clipped "AUTOMATIC" to "AUTOMAT" — a
    truncation the screen performed and did not say it had performed. The
    column is now sized from the widest word it can ever hold, and this asserts
    the enumeration of those words is exhaustive."""
    from PySide6.QtGui import QFontMetrics

    from dociq.gui.screens import DISPOSITION_WORDS, _disposition_column_width
    from dociq.gui.theme import build_theme

    seen = set()
    for state in ("complete", "keeps-everything", "two-rules"):
        _drive_checklist(window, state)
        seen |= {r.disposition_word() for r in window.checklist._view.rows}
    assert seen and seen <= set(DISPOSITION_WORDS)

    theme = build_theme()
    metrics = QFontMetrics(theme.label(9))
    width = _disposition_column_width(theme)
    for word in DISPOSITION_WORDS:
        assert metrics.horizontalAdvance(word) <= width, word


def test_the_checklist_has_exactly_one_forward_action(window) -> None:
    window.show_profile_checklist(MockPipeline().profiles()[0])
    primaries = [b for b in window.checklist.findChildren(QPushButton)
                 if b.objectName() == "primary"]
    assert len(primaries) == 1
    assert primaries[0].text() == "Use this profile"


def test_the_checklist_is_reachable_from_the_setup_screen(window) -> None:
    window.stack.setCurrentIndex(SETUP)
    links = [b for b in window.setup.findChildren(QPushButton)
             if b.objectName() == "link"]
    review = [b for b in links if "keeps and drops" in b.text()]
    assert len(review) == 1
    review[0].click()
    assert window.stack.currentIndex() == CHECKLIST
    # Still exactly one forward action on the setup screen (D-16).
    assert len([b for b in window.setup.findChildren(QPushButton)
                if b.objectName() == "primary"]) == 1


def test_a_pipeline_with_no_profile_rules_hook_renders_the_absence(app) -> None:
    """A pipeline that does not offer ``profile_rules`` must produce the loud
    empty state, not a crash and not a confident blank.

    The docstring used to say "the seam has no ``profile_rules`` (stop-the-line
    A-11)". **A-11 was applied on 2026-08-01** and the method is on
    ``PipelineAPI``, so that sentence became false; the test is not, and is worth
    more now than it was. A Protocol is structural typing — a stand-in that omits
    the method still type-checks in practice — so the screen must still survive
    its absence, and this is what proves it does."""
    class _NoRules(MockPipeline):
        profile_rules = None

    win = MainWindow(pipeline=_NoRules())
    try:
        win.show_profile_checklist(win._pipeline.profiles()[0])
        assert win.stack.currentIndex() == CHECKLIST
        assert CHECKLIST_NO_RULES in _all_text(win.checklist)
    finally:
        win.close()


def test_a_profile_rules_hook_that_raises_does_not_take_the_window(app) -> None:
    class _Angry(MockPipeline):
        def profile_rules(self, profile):
            raise RuntimeError("profile library unreachable")

    win = MainWindow(pipeline=_Angry())
    try:
        win.show_profile_checklist(win._pipeline.profiles()[0])
        assert CHECKLIST_NO_RULES in _all_text(win.checklist)
    finally:
        win.close()


# ---------------------------------------------------------------------------
# E-2 — the D-14 waterfall on real figures
# ---------------------------------------------------------------------------


def _rows(window):
    from dociq.gui.widgets import ReductionWaterfall, WaterfallRow

    return window.summary.findChild(ReductionWaterfall).findChildren(WaterfallRow)


def test_the_capacity_row_is_a_named_sourced_reference_not_a_target(window):
    from dociq.gui.widgets import WaterfallRow

    window.show_outcome(_outcome())
    capacity = [r for r in _rows(window) if r.kind == WaterfallRow.CAPACITY]
    assert len(capacity) == 1
    name = capacity[0].accessibleName()
    assert CAPACITY_LABEL in name
    assert "reference, not a target" in name
    assert "ruled D-21" in capacity[0].toolTip()
    assert CAPACITY_LABEL in window.summary._capacity_source.text()


def test_the_capacity_literal_appears_in_exactly_one_place() -> None:
    """``DIRECT_CONTEXT_TOKENS``' docstring: "the literal appears nowhere else,
    and no screen may inline it".

    **This test used to scan everything and then assert about ``gui/`` only.**
    It collected offenders across all of ``src/dociq`` and filtered them down
    before the assertion, so its name — "in exactly one place" — described a
    check it was not making: a second inlined capacity figure anywhere outside
    the GUI would have passed. The scan is now the assertion, with the one
    legitimate exception named rather than a whole subtree silently exempt.
    """
    import re

    # Word-boundary, not substring: "200000" in "1200000" is True, so the old
    # form both over-matched unrelated numbers and read as if it were exact.
    literal = re.compile(r"(?<![\d_])200_?000(?![\d_])")

    # The ONE place the figure may be spelled out, and why. emit/handoff.py's
    # ProjectLimits is an operator-configurable upload limit that happens to
    # share the value; it is not the capacity line and does not derive from it.
    ALLOWED = {"gui/pipeline.py", "emit/handoff.py"}

    src = Path(__file__).resolve().parents[1] / "src" / "dociq"
    offenders = []
    for path in sorted(src.rglob("*.py")):
        rel = path.relative_to(src).as_posix()
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if literal.search(line) and "DIRECT_CONTEXT" not in line:
                if rel in ALLOWED:
                    continue
                offenders.append(f"{rel}:{n}: {line.strip()}")
    assert not offenders, (
        "the capacity figure is inlined outside the one constant that owns it: "
        + "; ".join(offenders)
    )


def test_an_estimated_expert_lever_is_marked_in_the_same_column(window):
    """The defect this test was written against: ``estimated`` was rendered on
    automatic rows only, so a projected EXPERT saving stood in the delta column
    in the same type as a counted one."""
    from dociq.gui.widgets import WaterfallRow

    outcome = _outcome()
    levers = tuple(
        replace(le, estimated=not le.locked)
        for le in outcome.plan.levers
    )
    window.show_outcome(outcome)
    window.summary.plan_changed.emit(replace(outcome.plan, levers=levers))
    QApplication.processEvents()
    expert = [r for r in _rows(window) if r.kind == WaterfallRow.EXPERT]
    assert expert
    for row in expert:
        assert "projected" in row.accessibleName(), row.accessibleName()


def test_a_counted_expert_lever_is_not_marked_as_projected(window):
    from dociq.gui.widgets import WaterfallRow

    outcome = _outcome()
    levers = tuple(
        replace(le, estimated=False)
        for le in outcome.plan.levers
    )
    window.show_outcome(outcome)
    window.summary.plan_changed.emit(replace(outcome.plan, levers=levers))
    QApplication.processEvents()
    for row in _rows(window):
        if row.kind == WaterfallRow.EXPERT:
            assert "projected" not in row.accessibleName()


def test_the_two_totals_are_stated_apart_and_never_added(window) -> None:
    """D-14 / ``LEVER_AUTOMATIC``: only the expert's drops are the expert's to
    defend, so there is no single combined figure anywhere on the screen."""
    window.show_outcome(_outcome())
    view = window._view
    line = view.split_line()
    assert "Left out on your approval" in line
    assert "Removed mechanically by the tool" in line
    assert line in _all_text(window.summary)
    combined = view.plan.expert_tokens + view.plan.automatic_tokens
    from dociq.gui.view_models import compact

    assert compact(combined) not in line


def test_the_dropped_sections_are_named_so_they_can_be_described(window):
    """D-21: the wording must let an expert say what was dropped and why."""
    window.show_outcome(_outcome())
    view = window._view
    line = view.drops_line()
    engaged = [le for le in view.plan.engaged if le.kind == LEVER_EXPERT]
    assert engaged
    for lever in engaged:
        assert lever.label in line
    assert "processing log" in line
    assert line in _all_text(window.summary)


def test_nothing_dropped_is_stated_rather_than_left_blank(window) -> None:
    _drive_summary(window, "nothing-dropped")
    view = window._view
    assert "nothing" in view.split_line().lower()
    assert "No section type is being left out" in view.drops_line()


def test_a_run_with_no_plan_says_nothing_rather_than_zeros(window) -> None:
    _drive_summary(window, "no-plan")
    assert window._view.split_line() == ""
    assert window._view.drops_line() == ""


def test_a_locked_row_click_is_a_question_not_a_bug(window) -> None:
    from dociq.gui.widgets import WaterfallRow

    window.show_outcome(_outcome())
    before = window._view.tokens_after
    locked = next(r for r in _rows(window) if r.kind == WaterfallRow.AUTOMATIC)
    plan = window.summary._plan
    assert plan.with_toggled(locked.key) == plan  # no raise, no change
    locked.toggled.emit(locked.key)
    QApplication.processEvents()
    assert window._view.tokens_after == before


def test_the_provenance_is_the_pipelines_words_verbatim(window) -> None:
    from dociq.gui.mock_pipeline import PROVENANCE_STRUCTURAL

    window.show_outcome(_outcome())
    assert PROVENANCE_STRUCTURAL in window.summary._basis.text()


def test_a_conditional_inconsistency_stays_conditional(window) -> None:
    outcome = _outcome()
    basis = TokenBasis(provenance="counted from the text", is_structural=True,
                       ratio_refuted=True)
    window.show_outcome(outcome)
    window.summary.plan_changed.emit(replace(outcome.plan, basis=basis))
    QApplication.processEvents()
    note = window.summary._basis.text()
    assert "not a finding that the band is wrong" in note
    assert "refuted" not in note and "impossible" not in note


# ---------------------------------------------------------------------------
# E-3 — Analyze in Claude, Paths A and B
# ---------------------------------------------------------------------------


def test_the_summary_forward_action_reaches_the_handoff(window) -> None:
    window.show_outcome(_outcome())
    primaries = [b for b in window.summary.findChildren(QPushButton)
                 if b.objectName() == "primary"]
    assert len(primaries) == 1 and primaries[0].text() == "Analyze in Claude"
    assert primaries[0].isEnabled()
    primaries[0].click()
    assert window.stack.currentIndex() == HANDOFF


def test_a_run_that_published_nothing_cannot_be_handed_over(window) -> None:
    """Finding B-1: the output folder holds the PREVIOUS run's deliverables."""
    window.show_outcome(_cancelled())
    primaries = [b for b in window.summary.findChildren(QPushButton)
                 if b.objectName() == "primary"]
    assert not primaries[0].isEnabled()

    _drive_handoff(window, "unpublished")
    text = _all_text(window.handoff)
    assert "wrote no deliverables" in text
    assert not window.handoff._open.isEnabled()
    assert not window.handoff._build.isEnabled()


def test_path_b_leads_and_states_what_to_point_claude_at(window) -> None:
    _drive_handoff(window, "all")
    text = _all_text(window.handoff)
    assert "Expert Assist reads the folder from disk" in text
    assert "clean_text/" in text and "sources.json" in text
    assert window.handoff._folder.text() == r"D:\m\out"
    # Path B's action is the primary one on this screen; Path A's is not.
    assert window.handoff._open.objectName() == "primary"
    assert window.handoff._build.objectName() == "secondary"


def test_path_b_says_when_the_pipeline_verified_nothing(app) -> None:
    class _Quiet(MockPipeline):
        matter_layout_note = None

    win = MainWindow(pipeline=_Quiet())
    try:
        win.show_outcome(_outcome())
        win.show_handoff()
        assert "cannot confirm it is Expert-Assist-shaped" in _all_text(win.handoff)
    finally:
        win.close()


def test_a_full_scope_package_says_it_is_the_whole_record(window) -> None:
    _drive_handoff(window, "all")
    statement = window.handoff._view.scope_statement()
    assert "ALL" in statement and "SUBSET" not in statement
    assert statement in _all_text(window.handoff)


def test_a_full_scope_package_still_declares_the_listed_only_files(window):
    """§5's unsupported inventory can never be in a Path A package. A package
    that called itself the complete production while those files existed would
    be making D-20's forbidden claim in the one file a reader trusts to know
    better."""
    _drive_handoff(window, "all")
    view = window.handoff._view
    assert view.unsupported == len(_outcome().result.unsupported) > 0
    statement = view.scope_statement()
    assert f"{view.unsupported:,} further file" in statement
    assert "NOT in this package" in statement
    assert "document_index.csv" in statement
    assert "complete production" not in statement
    assert statement in _all_text(window.handoff)


def test_a_scoped_package_states_the_scope_in_the_package(window) -> None:
    """D-20. A package that silently contains part of a matter is the single
    worst thing this screen could produce."""
    _drive_handoff(window, "dates")
    view = window.handoff._view
    assert view.scope.is_subset
    assert 0 < len(view.selected()) < len(view.documents)
    statement = view.scope_statement()
    assert "SUBSET" in statement
    assert f"{len(view.selected()):,} of the {len(view.documents):,}" in statement
    assert "2021-01-01 to 2021-12-31" in statement
    assert "Do not treat it as the complete record" in statement
    # And it is on screen, verbatim, before the button is pressed.
    assert statement in _all_text(window.handoff)


def test_a_type_scope_is_also_declared_a_subset(window) -> None:
    _drive_handoff(window, "types")
    view = window.handoff._view
    assert 0 < len(view.selected()) < len(view.documents)
    assert "SUBSET" in view.scope_statement()
    assert "Monthly progress report" in view.scope_statement()


def test_a_date_scope_names_the_documents_it_silently_excludes(window) -> None:
    """Undated documents fall out of a date scope on the absence of a date, not
    on their content. That is a second subsetting and it is named."""
    _drive_handoff(window, "dates")
    view = window.handoff._view
    assert view.undated() > 0
    caution = view.scope_caution()
    assert "no detected date" in caution
    assert f"{view.undated():,}" in caution
    assert caution in _all_text(window.handoff)


def test_an_empty_scope_refuses_to_build(app) -> None:
    win = MainWindow(pipeline=_with_builder([]))
    try:
        _drive_handoff(win, "empty-scope")
        view = win.handoff._view
        assert view.selected() == ()
        assert "selects no documents" in view.package_blocker()
        assert not win.handoff._build.isEnabled()
        assert view.package_blocker() in _all_text(win.handoff)
        assert "selects no documents at all" in view.scope_caution()
    finally:
        win.close()


def test_a_pipeline_with_no_package_builder_says_why(window) -> None:
    """A pipeline that omits ``build_package`` must state the refusal; a greyed
    button with no reason is read as "not for me".

    Two claims withdrawn from this docstring. It said "the seam has no package
    builder (stop-the-line A-13)": the package builder is **A-12**, not A-13
    (A-13 is the ``DIRECT_CONTEXT_TOKENS`` docstring), and **A-12 was applied on
    2026-08-01** — ``build_package`` is on ``PipelineAPI``. The amendment
    explicitly allows an adapter that does not offer Path A to OMIT the method
    rather than return an empty result, which is exactly the case under test."""
    _drive_handoff(window, "all")
    assert not window.handoff._view.package_available
    assert PATH_A_UNAVAILABLE in _all_text(window.handoff)
    assert not window.handoff._build.isEnabled()


def _with_builder(calls: list):
    class _Builder(MockPipeline):
        def build_package(self, outcome, doc_ids, scope_statement):
            calls.append({"ids": doc_ids, "statement": scope_statement})

    return _Builder()


def test_the_gui_never_writes_the_package_itself(app, tmp_path) -> None:
    """Assembling ``upload_package/`` is emit-layer work. What crosses the seam
    is the scope and the statement that must travel inside the package.

    **The emptiness check used to be vacuous** and a rehearsal review proved it
    by mutation: nothing under test ever pointed at ``tmp_path``, so making
    ``_build_package`` write a real file to disk — a direct violation of the
    property this test names — left it passing. It was asserting that an
    unrelated, untouched directory was untouched.

    The run's ``output_root`` is now ``tmp_path``, which is where a GUI that
    wrote files would write them: the package lands under the output root by
    construction. The assertion is load-bearing only because the directory it
    inspects is the one a violation would land in."""
    from dataclasses import replace

    calls: list[dict] = []

    win = MainWindow(pipeline=_with_builder(calls))
    try:
        win.show_outcome(replace(_outcome(), output_root=str(tmp_path)))
        win.show_handoff()
        assert win.handoff._build.isEnabled()
        win._rescope(PackageScope(SCOPE_DATES, "2021-01-01", "2021-12-31"))
        win.handoff._build.click()
    finally:
        win.close()
    assert len(calls) == 1
    assert calls[0]["ids"]
    assert "SUBSET" in calls[0]["statement"]
    # The output root itself, and anything anywhere beneath it.
    assert not list(tmp_path.rglob("*")), (
        f"the GUI wrote into the output root: "
        f"{[str(p) for p in tmp_path.rglob('*')]}"
    )


def test_the_package_built_is_the_one_whose_statement_was_shown(app) -> None:
    """The defect this was written against: the screen rendered the view's
    scope statement while the build button re-read the controls, so a scope
    set any way but by clicking produced a package under a statement
    describing something else."""
    calls: list[dict] = []
    win = MainWindow(pipeline=_with_builder(calls))
    try:
        win.show_outcome(_outcome())
        win.show_handoff()
        for scope in (PackageScope(SCOPE_DATES, "2021-01-01", "2021-12-31"),
                      PackageScope(SCOPE_TYPES,
                                   doc_types=("Monthly progress report",)),
                      PackageScope(SCOPE_ALL)):
            win._rescope(scope)
            QApplication.processEvents()
            shown = win.handoff._statement.text()
            calls.clear()
            win.handoff._build.click()
            assert calls and calls[0]["statement"] == shown, scope
            # …and the controls agree with what was rendered, so the next
            # click of a control starts from the scope on screen.
            assert win.handoff.scope() == scope
    finally:
        win.close()


def test_the_scope_selection_is_the_documents_the_run_produced(window) -> None:
    """Selection over contract data, never a list the GUI invented."""
    _drive_handoff(window, "all")
    view = window.handoff._view
    outcome = _outcome()
    assert [d.doc_id for d in view.documents] == [
        d.doc_id for d in outcome.result.documents]
    assert set(view.doc_types) <= {
        d.doc_type or "(no type)" for d in outcome.result.documents}


def test_scope_pickers_do_not_reset_the_choice_they_are_showing(window) -> None:
    """Regression class: repopulating a QComboBox emits currentIndexChanged, so
    a naive reload turns every operator choice into an immediate reset."""
    _drive_handoff(window, "dates")
    chosen = window.handoff.scope()
    window.handoff.show_handoff(window.handoff._view)
    QApplication.processEvents()
    assert window.handoff.scope() == chosen
