"""The four screens of §9's primary flow.

    folder → profile → optional master index → Run → progress → summary

Each screen owns its layout and nothing else: it emits what the operator did and
the window decides what happens next. That is what keeps the pipeline seam a
seam — no screen calls the pipeline directly.

The language is deliberately plain. The operator is a claims professional: the
screens say "folder of documents", not "source root", and "pages removed by the
profile", not "Disposition.DROP".
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from dociq.gui.pipeline import (
    FolderPreview,
    ProgressEvent,
    RunRequest,
)
from dociq.gui.theme import PAGE_MARGIN, UNIT, Theme
from dociq.gui.view_models import (
    SCOPE_ALL,
    SCOPE_DATES,
    SCOPE_TYPES,
    ChecklistRow,
    FlagGroup,
    HandoffView,
    PackageScope,
    ProfileChecklistView,
    SummaryView,
)
from dociq.gui.widgets import (
    Chip,
    ReductionWaterfall,
    Rule,
    SectionLabel,
    StatFigure,
)


def clear_layout(layout, keep_trailing: int = 0) -> None:
    """Empty ``layout``, leaving its last ``keep_trailing`` items alone.

    ``deleteLater()`` alone is not enough and the failure is silent-looking but
    visible: a widget taken out of a layout keeps its parent and its geometry,
    so it goes on painting where it was until the event loop gets round to
    deleting it. On the summary screen that showed as the previous run's figures
    and chips drawn underneath the new ones. Unparenting first is what actually
    removes it from the screen.
    """
    while layout.count() > keep_trailing:
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())


def _button(text: str, theme: Theme, kind: str = "secondary") -> QPushButton:
    b = QPushButton(text)
    b.setObjectName(kind)
    b.setFont(theme.body(10))
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def _muted(text: str, theme: Theme, pt: int = 9) -> QLabel:
    lab = QLabel(text)
    lab.setFont(theme.body(pt))
    lab.setObjectName("muted")
    lab.setWordWrap(True)
    return lab


def _page(theme: Theme) -> tuple[QWidget, QVBoxLayout]:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(PAGE_MARGIN, UNIT * 4, PAGE_MARGIN, UNIT * 4)
    lay.setSpacing(UNIT * 2)
    return w, lay


class _Step(QWidget):
    """One numbered row of the setup screen: label, control, hairline under."""

    def __init__(self, number: int, label: str, theme: Theme,
                 control: QWidget, hint: str = "", parent=None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(UNIT // 2)
        head = QHBoxLayout()
        head.setSpacing(UNIT)
        num = QLabel(f"{number}")
        num.setFont(theme.body_strong(10))
        num.setStyleSheet(f"color: {theme.palette.accent};")
        head.addWidget(num)
        head.addWidget(SectionLabel(label, theme))
        head.addStretch(1)
        lay.addLayout(head)
        lay.addWidget(control)
        if hint:
            lay.addWidget(_muted(hint, theme, 8))
        lay.addSpacing(UNIT)
        lay.addWidget(Rule(theme))


class SetupScreen(QWidget):
    """Folder → profile → optional master index → output folder → Run."""

    run_requested = Signal(object)  # RunRequest
    template_review_requested = Signal()
    source_chosen = Signal(str)
    folders_changed = Signal(str, str)
    """``(source, output)`` — either folder was picked or cleared.

    The window asks the pipeline whether the pair is usable and pushes the answer
    back through :meth:`set_folder_warning`. The screen does not decide: the rule
    belongs to the pipeline, and a screen holding its own copy of it is a second
    definition free to disagree."""

    def __init__(self, theme: Theme,
                 parent=None) -> None:
        super().__init__(parent)
        self._theme = theme
        page, lay = _page(theme)

        title = QLabel("Reduce a matter folder to a text corpus")
        title.setFont(theme.title(16))
        title.setStyleSheet(f"color: {theme.palette.navy};")
        lay.addWidget(title)
        lay.addWidget(_muted(
            "DocIQ reads every document in a folder and writes one plain-text "
            "file per document, with the original page numbers kept. Nothing is "
            "removed unless a profile you approved says so.", theme))
        lay.addSpacing(UNIT * 2)

        self._source = QLineEdit()
        self._source.setPlaceholderText("Choose the folder holding the documents…")
        self._source.setReadOnly(True)
        self._source.setFont(theme.body(10))
        src_row = QHBoxLayout()
        src_row.setContentsMargins(0, 0, 0, 0)
        src_row.addWidget(self._source, 1)
        browse = _button("Browse…", theme)
        browse.clicked.connect(self._pick_source)
        src_row.addWidget(browse)
        # The folder preview belongs INSIDE step 1, above its rule: below the
        # rule it reads as a caption on step 2, which is where it first landed.
        src_holder = QWidget()
        src_box = QVBoxLayout(src_holder)
        src_box.setContentsMargins(0, 0, 0, 0)
        src_box.setSpacing(UNIT // 2)
        src_inner = QWidget()
        src_inner.setLayout(src_row)
        src_box.addWidget(src_inner)
        self._source_hint = _muted("", theme, 8)
        src_box.addWidget(self._source_hint)
        lay.addWidget(_Step(1, "Documents folder", theme, src_holder))

        # Step 2 was "Format profile": a combo of expert-authored YAML
        # profiles, a "Profile new format…" link, and a review link. D-38
        # deleted the profile system, so there is nothing to choose between —
        # DocIQ recognizes sections from the document's own structure and offers
        # a fixed set of section types from the shipped template.
        #
        # The REVIEW survives, and must: §6 requires that nothing be dropped
        # that the expert was not shown, and that requirement never belonged to
        # profiles. It stays a LINK rather than a button so the screen keeps
        # exactly one forward action (D-16).
        prof_holder = QWidget()
        prof_box = QVBoxLayout(prof_holder)
        prof_box.setContentsMargins(0, 0, 0, 0)
        prof_box.setSpacing(UNIT // 2)
        self._review = _button("See what DocIQ recognizes, and what it can "
                               "leave out…", theme, "link")
        self._review.clicked.connect(self._emit_review)
        prof_box.addWidget(self._review, alignment=Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(_Step(
            2, "What can be left out", theme, prof_holder,
            "DocIQ recognizes section types from each document's own structure. "
            "Nothing is left out unless you approve it after the run, and your "
            "name is recorded against it."))

        self._index = QLineEdit()
        self._index.setPlaceholderText("Optional — LI master document index (.xlsx / .csv)")
        self._index.setReadOnly(True)
        self._index.setFont(theme.body(10))
        idx_row = QHBoxLayout()
        idx_row.setContentsMargins(0, 0, 0, 0)
        idx_row.addWidget(self._index, 1)
        idx_browse = _button("Browse…", theme)
        idx_browse.clicked.connect(self._pick_index)
        idx_row.addWidget(idx_browse)
        idx_clear = _button("Clear", theme)
        idx_clear.clicked.connect(lambda: self._index.setText(""))
        idx_row.addWidget(idx_clear)
        idx_holder = QWidget()
        idx_holder.setLayout(idx_row)
        lay.addWidget(_Step(
            3, "Master index (optional)", theme, idx_holder,
            "Supply it and documents take their LI File No. as their ID, and "
            "you get a completeness check against the index."))

        self._output = QLineEdit()
        self._output.setPlaceholderText("Where the outputs should be written…")
        self._output.setReadOnly(True)
        self._output.setFont(theme.body(10))
        out_row = QHBoxLayout()
        out_row.setContentsMargins(0, 0, 0, 0)
        out_row.addWidget(self._output, 1)
        out_browse = _button("Browse…", theme)
        out_browse.clicked.connect(self._pick_output)
        out_row.addWidget(out_browse)
        out_holder = QWidget()
        out_holder.setLayout(out_row)
        lay.addWidget(_Step(4, "Output folder", theme, out_holder))

        lay.addStretch(1)
        # ONE forward action, named for the outcome rather than the mechanism:
        # "Run" says what the software does; "Build the reduced corpus" says
        # what the operator gets. The scope and the time sit beside it so
        # starting a 28-minute job is never a surprise, and the reassurance sits
        # with the action because that is the moment it is needed.
        foot = QHBoxLayout()
        foot.setSpacing(UNIT * 2)
        left = QVBoxLayout()
        left.setSpacing(2)
        self._blocker = _muted("Choose a documents folder and an output folder "
                               "to start.", theme, 9)
        left.addWidget(self._blocker)
        self._reassure = _muted(
            "Nothing is deleted — every page is accounted for, and the pages a "
            "profile leaves out are listed in the log with the rule that left "
            "them out.", theme, 9)
        left.addWidget(self._reassure)
        foot.addLayout(left, 1)

        right = QVBoxLayout()
        right.setSpacing(UNIT // 2)
        self._scope = _muted("", theme, 9)
        self._scope.setAlignment(Qt.AlignmentFlag.AlignRight)
        right.addWidget(self._scope)
        self._run = _button("Build the reduced corpus", theme, "primary")
        self._run.setFont(theme.body_strong(10))
        self._run.setEnabled(False)
        self._run.clicked.connect(self._emit_run)
        right.addWidget(self._run)
        foot.addLayout(right)
        lay.addLayout(foot)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        outer.addWidget(scroll)

    # -- state --------------------------------------------------------------

    def set_paths(self, source: str = "", output: str = "",
                  master_index: str = "") -> None:
        """Set the fields directly. Used by the render harness and by tests —
        a file dialog cannot be driven offscreen."""
        if source:
            self._source.setText(source)
            self.source_chosen.emit(source)
            self.folders_changed.emit(self._source.text(), self._output.text())
        if output:
            self._output.setText(output)
        if master_index:
            self._index.setText(master_index)
        self._refresh()

    def set_preview(self, preview: FolderPreview) -> None:
        exts = ", ".join(f"{n} {ext}" for ext, n in preview.by_extension)
        self._source_hint.setText(
            f"{preview.file_count} files · {preview.total_bytes / 1e9:.1f} GB · {exts}"
        )
        scope = f"{preview.file_count:,} documents"
        if preview.estimated_minutes:
            scope += f" · about {preview.estimated_minutes} minutes"
        self._scope.setText(scope)

    def request(self) -> RunRequest:
        return RunRequest(
            source_root=self._source.text(),
            output_root=self._output.text(),
            master_index_path=self._index.text() or None,
        )

    def set_folder_warning(self, message: str) -> None:
        """The pipeline's answer about this pair of folders, shown where they
        were chosen (D-43's first finding).

        The warning does not merely warn: it DISABLES the forward action. A
        refusal the operator can click past is not a preflight, and the run would
        refuse a moment later anyway — which is the experience this removes.
        """
        self._folder_warning = message
        self._refresh()

    def _refresh(self) -> None:
        chosen = bool(self._source.text() and self._output.text())
        warning = getattr(self, "_folder_warning", "")
        ready = chosen and not warning
        self._run.setEnabled(ready)
        if warning:
            self._blocker.setText(warning)
            self._blocker.setStyleSheet(f"color: {self._theme.palette.warn};")
        else:
            self._blocker.setStyleSheet("")
            self._blocker.setText(
                "" if ready
                else "Choose a documents folder and an output folder to start."
            )

    def _emit_run(self) -> None:
        self.run_requested.emit(self.request())

    def _emit_review(self) -> None:
        self.template_review_requested.emit()

    # -- pickers ------------------------------------------------------------

    def _pick_source(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose the documents folder")
        if path:
            self._source.setText(path)
            self.source_chosen.emit(path)
            self._refresh()
        self.folders_changed.emit(
            self._source.text(), self._output.text())

    def _pick_output(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose the output folder")
        if path:
            self._output.setText(path)
            self._refresh()
        self.folders_changed.emit(
            self._source.text(), self._output.text())

    def _pick_index(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose the master document index", "",
            "Index files (*.xlsx *.xls *.csv)")
        if path:
            self._index.setText(path)


class ProgressScreen(QWidget):
    """Per-document status while a run is in flight — and when it has stopped.

    **A settled run is a different screen from a running one** (Codex review #2,
    A-2). It used to be the same one: a failed run appended a flagged row saying
    "Run failed" and the worker thread quit, leaving Cancel as the only control
    on screen — a button whose entire job was to stop a thread that had already
    stopped. There was no back, no retry, and no new run, so the only recovery
    from an ordinary exception (an unreadable stored Bates pattern, a full disk,
    an emit error) was closing and reopening DocIQ.

    So the screen has two states. While a run is in flight, Cancel is live and
    it is the only action. Once the run has SETTLED — completed, failed, or
    cancelled — Cancel is disabled, because a control that cannot do what it
    says is worse than no control, and on failure the error is preserved in full
    beside two actions that work.
    """

    cancel_requested = Signal()
    back_requested = Signal()
    retry_requested = Signal()

    def __init__(self, theme: Theme, parent=None) -> None:
        super().__init__(parent)
        self._theme = theme
        page, lay = _page(theme)

        self._title = QLabel("Reading the folder…")
        self._title.setFont(theme.title(16))
        self._title.setStyleSheet(f"color: {theme.palette.navy};")
        lay.addWidget(self._title)
        self._count = _muted("", theme, 9)
        lay.addWidget(self._count)

        self._bar = QProgressBar()
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(UNIT)
        self._bar.setRange(0, 100)
        lay.addWidget(self._bar)
        lay.addSpacing(UNIT * 2)

        lay.addWidget(SectionLabel("Documents", theme))
        lay.addWidget(Rule(theme, strong=True))

        self._rows = QWidget()
        self._rows_lay = QVBoxLayout(self._rows)
        self._rows_lay.setContentsMargins(0, 0, 0, 0)
        self._rows_lay.setSpacing(0)
        self._rows_lay.addStretch(1)
        self._scroll = QScrollArea()
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidgetResizable(True)
        self._scroll.setWidget(self._rows)
        self._scroll.setSizePolicy(QSizePolicy.Policy.Expanding,
                                   QSizePolicy.Policy.Expanding)
        lay.addWidget(self._scroll, 1)

        # The failure banner sits between the list and the actions, so the
        # reason is read on the way to the button. Cleared and hidden while a
        # run is in flight.
        self._error = QLabel("")
        self._error.setFont(theme.body(10))
        self._error.setWordWrap(True)
        self._error.setStyleSheet(f"color: {theme.palette.warn};")
        self._error.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self._error)

        foot = QHBoxLayout()
        foot.addStretch(1)
        self._back = _button("← Back to setup", theme)
        self._back.clicked.connect(self.back_requested.emit)
        foot.addWidget(self._back)
        self._retry = _button("Try this run again", theme, "primary")
        self._retry.clicked.connect(self.retry_requested.emit)
        foot.addWidget(self._retry)
        self._cancel = _button("Cancel", theme)
        self._cancel.clicked.connect(self.cancel_requested.emit)
        foot.addWidget(self._cancel)
        lay.addLayout(foot)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(page)

        self._settled = False
        self.reset()

    # -- states ---------------------------------------------------------------

    def reset(self) -> None:
        """Back to the in-flight state. Called before every run, including a
        RETRY — a second attempt that still showed the first one's error would
        be reporting a failure that has not happened yet."""
        clear_layout(self._rows_lay, keep_trailing=1)  # keep the stretch
        self._bar.setValue(0)
        self._count.setText("")
        self._title.setText("Reading the folder…")
        self._error.setText("")
        self._error.setVisible(False)
        self._settled = False
        self._cancel.setEnabled(True)
        for button in (self._back, self._retry):
            button.setVisible(False)

    def settle(self) -> None:
        """The worker thread has stopped, however it stopped.

        Cancel is disabled here rather than hidden: the operator pressed a
        button a moment ago and a control that disappears reads as a screen that
        moved on without them. Disabled, it still says what it was for.
        """
        self._settled = True
        self._cancel.setEnabled(False)

    def fail(self, message: str) -> None:
        """A settled run that did NOT produce deliverables.

        The pipeline's own text is shown verbatim and in full. It is the only
        specific thing the operator has, and it is what they will paste into a
        message to whoever maintains this.
        """
        self._settled_state(
            "This run failed",
            (message.strip() or "The pipeline reported no reason.")
            + "\n\nNo deliverables were written for this run. Any files in the "
              "output folder are from an EARLIER run — check their dates before "
              "relying on them.",
        )

    def stopped(self, reason: str) -> None:
        """A settled run the OPERATOR ended. Not a failure, and not worded as
        one — the screen must not tell someone their deliberate act went wrong —
        but it is the same dead end if it offers no way out, so it settles and
        offers the same two actions."""
        self._settled_state(
            "This run was stopped",
            (reason.strip() or "The run was stopped before it finished.")
            + "\n\nNothing was published. The output folder holds whatever an "
              "earlier run left there.",
        )

    def _settled_state(self, title: str, message: str) -> None:
        self.settle()
        self._title.setText(title)
        self._error.setText(message)
        self._error.setVisible(True)
        for button in (self._back, self._retry):
            button.setVisible(True)

    # -- what a test reads ---------------------------------------------------

    def failed(self) -> bool:
        return self._error.text() != ""

    def error_text(self) -> str:
        return self._error.text()

    def title_text(self) -> str:
        return self._title.text()

    def cancel_enabled(self) -> bool:
        return self._cancel.isEnabled()

    def recovery_offered(self) -> bool:
        """Whether the screen offers a way out that actually does something.

        ``isHidden`` and not ``isVisible``: under the offscreen platform no
        top-level window is shown, so ``isVisible()`` is False for every widget
        on every screen. ``isHidden()`` reports the explicit hide these buttons
        are controlled by.
        """
        return not self._back.isHidden() and not self._retry.isHidden()

    def append(self, event: ProgressEvent) -> None:
        self._title.setText("Processing documents")
        self._bar.setValue(int(100 * event.done / max(1, event.total)))
        self._count.setText(f"{event.done} of {event.total} files")

        row = QWidget()
        row_lay = QHBoxLayout(row)
        row_lay.setContentsMargins(0, UNIT, 0, UNIT)
        name = QLabel(event.filename)
        name.setFont(self._theme.body(10))
        # Wrapped, so the label's MINIMUM width is not its full text width. An
        # unwrapped label inside a scroll area sets the scrolled widget's
        # minimum, and one long path then makes the whole list scroll sideways
        # — the class of defect the state grid caught on three screens at once.
        name.setWordWrap(True)
        status = QLabel(event.status)
        status.setFont(self._theme.body(9))
        # Wrapped for the same reason the filename is, and found by the same
        # probe: an unwrapped status sets the scrolled widget's minimum width,
        # and the failure row's status is the pipeline's exception text — the
        # longest string this list can ever hold. Adding the failed state to the
        # screen-state grid made the whole screen scroll sideways at the
        # product's minimum window.
        status.setWordWrap(True)
        status.setStyleSheet(
            f"color: {self._theme.palette.warn if event.flagged else self._theme.palette.ink_muted};"
        )
        row_lay.addWidget(name, 1)
        row_lay.addWidget(status)
        self._rows_lay.insertWidget(self._rows_lay.count() - 1, row)
        self._rows_lay.insertWidget(self._rows_lay.count() - 1, Rule(self._theme))
        bar = self._scroll.verticalScrollBar()
        bar.setValue(bar.maximum())


class SummaryScreen(QWidget):
    """The token headline, the capacity gauge, the accounting, and the chips."""

    flag_selected = Signal(str)
    new_run_requested = Signal()
    open_output_requested = Signal()
    handoff_requested = Signal()
    plan_changed = Signal(object)  # ReductionPlan
    lever_engaged = Signal(str, bool)
    """``(family_id, engaged)`` — a row the MODEL actually moved (D-34).

    Emitted only after :meth:`ReductionPlan.with_toggled` has accepted the
    change, so a locked row never reaches the window and never reaches the
    approver capture. The window turns it into an approval; this screen does
    not, because it has neither the pipeline nor the matter."""

    def __init__(self, theme: Theme, parent=None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._view: SummaryView | None = None
        self._plan = None
        page, lay = _page(theme)
        self._lay = lay

        # The run-status banner sits above everything, including the headline
        # (Codex review #1, finding B-1). A blocked or cancelled run's figures
        # describe part of a corpus, and the output folder still holds the
        # PREVIOUS run's deliverables — the operator has to be told both before
        # they read a single number. Empty and hidden on a completed run.
        self._status = QLabel("")
        self._status.setFont(theme.body(10))
        self._status.setWordWrap(True)
        self._status.setStyleSheet(f"color: {theme.palette.warn};")
        self._status.setVisible(False)
        lay.addWidget(self._status)

        lay.addWidget(SectionLabel("Tokens before and after reduction", theme))
        self._headline = QLabel("—")
        self._headline.setFont(theme.headline(40))
        self._headline.setStyleSheet(f"color: {theme.palette.navy};")
        self._unit = _muted("", theme, 11)
        head_row = QHBoxLayout()
        head_row.setSpacing(UNIT * 2)
        head_row.setAlignment(Qt.AlignmentFlag.AlignBottom)
        head_row.addWidget(self._headline)
        head_row.addWidget(self._unit)
        head_row.addStretch(1)
        lay.addLayout(head_row)

        # Two lines, not one: the capacity comparison is a short mono figure and
        # the provenance is a sentence. Set as one unwrapped mono label they ran
        # off the window and forced the whole page wider than the viewport,
        # clipping the footer buttons — at the measured record's scale the
        # provenance sentence is long enough to break the layout on its own.
        self._capacity_line = QLabel("")
        self._capacity_line.setFont(theme.mono(8))
        self._capacity_line.setStyleSheet(f"color: {theme.palette.navy};")
        self._capacity_line.setWordWrap(True)
        lay.addWidget(self._capacity_line)
        self._basis = _muted("", theme, 9)
        self._basis.setWordWrap(True)
        lay.addWidget(self._basis)
        lay.addSpacing(UNIT)

        # The waterfall IS the section picker (ruling, 2026-07-30): clicking a
        # row toggles that section and the stack re-flows. There is deliberately
        # no checklist beside it — two controls for one decision is the thing
        # that made the earlier layout unintuitive.
        self._waterfall = ReductionWaterfall(theme)
        self._waterfall.lever_toggled.connect(self._toggle_lever)
        lay.addWidget(self._waterfall)

        # The two totals, kept apart on screen as well as in the model. D-14
        # forbids merging them; a single "reduced by X" line under the stack
        # would merge them in the reader's head no matter what the rows said,
        # because it is the only figure they would carry away.
        self._split = QLabel("")
        self._split.setFont(theme.body_strong(9))
        self._split.setWordWrap(True)
        self._split.setStyleSheet(f"color: {theme.palette.navy};")
        lay.addWidget(self._split)
        # D-21: what was dropped, named, so the expert can describe the
        # omission rather than a figure they reduced to.
        self._drops = _muted("", theme, 9)
        lay.addWidget(self._drops)
        self._capacity_source = _muted("", theme, 8)
        lay.addWidget(self._capacity_source)
        self._route = _muted("", theme, 10)
        lay.addWidget(self._route)
        self._stale = _muted("", theme, 9)
        self._stale.setStyleSheet(f"color: {theme.palette.warn};")
        lay.addWidget(self._stale)
        lay.addSpacing(UNIT)
        lay.addWidget(Rule(theme, strong=True))

        # Labelled explicitly, because the waterfall above it can be moved after
        # the run: these figures are the run's OWN accounting, read from the
        # contract and never recomputed here, and they do not follow a toggle.
        lay.addWidget(SectionLabel("The run, as written to disk", theme))
        self._stats = QHBoxLayout()
        self._stats.setSpacing(UNIT * 6)
        lay.addLayout(self._stats)
        lay.addWidget(Rule(theme))

        self._flags_label = SectionLabel("Needs your attention", theme)
        lay.addWidget(self._flags_label)
        self._chips = QHBoxLayout()
        self._chips.setSpacing(UNIT)
        self._chips.addStretch(1)
        lay.addLayout(self._chips)

        lay.addStretch(1)
        self._regime = _muted("", theme, 9)
        lay.addWidget(self._regime)
        self._output = _muted("", theme, 9)
        lay.addWidget(self._output)

        foot = QHBoxLayout()
        again = _button("Start another run", theme)
        again.clicked.connect(self.new_run_requested.emit)
        foot.addWidget(again)
        foot.addStretch(1)
        self._open = _button("Open the output folder", theme)
        self._open.clicked.connect(self.open_output_requested.emit)
        foot.addWidget(self._open)
        # The ONE forward action on this screen (D-16). "Start another run" and
        # "Open the output folder" are secondary by weight and by object name;
        # the outcome this screen leads to is the analysis, and §8 makes that a
        # single button that then offers the two sanctioned routes.
        self._claude = _button("Analyze in Claude", theme, "primary")
        self._claude.setEnabled(False)
        self._claude.clicked.connect(self.handoff_requested.emit)
        foot.addWidget(self._claude)
        lay.addLayout(foot)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        outer.addWidget(scroll)

    def show_summary(self, view: SummaryView) -> None:
        self._view = view
        self._plan = view.plan
        self._paint_headline(view)
        self._waterfall.set_plan(view.plan)
        self._stale.setText("")
        banner = view.status_banner()
        self._status.setText(banner)
        self._status.setVisible(bool(banner))

        clear_layout(self._stats)
        for value_, label, tone in (
            (f"{view.documents:,}", "documents", "ink"),
            (f"{view.pages_in:,}", "pages read", "ink"),
            (f"{view.pages_kept:,}", "pages kept", "ink"),
            (f"{view.pages_dropped:,}", "pages left out", "muted"),
            (f"{view.unsupported:,}", "listed only", "muted"),
        ):
            self._stats.addWidget(StatFigure(value_, label, self._theme, tone))
        self._stats.addStretch(1)

        clear_layout(self._chips)
        for group in view.flags:
            chip = Chip(group.key, group.label, group.count, self._theme)
            chip.clicked.connect(self.flag_selected.emit)
            self._chips.addWidget(chip)
        if not view.flags:
            self._chips.addWidget(_muted("Nothing flagged.", self._theme, 10))
        self._chips.addStretch(1)

        self._regime.setText(view.id_regime_note)
        if not view.published:
            # Never "Outputs written to ..." for a run that wrote none. That
            # line beside an unchanged folder is the specific false statement
            # finding B-1 is about.
            self._output.setText(
                f"No outputs were written. {view.output_root} still holds the "
                "last completed run's deliverables."
                if view.output_root else "No outputs were written.")
        else:
            self._output.setText(f"Outputs written to {view.output_root}"
                                 if view.output_root else "")
        self._open.setEnabled(bool(view.output_root)
                              and Path(view.output_root).is_dir())
        # The handoff screen describes routes to a corpus ON DISK. A run that
        # published nothing has no such corpus, and offering to hand it over
        # would point Claude at the previous run's deliverables (finding B-1).
        self._claude.setEnabled(view.published and bool(view.output_root))
        self._claude.setToolTip(
            "" if (view.published and view.output_root)
            else "This run wrote no deliverables, so there is nothing to hand over."
        )

    def _paint_headline(self, view: SummaryView) -> None:
        self._headline.setText(view.headline())
        self._unit.setText(view.headline_unit())
        self._capacity_line.setText(view.capacity_line())
        self._basis.setText(view.basis_note())
        self._split.setText(view.split_line())
        self._drops.setText(view.drops_line())
        self._capacity_source.setText(view.capacity_source_line())
        self._route.setText(view.route_line())

    def _toggle_lever(self, key: str) -> None:
        """A row was clicked. The MODEL decides whether it may move.

        ``with_toggled`` ignores a locked row, so a recognized-and-never-offered
        section cannot be engaged even if a future widget wires a click to it.
        The screen then reports what the model did rather than what the click
        asked for — which is why the emitted plan is read back out instead of
        assumed.

        The approver is captured by the window, not here: this screen has no
        pipeline and no matter name, and a screen that could compose an approver
        is a screen that could compose a fiction (D-34).
        """
        if self._plan is None:
            return
        before = {lever.key: lever.engaged for lever in self._plan.levers}
        plan = self._plan.with_toggled(key)
        moved = [lever for lever in plan.levers
                 if before.get(lever.key) != lever.engaged]
        if not moved:
            return
        self._plan = plan
        self.lever_engaged.emit(moved[0].family_id, moved[0].engaged)
        self.plan_changed.emit(self._plan)

    def mark_stale(self) -> None:
        """Say that the files on disk no longer match what is on screen.

        Toggling a section changes the estimate, not the output — pretending
        otherwise would be the worst kind of quiet: the operator would hand over
        a corpus that does not match the picture they approved.
        """
        self._stale.setText(
            "These choices have not been written yet — the figures and files "
            "below are the run as it stands. Rebuild the corpus to apply them."
        )


class DetailScreen(QWidget):
    """What is behind a chip: the explanation, then every flagged item."""

    back_requested = Signal()

    def __init__(self, theme: Theme, parent=None) -> None:
        super().__init__(parent)
        self._theme = theme
        page, lay = _page(theme)
        self._lay = lay

        back = _button("← Back to the summary", theme, "link")
        back.setFont(theme.body(10))
        back.clicked.connect(self.back_requested.emit)
        lay.addWidget(back, alignment=Qt.AlignmentFlag.AlignLeft)

        self._title = QLabel("")
        self._title.setFont(theme.title(16))
        self._title.setStyleSheet(f"color: {theme.palette.navy};")
        lay.addWidget(self._title)
        self._explain = _muted("", theme, 10)
        lay.addWidget(self._explain)
        lay.addSpacing(UNIT)
        lay.addWidget(Rule(theme, strong=True))

        self._items = QWidget()
        self._items_lay = QVBoxLayout(self._items)
        self._items_lay.setContentsMargins(0, 0, 0, 0)
        self._items_lay.setSpacing(0)
        self._items_lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._items)
        lay.addWidget(scroll, 1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(page)

    def show_group(self, group: FlagGroup) -> None:
        self._title.setText(f"{group.label} — {group.count:,}")
        self._explain.setText(group.explanation)
        clear_layout(self._items_lay, keep_trailing=1)  # keep the stretch
        for it in group.items:
            row = QWidget()
            row_lay = QVBoxLayout(row)
            row_lay.setContentsMargins(0, UNIT * 1.5, 0, UNIT * 1.5)
            row_lay.setSpacing(2)
            head = QHBoxLayout()
            primary = QLabel(it.primary)
            primary.setFont(self._theme.body_strong(10))
            primary.setWordWrap(True)  # see ProgressScreen.append
            head.addWidget(primary, 1)
            if it.locator:
                loc = QLabel(it.locator)
                loc.setFont(self._theme.mono_plain(8))
                loc.setWordWrap(True)
                loc.setStyleSheet(f"color: {self._theme.palette.ink_muted};")
                head.addWidget(loc)
            row_lay.addLayout(head)
            row_lay.addWidget(_muted(it.secondary, self._theme, 9))
            self._items_lay.insertWidget(self._items_lay.count() - 1, row)
            self._items_lay.insertWidget(self._items_lay.count() - 1,
                                         Rule(self._theme))


DISPOSITION_WORDS = ("DROP", "KEEP", "AUTOMATIC", "KEPT")
"""Every value :meth:`ChecklistRow.disposition_word` can return.

Enumerated so the column that holds them can be sized from the widest one. A
test asserts this tuple is exhaustive — otherwise adding a fourth word would
reintroduce the clipping this exists to prevent, silently.

"KEPT" is A-20's ``LEVER_RECOGNIZED`` row: a section the template names and
never offers. It arrived here late — the word existed in
:data:`dociq.gui.view_models._LOCKED_DISPOSITION` only after that kind stopped
being rendered as "AUTOMATIC" — and it is narrower than "AUTOMATIC", so the
column width does not move.
"""


def _disposition_column_width(theme: Theme) -> int:
    metrics = QFontMetrics(theme.label(9))
    return max(metrics.horizontalAdvance(word)
               for word in DISPOSITION_WORDS) + UNIT * 2


class ProfileChecklistScreen(QWidget):
    """§6 step 2/3 — what this profile KEEPs and DROPs, before a run commits.

    **Nothing may be dropped that this screen did not show.** That is not a
    slogan: Principle 3 makes an unapproved omission indistinguishable from a
    missing document, so an omission the expert never saw is, downstream, a
    document that vanished. The screen therefore enumerates every rule, states
    what each one is worth, attributes each one to the rule that carries it,
    and — when the rule count it was given disagrees with the count the profile
    declares — refuses to pretend the list is complete.

    Exactly one forward action (D-16): "Use this profile".
    """

    back_requested = Signal()
    profile_accepted = Signal()

    def __init__(self, theme: Theme, parent=None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._view: ProfileChecklistView | None = None
        page, lay = _page(theme)

        back = _button("← Back", theme, "link")
        back.clicked.connect(self.back_requested.emit)
        lay.addWidget(back, alignment=Qt.AlignmentFlag.AlignLeft)

        self._title = QLabel("")
        self._title.setFont(theme.title(16))
        self._title.setStyleSheet(f"color: {theme.palette.navy};")
        lay.addWidget(self._title)
        self._subtitle = _muted("", theme, 9)
        lay.addWidget(self._subtitle)
        self._source = _muted("", theme, 9)
        lay.addWidget(self._source)
        lay.addSpacing(UNIT)

        # NOT "Sections this profile decides" (D-35). A profile decides nothing:
        # these rows are the section template's families, identical for
        # every profile including "no profile".
        lay.addWidget(SectionLabel("Section types DocIQ recognizes", theme))
        lay.addWidget(Rule(theme, strong=True))
        self._rows = QWidget()
        self._rows_lay = QVBoxLayout(self._rows)
        self._rows_lay.setContentsMargins(0, 0, 0, 0)
        self._rows_lay.setSpacing(0)
        self._rows_lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._rows)
        lay.addWidget(scroll, 1)

        # The completeness claim, in the warn color when it is a warning. It
        # sits directly under the list and directly above the action, because
        # it is the last thing read before the profile is accepted.
        self._completeness = QLabel("")
        self._completeness.setFont(theme.body(9))
        self._completeness.setWordWrap(True)
        lay.addWidget(self._completeness)
        lay.addWidget(Rule(theme))

        self._drops = QLabel("")
        self._drops.setFont(theme.body_strong(10))
        self._drops.setWordWrap(True)
        self._drops.setStyleSheet(f"color: {theme.palette.navy};")
        lay.addWidget(self._drops)
        self._automatic = _muted("", theme, 9)
        lay.addWidget(self._automatic)
        self._basis = _muted("", theme, 8)
        lay.addWidget(self._basis)

        foot = QHBoxLayout()
        foot.addStretch(1)
        self._accept = _button("Use this profile", theme, "primary")
        self._accept.setFont(theme.body_strong(10))
        self._accept.clicked.connect(self._emit_accept)
        foot.addWidget(self._accept)
        lay.addLayout(foot)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(page)

    def show_checklist(self, view: ProfileChecklistView) -> None:
        self._view = view
        self._title.setText("Section types DocIQ recognizes")
        offered = len(view.expert_rows)
        kept = len(view.recognized_rows)
        self._subtitle.setText(
            f"{len(view.rows)} recognized · {offered} can be left out · "
            f"{kept} never offered"
        )
        self._source.setText(
            view.source
            or "The pipeline did not say where these families came from."
        )

        clear_layout(self._rows_lay, keep_trailing=1)  # keep the stretch
        for row in view.rows:
            self._rows_lay.insertWidget(self._rows_lay.count() - 1,
                                        self._row_widget(row))
            self._rows_lay.insertWidget(self._rows_lay.count() - 1,
                                        Rule(self._theme))
        if view.empty:
            self._rows_lay.insertWidget(
                self._rows_lay.count() - 1,
                _muted("No section rules to show.", self._theme, 10))

        note = view.completeness_note()
        self._completeness.setText(note)
        alarming = not view.approvable
        tone = (self._theme.palette.warn if alarming
                else self._theme.palette.ink_muted)
        self._completeness.setStyleSheet(f"color: {tone};")
        self._drops.setText(view.drop_summary())
        self._automatic.setText(view.automatic_summary())
        self._basis.setText(view.basis_note())
        # A profile whose rule list cannot be shown in full must not be
        # accepted from this screen. Refusing is the only honest option: the
        # button's whole meaning is "I have seen what this drops".
        self._accept.setEnabled(not alarming)
        self._accept.setToolTip(
            "" if not alarming
            else "This profile's rules cannot be shown in full, so they "
                 "cannot be approved here."
        )

    def _row_widget(self, row: ChecklistRow) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, UNIT * 1.5, 0, UNIT * 1.5)
        lay.setSpacing(2)

        head = QHBoxLayout()
        head.setSpacing(UNIT * 2)
        # The disposition as a WORD, not only as a color or a checkbox glyph:
        # the sheet is printed and forwarded, and a monochrome print of a
        # colored tick is a blank.
        mark = QLabel(row.disposition_word())
        mark.setFont(self._theme.label(9))
        # Sized from the WIDEST word this column can ever hold, not from a
        # guessed multiple of the grid unit: a hand-picked 64 px clipped
        # "AUTOMATIC" to "AUTOMAT", which is a truncation the screen performed
        # and did not say it had performed. The column still aligns across
        # rows because every row is measured against the same maximum.
        mark.setFixedWidth(_disposition_column_width(self._theme))
        mark.setStyleSheet(
            f"color: {self._theme.palette.accent if row.expert_drop else self._theme.palette.ink_muted};"
        )
        head.addWidget(mark)
        label = QLabel(row.lever.label)
        label.setFont(self._theme.body_strong(10))
        label.setWordWrap(True)
        head.addWidget(label, 1)
        scale = QLabel(row.scale())
        scale.setFont(self._theme.mono_plain(8))
        scale.setStyleSheet(f"color: {self._theme.palette.ink_muted};")
        head.addWidget(scale)
        lay.addLayout(head)
        lay.addWidget(_muted(row.attribution(), self._theme, 9))
        # A-11b's pattern and the expert's own note, rendered at last. The
        # fields have been on the seam since A-11b and reached no screen and no
        # adapter until Codex review #2's seam-population probe; the checklist
        # could say a DROP rule existed and not what it catches or who approved
        # it — "Rule X → section Y → DROP" and nothing more.
        matched = row.matched_by()
        if matched:
            pattern = QLabel(matched)
            pattern.setFont(self._theme.mono_plain(8))
            pattern.setWordWrap(True)
            pattern.setStyleSheet(f"color: {self._theme.palette.ink_muted};")
            lay.addWidget(pattern)
        note = row.expert_note()
        if note:
            reason = QLabel(note)
            reason.setFont(self._theme.body(9))
            reason.setWordWrap(True)
            reason.setStyleSheet(f"color: {self._theme.palette.ink};")
            lay.addWidget(reason)
        return w

    def _emit_accept(self) -> None:
        if self._view is not None:
            self.profile_accepted.emit(self._view.profile)


class BatesConfirmScreen(QWidget):
    """§4 Stage 3 — the detected Bates format, put to the operator.

    **This screen is the finding.** Sprint 2 shipped without it, so the format
    never reached CONFIRMED, so a Bates-stamped production came out of DocIQ
    with no locators at all — while the acceptance harness PROJECTED 92.130%
    through a decision built in Python that the product could not produce
    (rehearsal A4). *(This said "measured" until 2026-08-18. D-29 rules the
    figure a projection — 568 native + 29 OCR pages, arithmetic over two runs,
    not one measurement — and says in terms never to quote it flat. The measured
    end-to-end figure is 91.512%.)* Everything below exists because the pipeline is BLOCKED on
    this screen: a run is standing still on a worker thread until one of three
    buttons is pressed.

    What it shows is chosen so an operator can actually rule on it. A regex is
    not confirmable and neither is a percentage on its own; a locator the
    operator recognizes from the production, next to how much of the record it
    covers, is. So the example leads, in the mono face, at size — it is the
    evidence, not a caption on the pattern.

    **Multi-series productions are named, not decided.** When
    :attr:`~dociq.gui.pipeline.BatesProposal.alternatives` is non-empty, D-28
    refuses prefix repair on this matter, and the operator is the one who has to
    know that: confirming one series here means the others keep whatever their
    pages read. The block says so in those words rather than leaving the
    operator to infer it from a list.

    That field must be **D-28's own census** and nothing else — the adapter
    fills it from ``identify.bates.matter_prefixes``, not from the detector's
    runner-up shapes, which carry no threshold and on a real single-series
    production include stray lines like ``Check 0001``. This screen states the
    D-28 consequence as fact, so a looser source would make it a false statement
    about the record at the exact moment the operator is asked to rule.

    One forward action (D-16), named for its outcome: "Use this Bates format".
    The other two are not variants of it — declining is a ruling the run
    records, and stopping is not a ruling at all.
    """

    confirmed = Signal()
    declined = Signal()
    stop_requested = Signal()

    def __init__(self, theme: Theme, parent=None) -> None:
        super().__init__(parent)
        self._theme = theme
        page, lay = _page(theme)

        title = QLabel("Confirm this document set's Bates format")
        title.setFont(theme.title(16))
        title.setStyleSheet(f"color: {theme.palette.navy};")
        lay.addWidget(title)
        lay.addWidget(_muted(
            "The run is paused here. DocIQ read a stamp format off the pages "
            "and will not write a single locator until you say it is this "
            "production's. You are asked once per document set.", theme, 10))
        lay.addSpacing(UNIT)

        lay.addWidget(SectionLabel("A locator read off a page", theme))
        lay.addWidget(Rule(theme, strong=True))
        self._example = QLabel("")
        self._example.setFont(theme.mono(20))
        self._example.setStyleSheet(f"color: {theme.palette.navy};")
        self._example.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self._example)
        self._pattern = _muted("", theme, 9)
        lay.addWidget(self._pattern)
        lay.addSpacing(UNIT)

        figures = QHBoxLayout()
        figures.setSpacing(UNIT * 6)
        self._documents = StatFigure("0", "Documents", theme)
        self._pages = StatFigure("0", "Pages stamped", theme)
        self._coverage = StatFigure("0%", "Of pages sampled", theme)
        for fig in (self._documents, self._pages, self._coverage):
            figures.addWidget(fig)
        figures.addStretch(1)
        lay.addLayout(figures)
        lay.addSpacing(UNIT)

        # The multi-series disclosure. Word-wrapped, in the warn color when it
        # fires, directly above the actions — it is the last thing read before
        # a format is confirmed, because it changes what confirming means.
        self._alternatives = QLabel("")
        self._alternatives.setFont(theme.body(9))
        self._alternatives.setWordWrap(True)
        lay.addWidget(self._alternatives)
        lay.addStretch(1)
        lay.addWidget(Rule(theme))

        foot = QHBoxLayout()
        stop = _button("Stop this run", theme, "link")
        stop.clicked.connect(self.stop_requested.emit)
        foot.addWidget(stop)
        foot.addStretch(1)
        decline = _button("Do not use it", theme, "secondary")
        decline.clicked.connect(self.declined.emit)
        foot.addWidget(decline)
        self._accept = _button("Use this Bates format", theme, "primary")
        self._accept.setFont(theme.body_strong(10))
        self._accept.clicked.connect(self.confirmed.emit)
        foot.addWidget(self._accept)
        lay.addLayout(foot)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(page)

    # -- rendering ----------------------------------------------------------

    def show_proposal(self, proposal) -> None:
        """Render a :class:`dociq.gui.pipeline.BatesProposal`.

        A proposal with no example is rendered as exactly that, and the forward
        action is DISABLED. The screen's whole claim is "here is a locator from
        your production" — with nothing to show, confirming would be the
        operator approving a pattern sight unseen, which is the state this
        screen was built to end.
        """
        self._example.setText(proposal.example or "(no example locator)")
        self._pattern.setText(
            f"Format: {proposal.pattern}"
            if proposal.pattern else "The pipeline named no format.")
        self._documents.set_value(f"{proposal.documents:,}")
        self._pages.set_value(f"{proposal.pages:,}")
        self._coverage.set_value(f"{proposal.coverage_pct:.0f}%")

        self._alternatives.setText(self._alternatives_text(proposal))
        self._alternatives.setStyleSheet(
            f"color: {self._theme.palette.warn if proposal.alternatives else self._theme.palette.ink_muted};"
        )
        usable = bool(proposal.example)
        self._accept.setEnabled(usable)
        self._accept.setToolTip(
            "" if usable
            else "No example locator came back with this format, so there is "
                 "nothing here to confirm it against.")

    def _alternatives_text(self, proposal) -> str:
        if not proposal.alternatives:
            return (
                "One stamp series was found in this matter. Numbers that do "
                "not match the confirmed format are flagged, never corrected "
                "silently.")
        listed = ", ".join(proposal.alternatives)
        return (
            f"THIS MATTER CARRIES MORE THAN ONE STAMP SERIES — DocIQ also "
            f"read: {listed}. Confirming the format above applies it to the "
            f"pages that match it and leaves the rest as they read. Because "
            f"the matter is multi-series, D-28 REFUSES to repair a near-miss "
            f"prefix anywhere in it: DocIQ cannot tell a genuine second series "
            f"from a misreading of the first, so it will not guess. Every "
            f"mismatch is flagged for you instead.")

    # -- what the tests and the window read ---------------------------------

    def example_text(self) -> str:
        return self._example.text()

    def alternatives_text(self) -> str:
        return self._alternatives.text()


class HandoffScreen(QWidget):
    """§8 / acceptance criterion 8 — "Analyze in Claude", Paths B and A.

    Path B leads. §8 recommends it for forensic matters and D-20 makes it the
    route proven at full scale, so it is first on the screen, it is described
    in full, and its action is the primary one. Path A is real and is bounded:
    D-20 proves it on a deliberately scoped subset, so this screen makes the
    operator choose that scope and shows them, verbatim, the statement of scope
    that will be written INTO the package. A package that silently contains
    part of a matter is the worst thing this screen could produce.
    """

    back_requested = Signal()
    open_matter_folder_requested = Signal()
    build_package_requested = Signal(object)  # PackageScope
    scope_changed = Signal(object)            # PackageScope

    def __init__(self, theme: Theme, parent=None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._view: HandoffView | None = None
        page, lay = _page(theme)

        back = _button("← Back to the summary", theme, "link")
        back.clicked.connect(self.back_requested.emit)
        lay.addWidget(back, alignment=Qt.AlignmentFlag.AlignLeft)

        title = QLabel("Analyze this matter in Claude")
        title.setFont(theme.title(16))
        title.setStyleSheet(f"color: {theme.palette.navy};")
        lay.addWidget(title)
        lay.addWidget(_muted(
            "Two routes. They are not equivalent, and the recommended one is "
            "first.", theme))
        lay.addSpacing(UNIT * 2)

        # -- Path B ---------------------------------------------------------
        lay.addWidget(SectionLabel(
            "Recommended — Expert Assist reads the folder from disk", theme))
        lay.addWidget(Rule(theme, strong=True))
        lay.addWidget(_muted(
            "Open Claude Cowork (or Claude Code) with the matter output folder "
            "as its working directory, then run the Expert Assist intake "
            "skill. Nothing is uploaded, the audit trail stays local beside "
            "the evidence, and the whole record is in scope — this is the "
            "route proven on every document of the matter.", theme, 10))
        self._folder = QLineEdit()
        self._folder.setReadOnly(True)
        self._folder.setFont(theme.mono_plain(9))
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self._folder, 1)
        copy = _button("Copy path", theme)
        copy.clicked.connect(self._copy_path)
        row.addWidget(copy)
        self._open = _button("Open the matter folder", theme, "primary")
        self._open.clicked.connect(self.open_matter_folder_requested.emit)
        row.addWidget(self._open)
        holder = QWidget()
        holder.setLayout(row)
        lay.addWidget(holder)
        self._path_b_note = _muted("", theme, 9)
        lay.addWidget(self._path_b_note)
        lay.addSpacing(UNIT * 3)

        # -- Path A ---------------------------------------------------------
        lay.addWidget(SectionLabel(
            "Alternative — a package to upload in the browser", theme))
        lay.addWidget(Rule(theme, strong=True))
        lay.addWidget(_muted(
            "A Claude Project holds far less than a full matter record, so an "
            "upload package covers a scope you choose. Choose it deliberately: "
            "the scope is written into the package, and downstream nobody can "
            "tell a subset from a whole record unless the package says so.",
            theme, 10))

        scope_row = QHBoxLayout()
        scope_row.setSpacing(UNIT * 2)
        self._scope_kind = QComboBox()
        self._scope_kind.setFont(theme.body(10))
        self._scope_kind.addItem("Every document in the matter", SCOPE_ALL)
        self._scope_kind.addItem("Only documents in a date range", SCOPE_DATES)
        self._scope_kind.addItem("Only documents of one type", SCOPE_TYPES)
        self._scope_kind.currentIndexChanged.connect(self._emit_scope)
        scope_row.addWidget(self._scope_kind, 1)
        self._date_from = QComboBox()
        self._date_from.setFont(theme.body(10))
        self._date_from.currentIndexChanged.connect(self._emit_scope)
        self._date_to = QComboBox()
        self._date_to.setFont(theme.body(10))
        self._date_to.currentIndexChanged.connect(self._emit_scope)
        self._doc_type = QComboBox()
        self._doc_type.setFont(theme.body(10))
        self._doc_type.currentIndexChanged.connect(self._emit_scope)
        for combo in (self._date_from, self._date_to, self._doc_type):
            scope_row.addWidget(combo, 1)
        scope_holder = QWidget()
        scope_holder.setLayout(scope_row)
        lay.addWidget(scope_holder)

        self._scope_caution = QLabel("")
        self._scope_caution.setFont(theme.body(9))
        self._scope_caution.setWordWrap(True)
        self._scope_caution.setStyleSheet(f"color: {theme.palette.warn};")
        lay.addWidget(self._scope_caution)

        lay.addWidget(SectionLabel("Written into the package, verbatim", theme))
        self._statement = QLabel("")
        self._statement.setFont(theme.mono_plain(8))
        self._statement.setWordWrap(True)
        lay.addWidget(self._statement)

        foot = QHBoxLayout()
        self._blocker = _muted("", theme, 9)
        foot.addWidget(self._blocker, 1)
        self._build = _button("Build the upload package", theme)
        self._build.clicked.connect(self._emit_build)
        foot.addWidget(self._build)
        lay.addLayout(foot)

        # -- what the last build actually did (Codex #2, A-1) ----------------
        # Hidden until there IS an outcome. An empty result panel standing
        # permanently under the button is the same non-signal as no panel at
        # all: the operator cannot tell it apart from a click that did nothing.
        self._result_head = QLabel("")
        self._result_head.setFont(theme.body_strong(11))
        self._result_head.setWordWrap(True)
        lay.addWidget(self._result_head)
        self._result_lines = QLabel("")
        self._result_lines.setFont(theme.mono_plain(9))
        self._result_lines.setWordWrap(True)
        self._result_lines.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self._result_lines)
        self._result_missing = QLabel("")
        self._result_missing.setFont(theme.body(9))
        self._result_missing.setWordWrap(True)
        self._result_missing.setStyleSheet(f"color: {theme.palette.warn};")
        lay.addWidget(self._result_missing)
        # An old package copy that survived this build (A-17 / finding A-7).
        # Its own label, BELOW the facts and the missing-document note: the
        # build succeeded, and this is a fact about a different folder.
        self._result_residue = QLabel("")
        self._result_residue.setFont(theme.body(9))
        self._result_residue.setWordWrap(True)
        self._result_residue.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self._result_residue.setStyleSheet(f"color: {theme.palette.warn};")
        lay.addWidget(self._result_residue)
        self._show_package(None)

        lay.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        outer.addWidget(scroll)

    # -- state --------------------------------------------------------------

    def show_handoff(self, view: HandoffView) -> None:
        self._view = view
        self._folder.setText(view.output_root)
        self._path_b_note.setText(view.path_b_note())
        self._open.setEnabled(view.path_b_ready())

        self._reload_scope_choices(view)
        # The controls are SYNCED to the view's scope, not merely repopulated.
        # Without this the screen showed the view's scope statement while
        # ``scope()`` — which is what the build button hands to the pipeline —
        # still read the untouched controls. A package whose contents disagree
        # with the scope statement printed inside it is the precise failure
        # this screen exists to prevent, so the two cannot have separate
        # sources of truth.
        self._sync_controls(view.scope)

        self._statement.setText(view.scope_statement())
        self._scope_caution.setText(view.scope_caution())
        blocker = view.package_blocker()
        self._blocker.setText(blocker)
        self._build.setEnabled(not blocker)
        self._build.setToolTip(blocker)

        kind = view.scope.kind
        self._date_from.setVisible(kind == SCOPE_DATES)
        self._date_to.setVisible(kind == SCOPE_DATES)
        self._doc_type.setVisible(kind == SCOPE_TYPES)

        self._show_package(view.package)

    def _show_package(self, package) -> None:
        """Render what the last build did — or hide the panel entirely (A-1).

        Hidden is a state, not an absence of one: it means *no build has been
        attempted under the scope now on screen*, and it is reached by a scope
        change as well as by a fresh visit. The alternative — leaving the
        previous outcome up — would show "Upload package built" above a scope
        statement describing a different set of documents.
        """
        shown = package is not None
        for label in (self._result_head, self._result_lines,
                      self._result_missing, self._result_residue):
            label.setVisible(shown)
        if package is None:
            self._result_head.setText("")
            self._result_lines.setText("")
            self._result_missing.setText("")
            self._result_residue.setText("")
            return
        self._result_head.setText(package.headline)
        self._result_head.setStyleSheet(
            f"color: {self._theme.palette.navy if package.ok else self._theme.palette.warn};")
        self._result_lines.setText("\n".join(package.lines))
        note = package.missing_note()
        self._result_missing.setText(note)
        self._result_missing.setVisible(bool(note))
        residue = package.residue_note()
        self._result_residue.setText(residue)
        self._result_residue.setVisible(bool(residue))

    # -- what a test reads, since nobody has ever driven this with a mouse ---
    #
    # The text itself, NOT a widget-visibility query. Under the offscreen
    # platform nothing has a shown top-level window, so ``isVisible()`` is False
    # for every widget on every screen and an accessor built on it would report
    # "" for a panel that is in fact rendering. The panel is CLEARED when there
    # is no outcome, so "" means the same thing without depending on that.

    def package_headline(self) -> str:
        return self._result_head.text()

    def package_lines(self) -> str:
        return self._result_lines.text()

    def package_missing_text(self) -> str:
        return self._result_missing.text()

    def package_residue_text(self) -> str:
        return self._result_residue.text()

    def _reload_scope_choices(self, view: HandoffView) -> None:
        """Populate the date and type pickers from the run's own documents.

        Selection over contract data already on screen: the pipeline decided
        what a document's type and dates are, this only lists them. Repopulated
        only when the values actually changed, because clearing a combo emits
        ``currentIndexChanged`` and a re-entrant scope change would reset the
        operator's choice the instant they made it.
        """
        for combo, values in ((self._date_from, view.dated),
                              (self._date_to, view.dated),
                              (self._doc_type, view.doc_types)):
            if [combo.itemData(i) for i in range(combo.count())] == list(values):
                continue
            was = combo.blockSignals(True)
            combo.clear()
            for value in values:
                combo.addItem(value, value)
            if combo is self._date_to and values:
                combo.setCurrentIndex(len(values) - 1)
            combo.blockSignals(was)

    def _sync_controls(self, scope: PackageScope) -> None:
        """Point every control at ``scope`` without re-emitting a change."""
        def _select(combo: QComboBox, value: str) -> None:
            if not value:
                return
            was = combo.blockSignals(True)
            index = combo.findData(value)
            if index < 0:
                # A scope value the run's documents do not offer — set from a
                # saved scope, or a range that matches nothing. It is ADDED
                # rather than ignored: silently leaving the control on some
                # other value would show the operator a scope that is not the
                # one the statement beneath it describes.
                combo.addItem(value, value)
                index = combo.count() - 1
            if combo.currentIndex() != index:
                combo.setCurrentIndex(index)
            combo.blockSignals(was)

        _select(self._scope_kind, scope.kind)
        if scope.kind == SCOPE_DATES:
            _select(self._date_from, scope.date_from)
            _select(self._date_to, scope.date_to)
        elif scope.kind == SCOPE_TYPES and scope.doc_types:
            _select(self._doc_type, scope.doc_types[0])

    def scope(self) -> PackageScope:
        kind = self._scope_kind.currentData()
        if kind == SCOPE_DATES:
            return PackageScope(kind=SCOPE_DATES,
                                date_from=self._date_from.currentData() or "",
                                date_to=self._date_to.currentData() or "")
        if kind == SCOPE_TYPES:
            chosen = self._doc_type.currentData()
            return PackageScope(kind=SCOPE_TYPES,
                                doc_types=(chosen,) if chosen else ())
        return PackageScope()

    def _emit_scope(self) -> None:
        self.scope_changed.emit(self.scope())

    def _emit_build(self) -> None:
        """Build the scope whose statement is ON SCREEN, not the controls'.

        The view is what produced the sentence the operator just read. Reading
        the controls again here would let a scope the screen never rendered —
        one set programmatically, or one whose value is not among the offered
        choices — reach the package builder under a statement describing
        something else.
        """
        if self._view is None:
            return
        self.build_package_requested.emit(self._view.scope)

    def _copy_path(self) -> None:
        from PySide6.QtWidgets import QApplication

        clipboard = QApplication.clipboard()
        if clipboard is not None and self._view is not None:
            clipboard.setText(self._view.output_root)
