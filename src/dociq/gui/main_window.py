"""The single window, and the only place that knows about the pipeline seam.

Screens emit intent; this class decides what happens. The run itself is done on
a worker thread — a 17,000-page matter takes minutes and a frozen window during
those minutes would be indistinguishable from a crash.
"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from dociq.gui.pipeline import (
    PipelineAPI,
    ProfileInfo,
    RunOutcome,
    RunRequest,
    get_pipeline,
)
from dociq.gui.screens import (
    DetailScreen,
    HandoffScreen,
    ProfileChecklistScreen,
    ProgressScreen,
    SetupScreen,
    SummaryScreen,
)
from dociq.gui.theme import Theme, build_theme, stylesheet
from dociq.gui.view_models import (
    PackageScope,
    SummaryView,
    build_handoff,
    build_profile_checklist,
    build_summary,
)
from dociq.gui.widgets import DisclosureBar, HeaderBar, Rule, ICON_ICO

SETUP, PROGRESS, SUMMARY, DETAIL, CHECKLIST, HANDOFF = range(6)


class _RunWorker(QObject):
    """Runs one pipeline call off the GUI thread."""

    progressed = Signal(object)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, pipeline: PipelineAPI, request: RunRequest) -> None:
        super().__init__()
        self._pipeline = pipeline
        self._request = request
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def start(self) -> None:
        try:
            outcome = self._pipeline.run(
                self._request,
                self.progressed.emit,
                lambda: self._cancelled,
            )
        except Exception as exc:  # a failed run must not take the window with it
            self.failed.emit(str(exc))
            return
        self.finished.emit(outcome)


class MainWindow(QMainWindow):
    """LI Document IQ."""

    def __init__(self, pipeline: PipelineAPI | None = None,
                 theme: Theme | None = None) -> None:
        super().__init__()
        self._pipeline = pipeline or get_pipeline()
        self.theme = theme or build_theme()
        self._thread: QThread | None = None
        self._worker: _RunWorker | None = None
        self._view: SummaryView | None = None
        self._outcome: RunOutcome | None = None
        self._scope = PackageScope()

        self.setWindowTitle("LI Document IQ")
        self.setMinimumSize(1040, 720)
        self.setStyleSheet(stylesheet(self.theme))
        if ICON_ICO.is_file():
            self.setWindowIcon(QIcon(str(ICON_ICO)))

        root = QWidget()
        lay = QVBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(HeaderBar(self.theme))
        lay.addWidget(Rule(self.theme, strong=True))
        disclosure = getattr(self._pipeline, "disclosure", lambda: "")()
        if disclosure:
            lay.addWidget(DisclosureBar(disclosure, self.theme))

        self.stack = QStackedWidget()
        self.setup = SetupScreen(self.theme, self._pipeline.profiles())
        self.progress = ProgressScreen(self.theme)
        self.summary = SummaryScreen(self.theme)
        self.detail = DetailScreen(self.theme)
        self.checklist = ProfileChecklistScreen(self.theme)
        self.handoff = HandoffScreen(self.theme)
        for screen in (self.setup, self.progress, self.summary, self.detail,
                       self.checklist, self.handoff):
            self.stack.addWidget(screen)
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        self.setup.run_requested.connect(self.start_run)
        self.setup.source_chosen.connect(self._preview_folder)
        self.summary.plan_changed.connect(self._replan)
        self.progress.cancel_requested.connect(self.cancel_run)
        self.summary.flag_selected.connect(self.show_flag)
        self.summary.new_run_requested.connect(
            lambda: self.stack.setCurrentIndex(SETUP))
        self.summary.open_output_requested.connect(self._open_output)
        self.detail.back_requested.connect(
            lambda: self.stack.setCurrentIndex(SUMMARY))
        self.setup.profile_review_requested.connect(self.show_profile_checklist)
        self.checklist.back_requested.connect(
            lambda: self.stack.setCurrentIndex(SETUP))
        self.checklist.profile_accepted.connect(
            lambda _p: self.stack.setCurrentIndex(SETUP))
        self.summary.handoff_requested.connect(self.show_handoff)
        self.handoff.back_requested.connect(
            lambda: self.stack.setCurrentIndex(SUMMARY))
        self.handoff.scope_changed.connect(self._rescope)
        self.handoff.open_matter_folder_requested.connect(self._open_output)
        self.handoff.build_package_requested.connect(self._build_package)

    # -- flow ---------------------------------------------------------------

    def _preview_folder(self, path: str) -> None:
        """Ask the pipeline what is in the folder, so the action can state its
        own scope. Failure is non-fatal: an unreadable folder must not stop the
        operator from choosing a different one."""
        try:
            self.setup.set_preview(self._pipeline.preview_folder(path))
        except Exception as exc:
            print(f"[dociq] folder preview unavailable for {path}: {exc}")

    def _replan(self, plan) -> None:
        """A lever was toggled on the waterfall: re-project the same run.

        The run is not re-executed and the files on disk do not change — which
        is exactly why the screen is then marked stale.
        """
        if self._outcome is None:
            return
        self._view = build_summary(self._outcome, plan)
        self.summary.show_summary(self._view)
        self.summary.mark_stale()

    def start_run(self, request: RunRequest) -> None:
        self.progress.reset()
        self.stack.setCurrentIndex(PROGRESS)

        thread = QThread(self)
        worker = _RunWorker(self._pipeline, request)
        worker.moveToThread(thread)
        thread.started.connect(worker.start)
        worker.progressed.connect(self.progress.append)
        worker.finished.connect(self._run_finished)
        worker.failed.connect(self._run_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        self._thread, self._worker = thread, worker
        thread.start()

    def cancel_run(self) -> None:
        if self._worker is not None:
            self._worker.cancel()

    def _run_finished(self, outcome: RunOutcome) -> None:
        self.show_outcome(outcome)

    def _run_failed(self, message: str) -> None:
        # Failure is disclosed on the screen the operator is already looking at,
        # not in a modal they will dismiss without reading.
        self.progress.append(
            type("_E", (), {"done": 1, "total": 1, "filename": "Run failed",
                            "status": message, "flagged": True})()
        )

    def show_outcome(self, outcome: RunOutcome) -> None:
        """Display a finished run. Separate from the worker path so a render
        harness or a test can drive the summary without a thread."""
        self._outcome = outcome
        self._view = build_summary(outcome)
        self.summary.show_summary(self._view)
        self.stack.setCurrentIndex(SUMMARY)

    # -- §6 profiling checklist (E-1) ---------------------------------------

    def show_profile_checklist(self, profile: ProfileInfo) -> None:
        """Show what a profile keeps and drops, before a run commits to it.

        The section rules come over the seam. ``PipelineAPI`` has no method for
        them yet (stop-the-line A-11), so they are asked for by an OPTIONAL
        adapter hook and their absence is a state the screen renders rather
        than a crash: an empty checklist that says it is empty is safe, an
        empty checklist that looks complete is not.
        """
        rules = getattr(self._pipeline, "profile_rules", None)
        levers, basis, source = (), None, ""
        if rules is not None:
            try:
                levers, basis, source = rules(profile)
            except Exception as exc:  # a bad profile must not take the window
                print(f"[dociq] profile rules unavailable for "
                      f"{profile.profile_id}: {exc}")
                levers, basis, source = (), None, ""
        from dociq.gui.pipeline import TokenBasis

        self.checklist.show_checklist(build_profile_checklist(
            profile, tuple(levers), basis or TokenBasis(), source))
        self.stack.setCurrentIndex(CHECKLIST)

    # -- §8 handoff (E-3) ---------------------------------------------------

    def show_handoff(self) -> None:
        self._scope = PackageScope()
        self._paint_handoff()
        self.stack.setCurrentIndex(HANDOFF)

    def _rescope(self, scope: PackageScope) -> None:
        self._scope = scope
        self._paint_handoff()

    def _paint_handoff(self) -> None:
        if self._outcome is None:
            return
        builder = getattr(self._pipeline, "build_package", None)
        note = getattr(self._pipeline, "matter_layout_note", None)
        self.handoff.show_handoff(build_handoff(
            self._outcome,
            scope=self._scope,
            package_available=builder is not None,
            layout_note=note(self._outcome) if note is not None else "",
        ))

    def _build_package(self, scope: PackageScope) -> None:
        """Ask the PIPELINE to write the package. The GUI writes no files.

        Assembling ``upload_package/`` is emit-layer work (``emit/handoff.py``)
        and the GUI may not import it. What crosses the seam is the operator's
        scope and the scope statement that must travel inside the package.
        """
        if self._outcome is None:
            return
        builder = getattr(self._pipeline, "build_package", None)
        if builder is None:
            return
        view = build_handoff(self._outcome, scope=scope, package_available=True)
        try:
            builder(self._outcome,
                    doc_ids=tuple(d.doc_id for d in view.selected()),
                    scope_statement=view.scope_statement())
        except Exception as exc:
            print(f"[dociq] upload package not built: {exc}")
            return
        self._paint_handoff()

    def show_flag(self, key: str) -> None:
        if self._view is None:
            return
        self.detail.show_group(self._view.flag(key))
        self.stack.setCurrentIndex(DETAIL)

    def _open_output(self) -> None:
        if self._view is None or not self._view.output_root:
            return
        path = Path(self._view.output_root)
        if path.is_dir():
            os.startfile(str(path))  # noqa: S606 — Windows-only product (§10)

    def closeEvent(self, event) -> None:
        self.cancel_run()
        if self._thread is not None and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(2000)
        super().closeEvent(event)
