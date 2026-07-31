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
from dociq.gui.view_models import FlagGroup, SummaryView
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
        prof_holder.setLayout(prof_row)
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
    plan_changed = Signal(object)  # ReductionPlan

    def __init__(self, theme: Theme, parent=None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._view: SummaryView | None = None
        self._plan = None
        page, lay = _page(theme)
        self._lay = lay

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
        self._claude = _button("Analyze in Claude", theme, "primary")
        self._claude.setEnabled(False)
        self._claude.setToolTip(
            "Available once the pipeline is wired in Sprint 2 — the handoff "
            "package (§8) is assembled from a real run's outputs."
        )
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
        self._output.setText(f"Outputs written to {view.output_root}"
                             if view.output_root else "")
        self._open.setEnabled(bool(view.output_root)
                              and Path(view.output_root).is_dir())

    def _paint_headline(self, view: SummaryView) -> None:
        self._headline.setText(view.headline())
        self._unit.setText(view.headline_unit())
        self._capacity_line.setText(view.capacity_line())
        self._basis.setText(view.basis_note())
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
            head.addWidget(primary, 1)
            if it.locator:
                loc = QLabel(it.locator)
                loc.setFont(self._theme.mono_plain(8))
                loc.setStyleSheet(f"color: {self._theme.palette.ink_muted};")
                head.addWidget(loc)
            row_lay.addLayout(head)
            row_lay.addWidget(_muted(it.secondary, self._theme, 9))
            self._items_lay.insertWidget(self._items_lay.count() - 1, row)
            self._items_lay.insertWidget(self._items_lay.count() - 1,
                                         Rule(self._theme))
