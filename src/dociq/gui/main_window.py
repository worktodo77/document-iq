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

from dociq.gui.pipeline import PipelineAPI, RunOutcome, RunRequest, get_pipeline
from dociq.gui.screens import (
    DetailScreen,
    ProgressScreen,
    SetupScreen,
    SummaryScreen,
)
from dociq.gui.theme import Theme, build_theme, stylesheet
from dociq.gui.view_models import SummaryView, build_summary
from dociq.gui.widgets import HeaderBar, Rule, ICON_ICO

SETUP, PROGRESS, SUMMARY, DETAIL = range(4)


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

        self.stack = QStackedWidget()
        self.setup = SetupScreen(self.theme, self._pipeline.profiles())
        self.progress = ProgressScreen(self.theme)
        self.summary = SummaryScreen(self.theme)
        self.detail = DetailScreen(self.theme)
        for screen in (self.setup, self.progress, self.summary, self.detail):
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
