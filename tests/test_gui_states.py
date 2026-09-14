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
    return RunRequest(
        r"D:\m", r"D:\m\out",
        master_index_path=r"D:\m\index.xlsx" if with_index else None)


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

    plain = RunRequest(r"D:\m", r"D:\m\out",
                       master_index_path=None)
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


def test_the_time_beside_the_action_follows_the_image_setting(window) -> None:
    """A-25 (D-51). The estimate comes from runs that read no picture on a text
    page, which is closest to a run that SKIPS them (not the same: the quick
    pass still reads a stamped scan whose image covers 90% or more of the page,
    D-54). Untick the box and the run is a different, unmeasured one, so the
    screen stops quoting a time for it -- and quotes it again when the box is
    ticked back.

    FAIL-BEFORE: the preview carried one figure, shown whatever the run would do.
    """
    from PySide6.QtWidgets import QCheckBox

    from dociq.gui.pipeline import FolderPreview

    window.setup.set_preview(FolderPreview(
        file_count=12, total_bytes=1_000_000_000, by_extension=((".pdf", 12),),
        estimated_minutes=40, estimated_minutes_reading_images=0))
    boxes = window.setup.findChildren(QCheckBox)
    assert len(boxes) == 1, "the setup screen has no image-skip switch"
    box = boxes[0]
    assert box.isChecked(), "a run skips pictures on text pages unless told otherwise"
    assert "about 40 minutes" in window.setup._scope.text()
    help_text = " ".join(lab.text() for lab in window.setup.findChildren(QLabel))
    # D-54 and the reviews' wording findings. A scan carrying a typed stamp is
    # still read, so the sentence that said only scans with NO typed text are
    # read is withdrawn; the thresholds are stated, because "scanned pages are
    # still read" was false for a stamped scan just under 90% and "the full
    # reading" was false for a picture under a quarter of the page, which
    # neither setting reads; and the 12-document timing (D-49) compared reading
    # every such picture with reading none, which is not what ticking the box
    # saves.
    for sentence in ("cover a quarter or more of a page that also has typed text",
                     "lists every page it skipped as not read",
                     "A scan whose image covers 90% or more of its page is still "
                     "read, even with a typed stamp.",
                     "Pictures covering less than a quarter of a typed page are not "
                     "read whether the box is ticked or not.",
                     "On a timed sample of 12 documents",
                     "about 3.4 times as long as reading none",
                     "the saving on a whole matter has not been measured",
                     "Untick it to read those pictures before relying on the results."):
        assert sentence in help_text, sentence
    for withdrawn in ("no typed text", "Scanned pages are still read",
                      "for the full reading"):
        assert withdrawn not in help_text, withdrawn

    box.setChecked(False)
    assert "minutes" not in window.setup._scope.text()
    assert window.setup.request().skip_images_on_text_pages is False

    box.setChecked(True)
    assert "about 40 minutes" in window.setup._scope.text()
    assert window.setup.request().skip_images_on_text_pages is True


@pytest.mark.parametrize("reviewed_skip", [True, False],
                         ids=["reviewed-skipping", "reviewed-reading"])
def test_the_retained_approval_hint_follows_the_picture_setting(
        app, reviewed_skip) -> None:
    """D-51 review finding 1. The picture setting is part of the recognition an
    approval was reviewed under, so Stage 4 refuses a retained approval when the
    box no longer matches. The setup screen compared only the project names and
    went on saying the approval "still applies" -- then the run refused it.

    Driven through the real capture point, so the setting the hint compares is
    the one the pipeline recorded, and toggled both ways from each reviewed value.

    FAIL-BEFORE: flipping the box left "still apply" on screen.
    """
    from dociq import adapter

    from .conftest import FIXTURES

    window = MainWindow(adapter.RealPipeline())
    try:
        window._request = RunRequest(str(FIXTURES), str(FIXTURES / "out"),
                                     skip_images_on_text_pages=reviewed_skip)
        window._capture_approval("progress-photographs", True)
        assert len(window._approvals) == 1, "no approval was captured"
        box = window.setup._skip_images

        def hint() -> str:
            return window.setup._tokens_hint.text()

        box.setChecked(reviewed_skip)
        assert "still apply" in hint() and "NO LONGER APPLY" not in hint(), hint()

        box.setChecked(not reviewed_skip)
        assert "NO LONGER APPLY" in hint(), hint()
        assert "still apply" not in hint(), hint()
        assert "quick first pass" in hint(), (
            "the stale message does not say it was the picture setting: " + hint())
        assert "kept" in hint().lower(), hint()

        box.setChecked(reviewed_skip)
        assert "still apply" in hint() and "NO LONGER APPLY" not in hint(), hint()
    finally:
        window.close()


def test_a_picture_setting_that_read_nothing_cannot_make_an_approval_stale(
        app) -> None:
    """The comparison is normalized the way ``recognition_fingerprint`` is. With
    OCR off no picture is read whichever way the box is set, both settings give
    one fingerprint, and Stage 4 applies the approval either way -- so the screen
    must not warn that it no longer applies. Warning there would be A-R2-1 again:
    telling the operator pages will be kept, moments before the run drops them.
    """
    from dociq import adapter
    from dociq.contracts import recognition_fingerprint
    from dociq.sections.templates import PROGRESS_REPORT

    from .conftest import FIXTURES

    window = MainWindow(adapter.RealPipeline(ocr_enabled=False))
    try:
        window._request = RunRequest(str(FIXTURES), str(FIXTURES / "out"),
                                     skip_images_on_text_pages=True)
        window._capture_approval("progress-photographs", True)
        (approval,) = window._approvals
        # Stage 4's side of the same question: the run with the box unticked
        # computes this fingerprint, and it is the approval's.
        assert approval.recognition == recognition_fingerprint(
            project_tokens=(), template_id=PROGRESS_REPORT.template_id,
            template_version=PROGRESS_REPORT.version, ocr_ran=False,
            skip_images_on_text_pages=False)

        window.setup._skip_images.setChecked(False)
        hint = window.setup._tokens_hint.text()
        assert "still apply" in hint and "NO LONGER APPLY" not in hint, hint
    finally:
        window.close()


_NAMES = "the project names"
_PICTURES = "the quick first pass setting"


@pytest.mark.parametrize(
    "reviewed, field, box, applies, names_named, pictures_named",
    [
        ([(("ALPHA7",), True)], "ALPHA7, HARBOR2", True, 0, True, False),
        ([(("ALPHA7",), True)], "ALPHA7", False, 0, False, True),
        ([(("ALPHA7",), True)], "ALPHA7, HARBOR2", False, 0, True, True),
        ([(("ALPHA7",), False), (("HARBOR2",), True)], "ALPHA7", True, 0, True, True),
        ([(("HARBOR2",), True), (("ALPHA7",), False)], "ALPHA7", True, 0, True, True),
        ([(("ALPHA7",), True), (("HARBOR2",), True)], "HARBOR2", True, 1, True, False),
        ([(("ALPHA7",), False), (("ALPHA7",), True)], "ALPHA7", True, 1, False, True),
        ([(("ALPHA7",), False), (("HARBOR2",), True), (("ALPHA7",), True)],
         "ALPHA7", True, 1, True, True),
        ([(("ALPHA7",), True), (("HARBOR2",), True), (("ALPHA7",), False)],
         "ALPHA7", True, 1, True, True),
        ([(("ALPHA7",), True), (("ALPHA7",), True)], "alpha7", True, 2, False, False),
    ],
    ids=["one-names-only", "one-box-only", "one-names-and-box",
         "two-stale-pictures-first", "two-stale-names-first",
         "partial-names", "partial-pictures",
         "partial-pictures-names-applies", "partial-applies-names-pictures",
         "all-apply"])
def test_the_retained_approval_hint_names_exactly_the_reasons_that_are_true(
        app, reviewed, field, box, applies, names_named, pictures_named) -> None:
    """D-51 round-2 review, finding 3. The hint names why approvals went stale,
    and no test checked WHICH reason it named: the last approval deciding a
    reason (O1, O2), the picture reason named whatever changed (O3), and the
    partial message dropping its reason (O5) all survived. The review's states:
    several approvals with different settings in both orders, the partial
    branch, names alone, box alone, both. Each reason phrase must appear
    exactly when it is a reason for some stale approval; the expectation is
    written out per state, not recomputed by the rule under test.
    """
    from dociq import adapter

    root = r"D:\matter-A"
    window = MainWindow(adapter.RealPipeline())
    try:
        for n, (tokens, skip) in enumerate(reviewed):
            window._request = RunRequest(root, root + r"\out", project_tokens=tokens,
                                         skip_images_on_text_pages=skip)
            window._capture_approval(("progress-photographs", "cover-page",
                                      "blank-page")[n], True)
        assert len(window._approvals) == len(reviewed), window._approvals
        window.setup._tokens.setText(field)
        window.setup._skip_images.setChecked(box)
        hint = window.setup._tokens_hint.text()

        total = len(reviewed)
        if applies == total:
            assert f"{total} approval(s) carried" in hint and "still apply" in hint, hint
        elif applies == 0:
            assert hint.startswith("Changing "), hint
            assert f"the {total} approval(s)" in hint and "NO LONGER APPLY" in hint, hint
        else:
            assert f"{applies} of {total} approval(s)" in hint, hint
            assert f"{total - applies} NO LONGER APPLY because " in hint, hint
        assert (_NAMES in hint) is names_named, hint
        assert (_PICTURES in hint) is pictures_named, hint
    finally:
        window.close()


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


def test_the_setup_steps_are_numbered_1_to_n_exactly_once(window) -> None:
    """The numbers are the operator's place in a sequence, so two steps sharing
    one is a wrong instruction, not a cosmetic slip.

    Asserted as a PROPERTY of the whole screen rather than against a fixed list,
    because the defect this catches is created by *inserting* a step: D-39's
    project-names field went in as step 3 and left the output folder still
    reading 4. Pinning the expected numbers would have to be edited by the same
    change that breaks them, and would not have caught it.
    """
    from PySide6.QtWidgets import QLabel

    numbers = [lab.text().strip()
               for lab in window.setup.findChildren(QLabel)
               if lab.text().strip().isdigit() and len(lab.text().strip()) <= 2]
    assert numbers == [str(n) for n in range(1, len(numbers) + 1)], numbers
    assert len(numbers) >= 4


def test_the_offline_indicator_is_always_present(window) -> None:
    """Principle 4 is a selling point to law-firm IT, so it is standing chrome
    on every screen — not a line in an about box."""
    from dociq.gui.widgets import OfflineBadge

    badges = window.findChildren(OfflineBadge)
    assert len(badges) == 1
    for index in (SETUP, PROGRESS, SUMMARY, DETAIL):
        window.stack.setCurrentIndex(index)
        assert badges[0].parent() is not None
