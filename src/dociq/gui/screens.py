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
    ProfileInfo,
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
    profile_new_requested = Signal()
    profile_review_requested = Signal(object)  # ProfileInfo
    source_chosen = Signal(str)

    def __init__(self, theme: Theme, profiles: tuple[ProfileInfo, ...],
                 parent=None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._profiles = profiles
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

        self._profile = QComboBox()
        self._profile.setFont(theme.body(10))
        # A combo sizes its minimum to its LONGEST item, so a profile named
        # "MODEC monthly progress report · 4 section rules" pushed step 2's row
        # past the viewport and made the whole setup page scroll sideways at
        # the product's minimum window. Capped here rather than by shortening
        # the label: the label is the operator's plain-language handle on the
        # profile and is not the layout's to trim.
        self._profile.setMinimumContentsLength(24)
        self._profile.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        for p in profiles:
            suffix = (f"  ·  {p.section_rules} section rules"
                      if p.section_rules else "")
            self._profile.addItem(f"{p.label}{suffix}", p)
        prof_row = QHBoxLayout()
        prof_row.setContentsMargins(0, 0, 0, 0)
        prof_row.addWidget(self._profile, 1)
        # A link, not a second button: §9 offers it as an alternative to
        # choosing a profile, and giving it the same weight as the forward
        # action is what made the earlier layout read as two equal choices.
        new_profile = _button("Profile new format…", theme, "link")
        new_profile.clicked.connect(self.profile_new_requested.emit)
        prof_row.addWidget(new_profile)
        prof_holder = QWidget()
        prof_box = QVBoxLayout(prof_holder)
        prof_box.setContentsMargins(0, 0, 0, 0)
        prof_box.setSpacing(UNIT // 2)
        prof_inner = QWidget()
        prof_inner.setLayout(prof_row)
        prof_box.addWidget(prof_inner)
        # A LINK, not a peer button. D-16 removed "Review what gets dropped" as
        # a button because a second button beside the primary read as a
        # prerequisite step — but §6 still requires the checklist to exist and
        # requires that nothing be dropped that the expert was not shown. A
        # link alongside the existing "Profile new format…" link keeps exactly
        # one forward action on this screen while leaving the review reachable.
        self._review = _button("See what this profile keeps and drops…",
                               theme, "link")
        self._review.clicked.connect(self._emit_review)
        prof_box.addWidget(self._review, alignment=Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(_Step(
            2, "Format profile", theme, prof_holder,
            "A profile lists the sections an expert approved for removal. "
            "With no profile, every page is kept."))

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
            profile=self._profile.currentData(),
            master_index_path=self._index.text() or None,
        )

    def _refresh(self) -> None:
        ready = bool(self._source.text() and self._output.text())
        self._run.setEnabled(ready)
        self._blocker.setText(
            "" if ready
            else "Choose a documents folder and an output folder to start."
        )

    def _emit_run(self) -> None:
        self.run_requested.emit(self.request())

    def _emit_review(self) -> None:
        profile = self._profile.currentData()
        if profile is not None:
            self.profile_review_requested.emit(profile)

    # -- pickers ------------------------------------------------------------

    def _pick_source(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose the documents folder")
        if path:
            self._source.setText(path)
            self.source_chosen.emit(path)
            self._refresh()

    def _pick_output(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose the output folder")
        if path:
            self._output.setText(path)
            self._refresh()

    def _pick_index(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose the master document index", "",
            "Index files (*.xlsx *.xls *.csv)")
        if path:
            self._index.setText(path)


class ProgressScreen(QWidget):
    """Per-document status while a run is in flight."""

    cancel_requested = Signal()

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

        foot = QHBoxLayout()
        foot.addStretch(1)
        cancel = _button("Cancel", theme)
        cancel.clicked.connect(self.cancel_requested.emit)
        foot.addWidget(cancel)
        lay.addLayout(foot)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(page)

    def reset(self) -> None:
        clear_layout(self._rows_lay, keep_trailing=1)  # keep the stretch
        self._bar.setValue(0)
        self._count.setText("")
        self._title.setText("Reading the folder…")

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
        if self._plan is None:
            return
        self._plan = self._plan.with_toggled(key)
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


DISPOSITION_WORDS = ("DROP", "KEEP", "AUTOMATIC")
"""Every value :meth:`ChecklistRow.disposition_word` can return.

Enumerated so the column that holds them can be sized from the widest one. A
test asserts this tuple is exhaustive — otherwise adding a fourth word would
reintroduce the clipping this exists to prevent, silently.
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
    profile_accepted = Signal(object)  # ProfileInfo

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

        lay.addWidget(SectionLabel("Sections this profile decides", theme))
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
        self._title.setText(view.profile.label)
        self._subtitle.setText(
            f"{view.profile.profile_id} · version {view.profile.version} "
            f"· {view.profile.section_rules} declared section "
            f"rule{'' if view.profile.section_rules == 1 else 's'}"
        )
        self._source.setText(
            view.source
            or "The pipeline did not say where these rules came from."
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
        return w

    def _emit_accept(self) -> None:
        if self._view is not None:
            self.profile_accepted.emit(self._view.profile)


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
