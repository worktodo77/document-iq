"""Widget-level state transitions.

Runs under the offscreen platform plugin. These are the assertions a screen
render cannot make: that a control is disabled until it should not be, that
navigation lands where it says, and that a second run does not leave the first
one's widgets on screen.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

from dociq.gui.main_window import DETAIL, PROGRESS, SETUP, SUMMARY, MainWindow  # noqa: E402
from dociq.gui.mock_pipeline import MockPipeline  # noqa: E402
from dociq.gui.pipeline import RunRequest  # noqa: E402
from dociq.gui.theme import build_theme  # noqa: E402
from dociq.gui.view_models import FLAG_OCR, FLAG_RECONCILIATION  # noqa: E402
from dociq.gui.widgets import Chip  # noqa: E402


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app):
    w = MainWindow(pipeline=MockPipeline())
    w.resize(1180, 800)
    yield w
    w.close()


def _request(with_index: bool = True) -> RunRequest:
    mp = MockPipeline()
    return RunRequest(r"D:\m", r"D:\m\out", mp.profiles()[0],
                      r"D:\m\index.xlsx" if with_index else None)


def test_every_font_role_returns_a_font(app) -> None:
    """Regression: the theme's family fields were once named for the same roles
    as its font factories, so ``theme.mono`` was a string on every instance and
    calling it crashed inside ``paintEvent`` — a hard access violation, not an
    exception anyone could read."""
    theme = build_theme()
    for role in ("headline", "title", "body", "body_strong", "label", "mono",
                 "mono_plain", "figure"):
        assert isinstance(getattr(theme, role)(), QFont), role


def test_verbatim_data_is_not_uppercased(app) -> None:
    """A file path shown in small caps is no longer the path it names."""
    theme = build_theme()
    assert theme.mono_plain().capitalization() == QFont.Capitalization.MixedCase
    assert theme.mono().capitalization() == QFont.Capitalization.AllUppercase


def _run_button(window):
    buttons = [b for b in window.setup.findChildren(QPushButton)
               if b.objectName() == "primary"]
    assert len(buttons) == 1
    return buttons[0]


def test_run_is_blocked_until_both_folders_are_chosen(window) -> None:
    button = _run_button(window)
    assert not button.isEnabled()

    window.setup.set_paths(source=r"D:\m")
    assert not button.isEnabled(), "an output folder is still missing"

    window.setup.set_paths(output=r"D:\m\out")
    assert button.isEnabled()


def test_setup_collects_exactly_what_was_entered(window) -> None:
    window.setup.set_paths(r"D:\m", r"D:\m\out", r"D:\m\index.xlsx")
    request = window.setup.request()
    assert request.source_root == r"D:\m"
    assert request.output_root == r"D:\m\out"
    assert request.master_index_path == r"D:\m\index.xlsx"
    assert request.profile is not None


def test_progress_screen_lists_each_document_as_it_lands(window) -> None:
    window.stack.setCurrentIndex(PROGRESS)
    window.progress.reset()
    pipeline = MockPipeline()
    events = []
    pipeline.run(_request(), lambda e: (events.append(e),
                                        window.progress.append(e)), lambda: False)
    assert len(events) == events[-1].total > 0
    assert events[-1].done == events[-1].total
    shown = [lab.text() for lab in window.progress.findChildren(QLabel)]
    for event in events:
        assert event.filename in shown

    window.progress.reset()
    QApplication.processEvents()
    assert not [lab for lab in window.progress.findChildren(QLabel)
                if lab.text() in {e.filename for e in events}]


def test_cancelling_stops_the_run_early() -> None:
    pipeline = MockPipeline()
    seen = []

    def cancel_after_three() -> bool:
        return len(seen) >= 3

    outcome = pipeline.run(_request(), seen.append, cancel_after_three)
    assert len(outcome.result.documents) == 3
    # A cancelled run must still be internally consistent — the summary it feeds
    # is the same code path as a completed one.
    assert outcome.result.pages_in == (outcome.result.pages_kept
                                       + outcome.result.pages_dropped)


def test_summary_then_detail_then_back(window) -> None:
    pipeline = MockPipeline()
    window.show_outcome(pipeline.run(_request(), lambda _e: None, lambda: False))
    assert window.stack.currentIndex() == SUMMARY

    window.show_flag(FLAG_OCR)
    assert window.stack.currentIndex() == DETAIL

    window.detail.back_requested.emit()
    assert window.stack.currentIndex() == SUMMARY

    window.summary.new_run_requested.emit()
    assert window.stack.currentIndex() == SETUP


def test_chip_click_opens_its_own_detail(window) -> None:
    pipeline = MockPipeline()
    window.show_outcome(pipeline.run(_request(), lambda _e: None, lambda: False))
    chips = window.summary.findChildren(Chip)
    assert {c.key for c in chips} == {"ocr", "unsupported", "reconciliation"}
    for chip in chips:
        chip.clicked.emit(chip.key)
        assert window.stack.currentIndex() == DETAIL


def test_a_second_run_leaves_none_of_the_first_on_screen(window) -> None:
    """Regression: widgets taken out of a layout keep painting until the event
    loop deletes them, so the first run's chips and figures were drawn under the
    second run's."""
    pipeline = MockPipeline()
    window.show_outcome(pipeline.run(_request(True), lambda _e: None, lambda: False))
    assert len(window.summary.findChildren(Chip)) == 3

    plain = RunRequest(r"D:\m", r"D:\m\out", pipeline.profiles()[2], None)
    window.show_outcome(pipeline.run(plain, lambda _e: None, lambda: False))
    QApplication.processEvents()
    keys = {c.key for c in window.summary.findChildren(Chip)
            if c.parent() is not None and c.isVisibleTo(window.summary)}
    assert FLAG_RECONCILIATION not in keys
    assert len(keys) == 2


def test_the_waterfall_is_the_section_picker(window) -> None:
    """Alex's ruling: clicking a row toggles that section and the stack
    re-flows. There is no separate checklist to keep in step with it."""
    from dociq.gui.widgets import ReductionWaterfall, WaterfallRow

    pipeline = MockPipeline()
    outcome = pipeline.run(_request(), lambda _e: None, lambda: False)
    window.show_outcome(outcome)

    waterfall = window.summary.findChild(ReductionWaterfall)
    rows = waterfall.findChildren(WaterfallRow)
    kinds = [r.kind for r in rows]
    assert kinds[0] == WaterfallRow.TOTAL
    assert kinds[-1] == WaterfallRow.CAPACITY
    assert kinds[-2] == WaterfallRow.RESULT
    assert WaterfallRow.AUTOMATIC in kinds

    before = window._view.tokens_after
    lever = next(r for r in rows if r.kind == WaterfallRow.EXPERT)
    lever.toggled.emit(lever.key)
    QApplication.processEvents()
    assert window._view.tokens_after != before


def test_a_locked_row_is_not_clickable(window) -> None:
    from dociq.gui.widgets import ReductionWaterfall, WaterfallRow

    pipeline = MockPipeline()
    window.show_outcome(pipeline.run(_request(), lambda _e: None, lambda: False))
    rows = window.summary.findChild(ReductionWaterfall).findChildren(WaterfallRow)
    locked = next(r for r in rows if r.kind == WaterfallRow.AUTOMATIC)
    before = window._view.tokens_after
    locked.toggled.emit(locked.key)  # the row does not emit this itself
    QApplication.processEvents()
    assert window._view.tokens_after == before


def test_every_row_states_its_own_number_and_state_in_words(window) -> None:
    """Non-visual parity: color encodes category, never magnitude, so each row
    must be readable in monochrome."""
    from dociq.gui.widgets import ReductionWaterfall, WaterfallRow

    pipeline = MockPipeline()
    window.show_outcome(pipeline.run(_request(), lambda _e: None, lambda: False))
    rows = window.summary.findChild(ReductionWaterfall).findChildren(WaterfallRow)
    for row in rows:
        name = row.accessibleName()
        assert name and any(ch.isdigit() for ch in name), name
    expert = [r.accessibleName() for r in rows if r.kind == WaterfallRow.EXPERT]
    assert all(("dropped" in n or "kept" in n) for n in expert), expert


def test_toggling_says_the_files_have_not_caught_up(window) -> None:
    from dociq.gui.widgets import ReductionWaterfall, WaterfallRow

    pipeline = MockPipeline()
    outcome = pipeline.run(_request(), lambda _e: None, lambda: False)
    window.show_outcome(outcome)
    stale = window.summary._stale.text()
    assert stale == ""
    window.summary.plan_changed.emit(outcome.plan.with_toggled("Photo logs"))
    QApplication.processEvents()
    assert "not been written" in window.summary._stale.text()


def test_the_forward_action_names_the_outcome(window) -> None:
    """"Run" was rejected: the button says what the operator gets, and there is
    exactly one such button on the screen."""
    button = _run_button(window)
    assert button.text() == "Build the reduced corpus"
    primaries = [b for b in window.setup.findChildren(QPushButton)
                 if b.objectName() == "primary"]
    assert len(primaries) == 1


def test_the_scope_and_the_time_sit_beside_the_action(window) -> None:
    preview = MockPipeline().preview_folder(r"D:\m")
    window.setup.set_preview(preview)
    scope = window.setup._scope.text()
    assert f"{preview.file_count:,} documents" in scope
    assert f"about {preview.estimated_minutes} minutes" in scope


def _no_horizontal_overflow(screen) -> bool:
    from PySide6.QtWidgets import QScrollArea

    area = screen.findChild(QScrollArea)
    return area.horizontalScrollBar().maximum() == 0


def test_the_summary_never_scrolls_sideways_at_any_scale(window) -> None:
    """Regression: the basis line was one unwrapped mono label. At the measured
    record's scale the provenance sentence made the page wider than the window
    and clipped the footer buttons off the right-hand edge."""
    from dociq.gui.mock_pipeline import at_measured_scale

    pipeline = MockPipeline()
    outcome = pipeline.run(_request(), lambda _e: None, lambda: False)
    window.resize(1040, 720)  # the product's minimum window
    window.show()
    window.show_outcome(outcome)
    QApplication.processEvents()
    assert _no_horizontal_overflow(window.summary)

    window.summary.plan_changed.emit(at_measured_scale(outcome.plan))
    QApplication.processEvents()
    assert _no_horizontal_overflow(window.summary)


def test_a_three_digit_multiplier_still_fits(window) -> None:
    """A larger matter than the measured one puts three digits in the caption."""
    from dociq.gui.pipeline import ReductionPlan

    pipeline = MockPipeline()
    outcome = pipeline.run(_request(), lambda _e: None, lambda: False)
    window.resize(1040, 720)
    window.show()
    window.show_outcome(outcome)
    huge = ReductionPlan(
        full_tokens=250 * outcome.plan.capacity,
        levers=outcome.plan.levers,
        capacity=outcome.plan.capacity,
        basis=outcome.plan.basis,
    )
    window.summary.plan_changed.emit(huge)
    QApplication.processEvents()
    assert "×" in window.summary._capacity_line.text()
    assert _no_horizontal_overflow(window.summary)


def test_a_stand_in_pipeline_discloses_itself_on_screen(window) -> None:
    """A shell that looks like the finished product while showing invented
    numbers is the most expensive misunderstanding this project could ship."""
    from dociq.gui.widgets import DisclosureBar

    bars = window.findChildren(DisclosureBar)
    assert len(bars) == 1
    text = bars[0].findChild(QLabel).text()
    assert "Sample data" in text
    # The measured record, stated — and asserted FROM the constants rather than
    # against literals. This test used to hard-code "298" and "17,732", which
    # were the pre-token figures of a superseded measurement (PyMuPDF over the
    # 298 source PDFs, no normalization, no OCR). When the constants were
    # corrected to what the pipeline actually emits, a literal assertion would
    # have gone red and invited someone to "fix" it back. A test cannot know
    # which number is true; it can refuse to let the banner and the fixture
    # disagree, which is the failure it is actually able to see.
    from dociq.gui.mock_pipeline import MEASURED_DOCUMENTS, MEASURED_PAGES

    assert str(MEASURED_DOCUMENTS) in text
    assert f"{MEASURED_PAGES:,}" in text

    class _Silent(MockPipeline):
        def disclosure(self) -> str:
            return ""

    quiet = MainWindow(pipeline=_Silent())
    try:
        assert quiet.findChildren(DisclosureBar) == []
    finally:
        quiet.close()


def test_the_chrome_is_us_english() -> None:
    """Long International is a US firm and §8 specifies "Analyze in Claude"."""
    import re

    gui = Path(__file__).resolve().parents[1] / "src" / "dociq"
    en_gb = re.compile(
        r"\b(analyse|analysed|organis\w+|recognis\w+|colour\w*|centre|"
        r"licence|behaviour\w*)\b", re.IGNORECASE)
    offenders = []
    for path in sorted(gui.rglob("*.py")):
        if path.name == "contracts.py":  # frozen — not ours to edit
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if en_gb.search(line):
                offenders.append(f"{path.name}:{n}: {line.strip()}")
    assert not offenders, "en-GB spelling found:\n" + "\n".join(offenders)


def test_the_offline_indicator_is_always_present(window) -> None:
    """Principle 4 is a selling point to law-firm IT, so it is standing chrome
    on every screen — not a line in an about box."""
    from dociq.gui.widgets import OfflineBadge

    badges = window.findChildren(OfflineBadge)
    assert len(badges) == 1
    for index in (SETUP, PROGRESS, SUMMARY, DETAIL):
        window.stack.setCurrentIndex(index)
        assert badges[0].parent() is not None
