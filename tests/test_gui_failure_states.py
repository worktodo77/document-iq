"""What the GUI does when something goes WRONG, and when something works.

Codex review #2, findings A-1 and A-2. Both are the same shape: the product did
the right thing and told nobody.

* **A-1** — "Build the upload package" discarded the ``PackageResult`` the
  pipeline returned and repainted the same view. Success showed no path, no
  counts, no size, no scope, no missing-document warning; failure went to
  ``print()``, and the shipped GUI is a **windowed** executable with no console
  for that text to reach. Success, failure and an ignored click were the same
  pixels.
* **A-2** — a run that raised appended one flagged row and the worker thread
  quit, leaving Cancel as the only control: a button whose job was to stop a
  thread that had already stopped. The only recovery was closing and reopening
  DocIQ.

**Disclosed, and it stays disclosed: nobody has ever driven this GUI with a
mouse.** Everything here runs under the offscreen platform plugin and asserts
widget state, not pixels. What that does and does not prove is stated in
``docs/verification/codex_r2_uigap_2026-08-04.md``.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtWidgets import QPushButton

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from .conftest import FIXTURES  # noqa: E402
from dociq.contracts import RunConfig, RunResult  # noqa: E402
from dociq.gui.pipeline import (  # noqa: E402
    FolderPreview,
    PackageResult,
    RunOutcome,
    RunRequest,
    TokenEstimate,
)
from dociq.gui.view_models import PackageScope, SCOPE_DATES  # noqa: E402

LOCK_ERROR = (
    r"[WinError 32] The process cannot access the file because it is being "
    r"used by another process: 'D:\m\out\sources.json'"
)
"""A real, documented hazard on the machine this ships to (Carbonite / AV file
locks), not an invented exception. It is what Codex's own failure scenario
names."""


@pytest.fixture(scope="session")
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _pump(app, predicate, timeout: float = 10.0) -> bool:
    """Spin the GUI event loop until ``predicate`` or the clock runs out.

    Bounded, not an unbounded ``processEvents`` loop: a deadlock must make the
    test FAIL rather than hang the suite until someone kills it. Copied in
    spirit from ``tests/test_bates_confirmation.py``, which needs the same thing
    for the same reason.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return False


def _outcome(request: RunRequest) -> RunOutcome:
    return RunOutcome(
        result=RunResult(
            config=RunConfig(source_root=request.source_root,
                             output_root=request.output_root),
            documents=(), unsupported=(), warnings=()),
        tokens_before=TokenEstimate(0, 2.0, 3.0),
        tokens_after=TokenEstimate(0, 2.0, 3.0),
        output_root=request.output_root,
    )


class _StubPipeline:
    """The smallest thing that satisfies ``PipelineAPI``.

    A real 6-stage run is not what either finding is about: A-2 is a property of
    :mod:`dociq.gui.main_window`'s worker handling and A-1 of what the handoff
    screen does with a returned record. Driving them through a real run would
    measure extraction and leave the actual property untested for minutes at a
    time. The seam is what is being exercised, and the seam is what a stand-in
    can honestly stand in for — ``tests/test_adapter.py`` and
    ``tests/test_seam_population.py`` hold the real implementation to it.
    """

    def __init__(self, *, raises: BaseException | None = None,
                 raises_times: int = 0) -> None:
        self.raises = raises
        self.raises_times = raises_times
        self.runs = 0
        self.requests: list[RunRequest] = []

    def profiles(self):
        return ()

    def preview_folder(self, path):
        return FolderPreview(0, 0)

    def disclosure(self):
        return ""

    def run(self, request, on_progress, should_cancel, confirm_bates=None):
        self.runs += 1
        self.requests.append(request)
        if self.raises is not None and self.runs <= self.raises_times:
            raise self.raises
        return _outcome(request)


class _PackagePipeline(_StubPipeline):
    """A stand-in that also offers §8 Path A."""

    def __init__(self, result: PackageResult | None = None,
                 error: BaseException | None = None) -> None:
        super().__init__()
        self.result = result
        self.error = error
        self.calls: list[tuple] = []

    def matter_layout_note(self, outcome) -> str:
        return ""

    def build_package(self, outcome, doc_ids, scope_statement) -> PackageResult:
        self.calls.append((doc_ids, scope_statement))
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


PACKAGE = PackageResult(
    root=r"D:\m\out\upload_package",
    file_count=14,
    total_bytes=3_407_872,
    scope_statement="SCOPE OF THIS PACKAGE — m\n" + "=" * 60 + "\n  ...\n",
    doc_count=12,
)


def _window(app, pipeline):
    from dociq.gui.main_window import MainWindow

    return MainWindow(pipeline=pipeline)


# ---------------------------------------------------------------------------
# A-2 — a failed run must not be a dead end
# ---------------------------------------------------------------------------


def test_a_failed_run_reaches_an_explicit_failed_state(app, tmp_path):
    """FAIL-BEFORE: ``_run_failed`` appended one flagged row and stopped. The
    title still read "Processing documents", Cancel was still enabled with
    nothing left to cancel, and no back or retry existed — ``recovery_offered()``
    was False and the only way out was closing the window.

    Driven through the WORKER THREAD, not by calling ``_run_failed`` directly:
    the finding is that the thread quits and the screen does not notice.
    """
    from dociq.gui.main_window import PROGRESS

    pipe = _StubPipeline(raises=OSError(LOCK_ERROR), raises_times=1)
    window = _window(app, pipe)
    try:
        window.start_run(RunRequest(str(tmp_path), str(tmp_path / "out")))
        assert _pump(app, lambda: window.progress.failed()), \
            "the run failed and the screen never said so"
        assert _pump(app, lambda: not window.thread_running(), timeout=5.0)

        assert window.stack.currentIndex() == PROGRESS
        assert "failed" in window.progress.title_text().lower()
        assert LOCK_ERROR in window.progress.error_text(), \
            "the pipeline's own reason was paraphrased or lost"
        assert not window.progress.cancel_enabled(), \
            "Cancel is still offered for a thread that has already stopped"
        assert window.progress.recovery_offered(), \
            "the failed screen offers no way out — the finding itself"
    finally:
        window.close()


def test_the_failed_screen_returns_to_setup(app, tmp_path):
    from dociq.gui.main_window import SETUP

    pipe = _StubPipeline(raises=OSError(LOCK_ERROR), raises_times=1)
    window = _window(app, pipe)
    try:
        window.start_run(RunRequest(str(tmp_path), str(tmp_path / "out")))
        assert _pump(app, lambda: window.progress.failed())
        assert _pump(app, lambda: not window.thread_running(), timeout=5.0)
        window.progress.back_requested.emit()
        assert window.stack.currentIndex() == SETUP
    finally:
        window.close()


def test_retry_reruns_the_same_request_and_can_succeed(app, tmp_path):
    """The action has to WORK, not merely exist. A retry button that navigated
    somewhere and started nothing would satisfy a naive assertion and leave the
    operator exactly where the finding put them."""
    from dociq.gui.main_window import SUMMARY

    request = RunRequest(str(tmp_path), str(tmp_path / "out"))
    pipe = _StubPipeline(raises=OSError(LOCK_ERROR), raises_times=1)
    window = _window(app, pipe)
    try:
        window.start_run(request)
        assert _pump(app, lambda: window.progress.failed())
        assert _pump(app, lambda: not window.thread_running(), timeout=5.0)

        window.progress.retry_requested.emit()
        assert _pump(app, lambda: window.stack.currentIndex() == SUMMARY), \
            "the retry never produced a run that finished"
        assert pipe.runs == 2
        assert pipe.requests[1] == request, \
            "the retry ran something other than the request that failed"
        assert not window.progress.failed(), \
            "the second attempt still shows the first attempt's error"
        assert _pump(app, lambda: not window.thread_running(), timeout=5.0)
    finally:
        window.close()


def test_cancel_is_dead_after_a_successful_run_too(app, tmp_path):
    """Settlement, not failure, is what disables it. The operator can walk back
    onto this screen from the summary, and a live Cancel there would offer to
    stop a run that finished."""
    window = _window(app, _StubPipeline())
    try:
        window.start_run(RunRequest(str(tmp_path), str(tmp_path / "out")))
        assert _pump(app, lambda: not window.thread_running(), timeout=5.0)
        assert not window.progress.cancel_enabled()
        assert not window.progress.failed(), \
            "a successful run was rendered as a failure"
    finally:
        window.close()


def test_an_aborted_run_settles_without_being_called_a_failure(app, tmp_path):
    """A stop the operator asked for is not a fault, and must not be worded as
    one — but it is the same dead end if the screen offers no way out."""
    from dociq.runstate import RunAborted

    pipe = _StubPipeline(raises=RunAborted("the run was stopped"),
                         raises_times=1)
    window = _window(app, pipe)
    try:
        window.start_run(RunRequest(str(tmp_path), str(tmp_path / "out")))
        assert _pump(app, lambda: window.progress.error_text() != "")
        assert _pump(app, lambda: not window.thread_running(), timeout=5.0)
        assert "stopped" in window.progress.title_text().lower()
        assert "failed" not in window.progress.title_text().lower()
        assert window.progress.recovery_offered()
        assert not window.progress.cancel_enabled()
    finally:
        window.close()


@pytest.mark.parametrize("attempt", range(30))
def test_the_failed_state_is_reached_every_time(app, tmp_path, attempt):
    """Thirty runs, because this crosses a thread boundary and one green run of
    a threaded path is not evidence of anything.

    The race it is looking for is real: ``failed`` is emitted from the worker
    thread and ``QThread.quit`` is connected to the same signal, so the screen's
    transition and the thread's teardown are ordered only by Qt's queued
    delivery.
    """
    pipe = _StubPipeline(raises=OSError(LOCK_ERROR), raises_times=1)
    window = _window(app, pipe)
    try:
        window.start_run(RunRequest(str(tmp_path), str(tmp_path / "out")))
        assert _pump(app, lambda: window.progress.failed(), timeout=15.0)
        assert _pump(app, lambda: not window.thread_running(), timeout=15.0)
        assert not window.progress.cancel_enabled()
        assert window.progress.recovery_offered()
        assert LOCK_ERROR in window.progress.error_text()
    finally:
        window.close()


def test_a_second_run_cannot_start_over_a_live_one(app, tmp_path):
    """Two live workers would interleave into one progress list and the second
    outcome would overwrite the first. Guarded in ``start_run``."""
    import threading

    release = threading.Event()

    class _Blocking(_StubPipeline):
        def run(self, request, on_progress, should_cancel, confirm_bates=None):
            self.runs += 1
            self.requests.append(request)
            release.wait(10.0)
            return _outcome(request)

    pipe = _Blocking()
    window = _window(app, pipe)
    try:
        request = RunRequest(str(tmp_path), str(tmp_path / "out"))
        window.start_run(request)
        assert _pump(app, lambda: pipe.runs == 1)
        window.start_run(request)
        app.processEvents()
        assert pipe.runs == 1, "a second run started over a live one"
    finally:
        release.set()
        _pump(app, lambda: not window.thread_running(), timeout=5.0)
        window.close()


# ---------------------------------------------------------------------------
# A-1 — the package build must be observable, either way
# ---------------------------------------------------------------------------


def _at_handoff(app, pipe):
    window = _window(app, pipe)
    window.show_outcome(_outcome(RunRequest(r"D:\m", r"D:\m\out")))
    window.show_handoff()
    return window


def test_a_successful_build_states_the_path_counts_and_size(app):
    """FAIL-BEFORE: ``_build_package`` called the builder, discarded the result
    and called ``_paint_handoff()``. Every assertion below read "" — the screen
    was byte-identical before and after the click."""
    pipe = _PackagePipeline(result=PACKAGE)
    window = _at_handoff(app, pipe)
    try:
        assert window.handoff.package_headline() == "", \
            "an outcome panel is showing before anything was built"

        window.handoff.build_package_requested.emit(PackageScope())
        headline = window.handoff.package_headline()
        lines = window.handoff.package_lines()

        assert "built" in headline.lower()
        assert PACKAGE.root in lines, "the operator is not told WHERE it went"
        assert "12 documents" in lines
        assert "14 files" in lines
        assert "3.2 MB" in lines  # 3,407,872 bytes, in binary MB
        assert window.handoff.package_missing_text() == ""
    finally:
        window.close()


def test_a_short_package_names_the_documents_it_could_not_include(app):
    """B-3's user-visible half. The seam now carries ``missing``; this is the
    assertion that it REACHES A RENDERED SCREEN, which is the thing the old
    private-attribute test could not make."""
    pipe = _PackagePipeline(
        result=replace(PACKAGE, doc_count=11, missing=("LI-99999",)))
    window = _at_handoff(app, pipe)
    try:
        window.handoff.build_package_requested.emit(PackageScope())
        note = window.handoff.package_missing_text()
        assert "LI-99999" in note, \
            "a package one document short of its own scope statement said so " \
            "nowhere on screen"
        assert "NOT in this package" in note
    finally:
        window.close()


def test_a_failed_build_is_an_error_on_the_same_screen(app):
    """FAIL-BEFORE: the exception went to ``print()`` and the function
    returned. The shipped GUI is a windowed executable — there is no console —
    so the screen was unchanged and the operator could not tell the failure from
    a click that did nothing."""
    pipe = _PackagePipeline(error=OSError(LOCK_ERROR))
    window = _at_handoff(app, pipe)
    try:
        window.handoff.build_package_requested.emit(PackageScope())
        headline = window.handoff.package_headline()
        lines = window.handoff.package_lines()

        assert "NOT built" in headline
        assert LOCK_ERROR in lines, "the reason was paraphrased or lost"
        assert "EARLIER build" in lines, (
            "the operator is not warned that a package already on disk is from "
            "a previous run — the upload-the-wrong-package failure"
        )
    finally:
        window.close()


def test_changing_the_scope_clears_the_last_build(app):
    """A success banner surviving a scope change would tell the operator that
    the package on disk covers the set now described beneath it. That is the
    D-20 subset confusion wearing a reassuring colour."""
    pipe = _PackagePipeline(result=PACKAGE)
    window = _at_handoff(app, pipe)
    try:
        window.handoff.build_package_requested.emit(PackageScope())
        assert window.handoff.package_headline() != ""
        window._rescope(PackageScope(SCOPE_DATES, "2021-01-01", "2021-12-31"))
        assert window.handoff.package_headline() == ""
        assert window.handoff.package_lines() == ""
    finally:
        window.close()


def test_revisiting_the_handoff_clears_the_last_build(app):
    pipe = _PackagePipeline(result=PACKAGE)
    window = _at_handoff(app, pipe)
    try:
        window.handoff.build_package_requested.emit(PackageScope())
        assert window.handoff.package_headline() != ""
        window.show_handoff()
        assert window.handoff.package_headline() == ""
    finally:
        window.close()


def test_the_build_never_lets_an_exception_reach_the_event_loop(app):
    """Any exception, not a named list. An unhandled exception out of a slot is
    a crash on Windows with PySide6, and the class this does NOT name is the
    one that would go back to being silent."""
    for error in (OSError(LOCK_ERROR), ValueError("scope selects nothing"),
                  RuntimeError("emit layer exploded"), KeyError("clean_text")):
        pipe = _PackagePipeline(error=error)
        window = _at_handoff(app, pipe)
        try:
            window.handoff.build_package_requested.emit(PackageScope())
            assert "NOT built" in window.handoff.package_headline(), error
            assert str(error).strip("'") in window.handoff.package_lines(), error
        finally:
            window.close()


def test_a_refused_folder_pair_is_caught_when_it_is_PICKED(app) -> None:
    """D-43's first finding, from the first human-driven session.

    An operator chose a documents folder and an output folder, pressed the one
    forward action, waited, and was then told the pair was refused because the
    output folder was a PARENT of the source. The setup screen had known both
    paths since the second click.

    Two properties, and the second is the one that makes it a preflight rather
    than a notice: the reason appears where the folders were chosen, AND the
    forward action is disabled. A refusal an operator can click past is not a
    preflight — the run would refuse a moment later anyway, which is the
    experience this removes.
    """
    from dociq.adapter import RealPipeline
    from dociq.gui.main_window import MainWindow

    win = MainWindow(RealPipeline())
    try:
        run = [b for b in win.setup.findChildren(QPushButton)
               if "Build the reduced" in b.text()][0]

        # The line edits MUST be populated, or this test passes for the wrong
        # reason: the button is disabled whenever either folder is unset, so
        # emitting the signal alone proves nothing about the warning. Caught by
        # mutating `ready = chosen and not warning` to `ready = chosen` and
        # watching this test pass anyway.
        def pick(src: str, out: str) -> None:
            win.setup._source.setText(src)
            win.setup._output.setText(out)
            win.setup.folders_changed.emit(src, out)

        # Output is a parent of source — the pair that was refused at run time.
        pick(str(FIXTURES), str(FIXTURES.parent))
        assert not run.isEnabled(), (
            "the run button is live over a folder pair the run will refuse")
        assert "output folder" in win.setup._blocker.text().lower(), (
            "the operator is not told WHICH choice is the problem")

        # A source folder that is not there.
        pick(str(FIXTURES / "no-such-folder"), str(FIXTURES.parent / "out"))
        assert not run.isEnabled()
        assert "could not be read" in win.setup._blocker.text()

        # A usable pair re-enables it — a check that refuses everything is not
        # a check.
        pick(str(FIXTURES), str(FIXTURES.parent / "dociq-out"))
        assert run.isEnabled(), (
            "a valid pair is still refused — the check refuses everything")
    finally:
        win.deleteLater()


def test_the_screen_and_the_run_share_one_folder_rule() -> None:
    """They must not be able to disagree.

    The OCR review flag had exactly this defect one screen over: the log and the
    screen each implemented "is this page low-confidence" and answered 99 and 80
    for the same run. The setup screen therefore does not own a copy of the
    folder rule — it asks the pipeline, which calls the same function
    :func:`walker.run` calls.
    """
    from dociq.adapter import RealPipeline
    from dociq.ingest.walker import preflight_folders

    pipe = RealPipeline()
    for src, out in ((str(FIXTURES), str(FIXTURES.parent)),
                     (str(FIXTURES), str(FIXTURES.parent / "out")),
                     (str(FIXTURES / "nope"), str(FIXTURES.parent / "out"))):
        assert pipe.check_folders(src, out) == preflight_folders(src, out)
