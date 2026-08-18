"""The painted pieces of the D-07 chassis.

Anything with a shape of its own is painted here rather than assembled out of
stylesheet boxes, because the design's whole argument is the *grid*: a hairline
that is 1 physical pixel, waterfall bars whose lengths are the token counts they
claim to be, chips that sit on a shared baseline. QSS cannot promise any of that.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFontMetrics,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from dociq.branding.palette import BRAND_DIR
from dociq.gui.pipeline import LEVER_AUTOMATIC, LEVER_RECOGNIZED
from dociq.gui.theme import HAIRLINE, UNIT, Theme
from dociq.gui.view_models import CAPACITY_LABEL, CAPACITY_SOURCE

LOCKUP_PNG = BRAND_DIR / "li_dociq_lockup.png"
ICON_ICO = BRAND_DIR / "li_dociq_icon.ico"
ICON_PNG = BRAND_DIR / "li_dociq_icon.png"

HEADER_HEIGHT = UNIT * 10
LOCKUP_HEIGHT = UNIT * 6


class Rule(QWidget):
    """A hairline. One weight everywhere — that is what makes it a grid."""

    def __init__(self, theme: Theme, strong: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._color = QColor(
            theme.palette.hairline_strong if strong else theme.palette.hairline
        )
        self.setFixedHeight(HAIRLINE)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, _event) -> None:
        QPainter(self).fillRect(self.rect(), self._color)


class SectionLabel(QLabel):
    """A letterspaced small-caps column head."""

    def __init__(self, text: str, theme: Theme, parent=None) -> None:
        super().__init__(text, parent)
        self.setFont(theme.label())
        self.setStyleSheet(f"color: {theme.palette.ink_muted};")


class OfflineBadge(QWidget):
    """The standing "offline — no network" indicator (Principle 4).

    In the window chrome and never dismissed: it is the first question a law
    firm's IT reviewer asks, so the answer is on screen at all times rather than
    in an about box. It reports a *design guarantee*, not a live probe — the
    product makes no network calls at all, so there is nothing to sample.
    """

    def __init__(self, theme: Theme, parent=None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._text = "offline — no network"
        fm = QFontMetrics(theme.label(8))
        self._text_w = fm.horizontalAdvance(self._text.upper()) + 12
        self.setFixedHeight(UNIT * 3 + 4)
        self.setFixedWidth(self._text_w + UNIT * 5)
        self.setToolTip(
            "LI Document IQ makes no network connections of any kind. All "
            "processing, including OCR, runs on this machine."
        )

    def paintEvent(self, _event) -> None:
        p = self._theme.palette
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(QColor(p.hairline_strong), HAIRLINE))
        painter.setBrush(QColor(p.tint))
        painter.drawRoundedRect(r, r.height() / 2, r.height() / 2)

        d = UNIT
        cy = r.center().y()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(p.accent))
        painter.drawEllipse(QRectF(r.left() + UNIT * 1.5, cy - d / 4, d / 2, d / 2))

        painter.setPen(QColor(p.navy))
        painter.setFont(self._theme.label(8))
        painter.drawText(
            QRectF(r.left() + UNIT * 3, r.top(), r.width() - UNIT * 3.5, r.height()),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            self._text,
        )


class HeaderBar(QWidget):
    """Window chrome: the D-09 lockup at left, the offline badge at right."""

    def __init__(self, theme: Theme, parent=None) -> None:
        super().__init__(parent)
        self._theme = theme
        self.setFixedHeight(HEADER_HEIGHT)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(UNIT * 5, 0, UNIT * 5, 0)

        self._lockup = QLabel()
        pix = load_lockup()
        if pix is not None:
            # Scaled from the 4× asset and tagged with the device pixel ratio,
            # so the badge is sharp at 150% Windows scaling instead of being a
            # 44 px bitmap stretched to 66.
            dpr = self.devicePixelRatioF()
            scaled = pix.scaledToHeight(
                int(LOCKUP_HEIGHT * dpr), Qt.TransformationMode.SmoothTransformation
            )
            scaled.setDevicePixelRatio(dpr)
            self._lockup.setPixmap(scaled)
        else:
            # The lockup is generated art; if it has not been generated the
            # header says so rather than rendering an empty box that looks like
            # a layout bug.
            self._lockup.setText("Document IQ  (run make_logo)")
            self._lockup.setFont(theme.title())
        lay.addWidget(self._lockup)
        lay.addStretch(1)
        lay.addWidget(OfflineBadge(theme))

    # The rule under the header is a sibling Rule widget in the window's layout,
    # NOT painted here: child widgets carry the stylesheet's opaque white
    # background and stretch to the bar's full height, so a line drawn on the
    # bar's own bottom row is overpainted wherever a child sits — which showed
    # up as a hairline with a 110 px gap under the lockup.


def load_lockup() -> QPixmap | None:
    if not LOCKUP_PNG.is_file():
        return None
    pix = QPixmap(str(LOCKUP_PNG))
    return None if pix.isNull() else pix


class DisclosureBar(QWidget):
    """A standing notice under the header, for what the run itself is.

    Used by the Sprint-1 shell to say its figures come from a fixture and how
    far that fixture sits from the measured record. It is not dismissible and it
    is not a tooltip: a screenshot of this window will be forwarded to people
    who were not in the conversation, and the caveat has to travel with it.
    """

    def __init__(self, text: str, theme: Theme, parent=None) -> None:
        super().__init__(parent)
        self._theme = theme
        lay = QHBoxLayout(self)
        lay.setContentsMargins(UNIT * 5, UNIT, UNIT * 5, UNIT)
        label = QLabel(text)
        label.setFont(theme.body(9))
        label.setWordWrap(True)
        label.setStyleSheet(f"color: {theme.palette.warn}; background: transparent;")
        lay.addWidget(label, 1)

    def paintEvent(self, _event) -> None:
        p = self._theme.palette
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(p.warn_tint))
        painter.fillRect(0, self.height() - HAIRLINE, self.width(), HAIRLINE,
                         QColor(p.warn))


class Chip(QWidget):
    """A flag, as D-07 asks for them: a pill with a count and a plain label,
    clickable through to the detail behind it."""

    clicked = Signal(str)

    def __init__(self, key: str, label: str, count: int, theme: Theme,
                 tone: str = "warn", parent=None) -> None:
        super().__init__(parent)
        self._key, self._label, self._count = key, label, count
        self._theme, self._tone = theme, tone
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        fm_c = QFontMetrics(theme.body_strong(10))
        fm_l = QFontMetrics(theme.body(10))
        self._w = (UNIT * 2 + fm_c.horizontalAdvance(f"{count:,}") + UNIT
                   + fm_l.horizontalAdvance(label) + UNIT * 2)
        self.setFixedSize(QSize(self._w, UNIT * 4 + 4))
        self.setToolTip(f"Show the {label.lower()} in detail")

    @property
    def key(self) -> str:
        return self._key

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._key)

    def paintEvent(self, _event) -> None:
        p = self._theme.palette
        warn = self._tone == "warn"
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(QColor(p.warn if warn else p.hairline_strong), HAIRLINE))
        painter.setBrush(QColor(p.warn_tint if warn else p.tint))
        painter.drawRoundedRect(r, r.height() / 2, r.height() / 2)

        x = r.left() + UNIT * 2
        painter.setFont(self._theme.body_strong(10))
        painter.setPen(QColor(p.warn if warn else p.navy))
        count = f"{self._count:,}"
        cw = QFontMetrics(self._theme.body_strong(10)).horizontalAdvance(count)
        painter.drawText(QRectF(x, r.top(), cw, r.height()),
                         int(Qt.AlignmentFlag.AlignVCenter), count)
        painter.setFont(self._theme.body(10))
        painter.setPen(QColor(p.ink))
        painter.drawText(QRectF(x + cw + UNIT, r.top(), r.width(), r.height()),
                         int(Qt.AlignmentFlag.AlignVCenter), self._label)



def compact_tokens(value: int) -> str:
    """1_332_602 → "1.33M". The waterfall is read at a glance; nine digits in a
    column are not read at all."""
    if value >= 1_000_000:
        return f"{value / 1e6:.2f}M"
    if value >= 1_000:
        return f"{value / 1e3:.0f}K"
    return str(value)


class WaterfallRow(QWidget):
    """One bar of the reduction waterfall.

    The row IS the control (Alex's ruling, 2026-07-30): clicking an expert lever
    toggles that section's KEEP/DROP and the whole waterfall re-flows. There is
    no separate checklist — the picture and the next action are the same object.

    Color encodes CATEGORY only (expert lever / automatic saving / capacity),
    never magnitude: every row states its own number and its own state in words,
    so the screen carries the same information in monochrome.
    """

    toggled = Signal(str)

    TOTAL, EXPERT, AUTOMATIC, RESULT, CAPACITY, RECOGNIZED = (
        "total", "expert", "automatic", "result", "capacity", "recognized")
    """RECOGNIZED is the row for a section the tool found and will never offer
    to drop (A-20).

    It is a fifth kind rather than a re-use of AUTOMATIC, and the reason is what
    AUTOMATIC's row SAYS: "Removed mechanically by the tool, not by an expert
    decision". A weather log or a set of timesheets is recognized and KEPT, so
    rendering it on the automatic row would tell the operator that the tool
    removed the one category §4 grades most dangerous to lose — a false
    statement about the corpus, made in the place the operator goes to check
    what happened to it."""

    LABEL_W = UNIT * 30
    DELTA_W = UNIT * 26

    def __init__(self, kind: str, key: str, label: str, fraction: float,
                 delta: str, theme: Theme, ghost: float = 0.0,
                 engaged: bool = True, hint: str = "", parent=None) -> None:
        super().__init__(parent)
        self._kind, self._key, self._label = kind, key, label
        self._fraction, self._ghost, self._delta = fraction, ghost, delta
        self._engaged, self._theme = engaged, theme
        self._hover = False
        self.setFixedHeight(UNIT * 4 + 2)
        if kind == self.EXPERT:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setMouseTracking(True)
            self.setToolTip(hint)
            self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        elif hint:
            self.setToolTip(hint)
        self.setAccessibleName(f"{label}, {delta}")

    @property
    def key(self) -> str:
        return self._key

    @property
    def kind(self) -> str:
        return self._kind

    # -- interaction --------------------------------------------------------

    def enterEvent(self, _event) -> None:
        if self._kind == self.EXPERT:
            self._hover = True
            self.update()

    def leaveEvent(self, _event) -> None:
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self._kind == self.EXPERT and event.button() == Qt.MouseButton.LeftButton:
            self.toggled.emit(self._key)

    def keyPressEvent(self, event) -> None:
        # Keyboard parity: the waterfall is the only way to change what is
        # dropped, so it cannot be mouse-only.
        if self._kind == self.EXPERT and event.key() in (
            Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter
        ):
            self.toggled.emit(self._key)
            return
        super().keyPressEvent(event)

    # -- painting -----------------------------------------------------------

    def _mark(self, painter: QPainter, x: float, cy: float) -> None:
        """The state glyph in the label column: a filled box when this section
        is being dropped, an empty box when it is being kept, a padlock when the
        tool did it and the expert has no say."""
        p = self._theme.palette
        s = UNIT * 1.25
        box = QRectF(x, cy - s / 2, s, s)
        if self._kind == self.AUTOMATIC:
            painter.setPen(QPen(QColor(p.ink_muted), 1.4))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(box.left(), box.top() + s * 0.35,
                                    s, s * 0.65))
            painter.drawArc(QRectF(box.left() + s * 0.2, box.top(),
                                   s * 0.6, s * 0.7), 0, 180 * 16)
            return
        if self._kind != self.EXPERT:
            return
        painter.setPen(QPen(QColor(p.accent if self._engaged else p.hairline_strong),
                            1.4))
        painter.setBrush(QColor(p.accent) if self._engaged
                         else Qt.BrushStyle.NoBrush)
        painter.drawRect(box)

    def paintEvent(self, _event) -> None:
        p = self._theme.palette
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        h = float(self.height())
        w = float(self.width())
        cy = h / 2

        if self._hover:
            painter.fillRect(self.rect(), QColor(p.tint))

        text_color = {
            self.TOTAL: p.ink,
            self.EXPERT: p.ink if self._engaged else p.ink_muted,
            self.AUTOMATIC: p.ink_muted,
            self.RECOGNIZED: p.ink_muted,
            self.RESULT: p.navy,
            self.CAPACITY: p.ink_muted,
        }[self._kind]

        font = (self._theme.body_strong(10)
                if self._kind in (self.TOTAL, self.RESULT)
                else self._theme.body(10))
        if self._kind == self.AUTOMATIC:
            font.setItalic(True)
        painter.setFont(font)
        self._mark(painter, UNIT * 0.5, cy)
        painter.setPen(QColor(text_color))
        # Elided rather than clipped: a label cut mid-glyph renders a tofu box
        # that reads as a missing font, not as a truncation.
        label_w = self.LABEL_W - UNIT * 3.5
        painter.drawText(
            QRectF(UNIT * 3, 0, label_w, h),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            QFontMetrics(font).elidedText(self._label, Qt.TextElideMode.ElideRight,
                                          int(label_w)),
        )

        bar_x = self.LABEL_W
        bar_w = max(UNIT * 4.0, w - self.LABEL_W - self.DELTA_W)
        bar_h = UNIT * 1.75
        bar_top = cy - bar_h / 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.fillRect(QRectF(bar_x, bar_top, bar_w, bar_h),
                         QColor(p.gauge_track))

        fill_w = bar_w * max(0.0, min(1.0, self._fraction))
        if self._kind == self.CAPACITY:
            # A fixed reference, drawn as an outline: the capacity never moves,
            # the bars move toward it. A solid fill here would read as another
            # quantity in the same series.
            pen = QPen(QColor(p.navy), 1.6)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if fill_w < UNIT * 1.5:
                # Against the measured record the capacity is ~1% of the bar,
                # and a dashed rectangle that narrow renders as a smudge that
                # reads like a glyph fault. A tick says "reference line" at any
                # width. The figure itself is printed in the delta column
                # either way, so nothing is hidden by the change of shape.
                painter.setPen(pen)
                painter.drawLine(int(bar_x + max(1.0, fill_w)), int(bar_top - 2),
                                 int(bar_x + max(1.0, fill_w)),
                                 int(bar_top + bar_h + 2))
            else:
                pen.setStyle(Qt.PenStyle.DashLine)
                painter.setPen(pen)
                painter.drawRect(QRectF(bar_x + 0.8, bar_top + 0.8,
                                        max(2.0, fill_w - 1.6), bar_h - 1.6))
        else:
            color = {
                self.TOTAL: QColor(p.hairline_strong),
                self.EXPERT: QColor(p.accent),
                self.AUTOMATIC: QColor(p.ink_muted),
                self.RECOGNIZED: QColor(p.hairline_strong),
                self.RESULT: QColor(p.navy),
            }[self._kind]
            painter.setPen(Qt.PenStyle.NoPen)
            painter.fillRect(QRectF(bar_x, bar_top, fill_w, bar_h), color)

        if self._ghost > 0:
            # The tail of the bar this lever would remove, cut out of the fill
            # and outlined: the cost of the choice has to be visible before it
            # is made. Drawn INSIDE the bar because the pages are still in the
            # corpus — an outline hanging off the end would say the opposite.
            ghost_w = min(fill_w, bar_w * self._ghost)
            rect = QRectF(bar_x + fill_w - ghost_w, bar_top, ghost_w, bar_h)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.fillRect(rect, QColor(p.tint_strong))
            pen = QPen(QColor(p.accent), 1.2)
            pen.setStyle(Qt.PenStyle.DotLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect.adjusted(0.6, 0.6, -0.6, -0.6))

        painter.setFont(self._theme.mono_plain(8))
        painter.setPen(QColor(text_color))
        painter.drawText(
            QRectF(w - self.DELTA_W + UNIT, 0, self.DELTA_W - UNIT * 2, h),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            self._delta,
        )

        if self.hasFocus():
            painter.setPen(QPen(QColor(p.accent), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(0.5, 0.5, w - 1, h - 1))


class ReductionWaterfall(QWidget):
    """The stack of bars: full record → each lever → what you'd upload → capacity.

    Owns no state. It is handed a :class:`~dociq.gui.pipeline.ReductionPlan`,
    draws it, and emits the key of any lever the operator clicked; whoever owns
    the plan decides what that means and hands back a new one.
    """

    lever_toggled = Signal(str)

    def __init__(self, theme: Theme, parent=None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(2)
        self._plan = None

    def plan(self):
        return self._plan

    def set_plan(self, plan) -> None:
        from dociq.gui.screens import clear_layout  # local: avoids a cycle

        self._plan = plan
        clear_layout(self._lay)
        if plan is None:
            return
        full = max(1, plan.full_tokens)

        rows: list[WaterfallRow] = [WaterfallRow(
            WaterfallRow.TOTAL, "full", "Everything read", 1.0,
            compact_tokens(plan.full_tokens), self._theme,
            hint="Every page extracted from the folder, before anything is left out.",
        )]

        running = plan.full_tokens
        for lever in plan.levers:
            if lever.locked:
                continue
            ghost = 0.0
            # An ``estimated`` lever is PROJECTED, not counted. The mark rides
            # in the delta column beside the figure itself, not in a tooltip:
            # this used to be rendered on automatic rows only, so an expert
            # lever whose saving was a projection stood in the same column, in
            # the same type, as a counted one — the exact claim
            # ``ReductionLever.estimated`` exists to prevent.
            projected = ", projected" if lever.estimated else ""
            if lever.engaged:
                running -= lever.tokens
                delta = f"−{compact_tokens(lever.tokens)}  dropped{projected}"
                hint = (f"{lever.pages:,} pages are being left out. "
                        "Click to keep them.")
            else:
                ghost = lever.tokens / full
                delta = f"{compact_tokens(lever.tokens)}  kept{projected}"
                hint = (f"{lever.pages:,} pages are being kept. Click to leave "
                        f"them out and save {compact_tokens(lever.tokens)} tokens.")
            if lever.estimated:
                hint += (" This saving is projected from the profile's rules, "
                         "not counted from pages this run produced.")
            rows.append(WaterfallRow(
                WaterfallRow.EXPERT, lever.key, lever.label, running / full,
                delta, self._theme, ghost=ghost, engaged=lever.engaged, hint=hint,
            ))

        # Recognized-and-never-offered, BEFORE the automatic rows and visually
        # distinct from them (A-20). These pages are KEPT: the row exists so an
        # expert can see that DocIQ found the weather log and the timesheets and
        # is not touching them. `running` is deliberately not moved — nothing
        # here is subtracted from anything.
        for lever in plan.levers:
            if lever.kind != LEVER_RECOGNIZED:
                continue
            rows.append(WaterfallRow(
                WaterfallRow.RECOGNIZED, lever.key, lever.label, running / full,
                f"{compact_tokens(lever.tokens)}  kept — not offered",
                self._theme,
                hint=(f"{lever.pages:,} pages. Recognized and kept: this "
                      "section is never offered as an omission."
                      + (f" {lever.note}" if lever.note else "")),
            ))

        for lever in plan.levers:
            if lever.kind != LEVER_AUTOMATIC:
                continue
            running -= lever.tokens if lever.engaged else 0
            note = "automatic, estimated" if lever.estimated else "automatic"
            rows.append(WaterfallRow(
                WaterfallRow.AUTOMATIC, lever.key, lever.label, running / full,
                f"−{compact_tokens(lever.tokens)}  {note}", self._theme,
                # The hint names the CATEGORY, never a mechanism. It read
                # "Removed mechanically — exact duplicates and page furniture",
                # which asserts a behavior the pipeline withdraws: `adapter._plan`
                # states that DocIQ *detects* exact-hash duplicates and warns
                # about them and "removes neither them nor page furniture", so
                # the real adapter emits no automatic lever at all and this
                # string was unreachable-but-latent. Track D recorded it
                # (track_d_sprint2 §5.6) and it survived; withdrawn here.
                # Whatever a locked lever turns out to be, its own `label` says
                # what it is — the hint's job is only to say who decided it.
                hint=("Removed mechanically by the tool, not by an expert "
                      "decision — and recorded separately in the log."
                      + (" This saving is projected, not counted."
                         if lever.estimated else "")),
            ))

        rows.append(WaterfallRow(
            WaterfallRow.RESULT, "result", "What you would upload", running / full,
            compact_tokens(max(0, running)), self._theme,
            hint="The reduced corpus, as it stands with these choices.",
        ))
        # D-21: a NAMED, SOURCED reference line — never a budget and never a
        # target. The name is on the row and "reference, not a target" is in
        # the delta column beside the figure, because the row sits at the foot
        # of a stack of shrinking bars and that arrangement alone reads as a
        # goal line unless the row says otherwise. Its source travels with it
        # in the tooltip and in the sentence under the headline.
        rows.append(WaterfallRow(
            WaterfallRow.CAPACITY, "capacity", CAPACITY_LABEL,
            plan.capacity / full,
            f"{compact_tokens(plan.capacity)}  reference, not a target",
            self._theme,
            hint=(f"{CAPACITY_LABEL} — {CAPACITY_SOURCE}. Getting under this "
                  "line is not the objective: on a full matter record the "
                  "corpus stays above it, and Expert Assist reads the matter "
                  "folder from disk where the line does not apply."),
        ))

        for row in rows:
            if row.kind == WaterfallRow.EXPERT:
                row.toggled.connect(self.lever_toggled.emit)
            self._lay.addWidget(row)


class StatFigure(QWidget):
    """A figure over a letterspaced label — the docket's tabular voice."""

    def __init__(self, value: str, label: str, theme: Theme,
                 tone: str = "ink", parent=None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self._value = QLabel(value)
        self._value.setFont(theme.figure())
        color = {"ink": theme.palette.navy, "muted": theme.palette.ink_muted,
                 "warn": theme.palette.warn}[tone]
        self._value.setStyleSheet(f"color: {color};")
        lay.addWidget(self._value)
        lay.addWidget(SectionLabel(label, theme))

    def set_value(self, value: str) -> None:
        self._value.setText(value)
