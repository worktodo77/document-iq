"""Entry point.

    python -m dociq.gui.app            — the real pipeline
    python -m dociq.gui.app --mock     — the Sprint-1 fixture, for review

Sprint 2 flipped the default. :func:`dociq.gui.pipeline.get_pipeline` returns the
real adapter, and this module no longer names a pipeline at all on the normal
path — which is the whole point of the seam.

``--mock`` stays, and stays LOUD. The mock's own ``disclosure()`` puts a standing
notice above every screen saying the figures are a fixture, so a screenshot taken
from it cannot be mistaken for a run; without a way to launch it, the only way to
review a screen layout would be to have a real corpus to hand.
"""

from __future__ import annotations

import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from dociq.gui.main_window import MainWindow
from dociq.gui.pipeline import get_pipeline, set_pipeline
from dociq.gui.widgets import ICON_ICO

MOCK_STEP_DELAY_S = 0.12
"""``--mock`` only: the mock finishes instantly, and a progress screen that
flicks past in one frame cannot be reviewed. Logged as a deliberate delay so
nobody mistakes it for the pipeline's speed."""


def build_app(argv: list[str] | None = None) -> tuple[QApplication, MainWindow]:
    args = list(argv if argv is not None else sys.argv)
    mock = "--mock" in args
    app = QApplication.instance() or QApplication([a for a in args if a != "--mock"])
    app.setApplicationName("LI Document IQ")
    app.setOrganizationName("Long International")
    if ICON_ICO.is_file():
        app.setWindowIcon(QIcon(str(ICON_ICO)))
    if mock:
        from dociq.gui.mock_pipeline import MockPipeline

        set_pipeline(MockPipeline(step_delay_s=MOCK_STEP_DELAY_S))
    window = MainWindow(pipeline=get_pipeline())
    return app, window


def main(argv: list[str] | None = None) -> int:
    if "--mock" in (argv if argv is not None else sys.argv):
        print("[dociq] MOCK pipeline: no files are read or written "
              f"(artificial per-document delay {MOCK_STEP_DELAY_S}s).")
    app, window = build_app(argv)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
