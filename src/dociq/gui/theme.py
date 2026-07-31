"""The D-07 "counsel docket" design system, in one place.

Every color comes from :mod:`dociq.branding.palette`, which samples the brand
art — no hex literal for a brand color appears anywhere else in the GUI. Every
spacing value is a multiple of :data:`UNIT`, so the hairline grid stays a grid
rather than a set of numbers that happen to look aligned.

D-07 in one sentence: a light editorial chassis — white ground, hairline rules,
an oversized token headline, flags as chips. The interface should read like a
well-set page of a document index, not like a dashboard.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontInfo

from dociq.branding.palette import Palette, sample_palette

UNIT = 8
"""The grid. Every margin, gap and rule offset is a whole multiple of this."""

PAGE_MARGIN = UNIT * 5
HAIRLINE = 1

_SERIF_CANDIDATES = ("Georgia", "Cambria", "Times New Roman")
"""The editorial voice. A serif is what makes the token headline read as a
printed figure rather than as a metric on a dashboard — the distinction D-07's
"counsel docket" direction turns on."""

_SANS_CANDIDATES = ("Segoe UI", "Arial", "Helvetica")
_MONO_CANDIDATES = ("Consolas", "Courier New", "monospace")


def _first_available(candidates: tuple[str, ...]) -> str:
    """The first candidate the font engine actually resolves to itself.

    Asked of :class:`QFontInfo` rather than of :class:`QFontDatabase`: under the
    ``offscreen`` platform plugin the family list comes back EMPTY, so a
    membership test silently selects the last candidate and every render is
    typeset in a substitute. QFontInfo answers from the engine that will do the
    drawing, which is the question that matters.
    """
    for name in candidates:
        if QFontInfo(QFont(name)).family().casefold() == name.casefold():
            return name
    return candidates[-1]


def font_report() -> str:
    """What the type actually resolved to. Printed by the render harness so a
    substituted face is visible in the log rather than only in the pixels."""
    n = len(QFontDatabase.families())
    theme = build_theme()
    return (f"fonts: serif={theme.serif_family} sans={theme.sans_family} "
            f"mono={theme.mono_family} (font database reports {n} families)")


@dataclass(frozen=True)
class Theme:
    """Resolved fonts and colors for one application run.

    The family fields carry a ``_family`` suffix because the type scale below
    exposes methods named for the *roles* (``mono()``, ``label()``…). A field
    named ``mono`` shadows the method of the same name on every instance — the
    dataclass sets it as an instance attribute — and the failure surfaces as a
    hard crash inside ``paintEvent``, not as an AttributeError anywhere useful.
    """

    palette: Palette
    serif_family: str
    sans_family: str
    mono_family: str

    # -- type scale ---------------------------------------------------------
    # Sizes are point sizes, chosen so the headline is unmistakably the largest
    # thing on the summary screen (D-07: "oversized token headline").

    def headline(self, pt: int = 46) -> QFont:
        f = QFont(self.serif_family, pt)
        f.setWeight(QFont.Weight.DemiBold)
        return f

    def title(self, pt: int = 15) -> QFont:
        f = QFont(self.sans_family, pt)
        f.setWeight(QFont.Weight.DemiBold)
        return f

    def body(self, pt: int = 10) -> QFont:
        return QFont(self.sans_family, pt)

    def body_strong(self, pt: int = 10) -> QFont:
        f = QFont(self.sans_family, pt)
        f.setWeight(QFont.Weight.DemiBold)
        return f

    def label(self, pt: int = 8) -> QFont:
        """Letterspaced small capitals — the column heads of the docket."""
        f = QFont(self.sans_family, pt)
        f.setWeight(QFont.Weight.DemiBold)
        f.setCapitalization(QFont.Capitalization.AllUppercase)
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 118)
        return f

    def mono(self, pt: int = 8) -> QFont:
        f = QFont(self.mono_family, pt)
        f.setCapitalization(QFont.Capitalization.AllUppercase)
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 106)
        return f

    def mono_plain(self, pt: int = 8) -> QFont:
        """Monospace with the case left alone — for verbatim data.

        :meth:`mono` uppercases, which is right for the gauge's captions and
        wrong for anything quoted from the corpus: a file path rendered as
        ``MPR/2021-05 MODEC MONTHLY PROGRESS REPORT.PDF`` is no longer the path
        it names.
        """
        return QFont(self.mono_family, pt)

    def figure(self, pt: int = 20) -> QFont:
        """Secondary figures — page counts, document counts."""
        f = QFont(self.serif_family, pt)
        f.setWeight(QFont.Weight.DemiBold)
        return f

    # -- color -------------------------------------------------------------

    def color(self, name: str) -> QColor:
        return QColor(self.palette.as_dict()[name])


def build_theme() -> Theme:
    """Resolve the theme. Requires a QApplication (font enumeration needs one)."""
    return Theme(
        palette=sample_palette(),
        serif_family=_first_available(_SERIF_CANDIDATES),
        sans_family=_first_available(_SANS_CANDIDATES),
        mono_family=_first_available(_MONO_CANDIDATES),
    )


def stylesheet(theme: Theme) -> str:
    """Application-wide QSS.

    Kept small on purpose: anything with a shape of its own is painted by a
    widget in :mod:`dociq.gui.widgets` where the geometry can be reasoned about,
    and QSS is used only for flat fills, type color and the hairline borders.
    """
    p = theme.palette
    return f"""
    QWidget {{
        background: {p.ground};
        color: {p.ink};
    }}
    QLabel#muted {{ color: {p.ink_muted}; }}
    QLabel#navy  {{ color: {p.navy}; }}

    QPushButton#primary {{
        background: {p.navy};
        color: {p.ground};
        border: none;
        padding: {UNIT + 3}px {UNIT * 3}px;
        font-weight: 600;
    }}
    QPushButton#primary:hover  {{ background: {p.accent}; }}
    QPushButton#primary:disabled {{ background: {p.hairline_strong}; }}

    QPushButton#secondary {{
        background: {p.ground};
        color: {p.navy};
        border: {HAIRLINE}px solid {p.hairline_strong};
        padding: {UNIT}px {UNIT * 2}px;
    }}
    QPushButton#secondary:hover {{
        border-color: {p.accent};
        color: {p.accent};
    }}

    QPushButton#link {{
        background: transparent;
        border: none;
        color: {p.accent};
        text-align: left;
        padding: 0px;
    }}

    QLineEdit {{
        background: {p.ground};
        border: none;
        border-bottom: {HAIRLINE}px solid {p.hairline_strong};
        padding: {UNIT}px 0px;
        selection-background-color: {p.tint_strong};
        selection-color: {p.ink};
    }}
    QComboBox {{
        background: {p.ground};
        border: none;
        border-bottom: {HAIRLINE}px solid {p.hairline_strong};
        padding: {UNIT}px 0px;
    }}
    QComboBox QAbstractItemView {{
        border: {HAIRLINE}px solid {p.hairline_strong};
        background: {p.ground};
        selection-background-color: {p.tint};
        selection-color: {p.ink};
        outline: none;
    }}

    QProgressBar {{
        background: {p.gauge_track};
        border: none;
    }}
    QProgressBar::chunk {{ background: {p.accent}; }}

    QScrollArea, QScrollArea > QWidget > QWidget {{ background: {p.ground}; }}
    QScrollBar:vertical {{
        background: {p.ground}; width: {UNIT}px; margin: 0px;
    }}
    QScrollBar::handle:vertical {{ background: {p.hairline_strong}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0px; }}
    """
