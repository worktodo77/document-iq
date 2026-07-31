"""Entry point.

    python -m dociq.gui.app

Sprint 1 runs against the mock pipeline. Sprint 2 installs the real adapter with
:func:`dociq.gui.pipeline.set_pipeline` — here and nowhere else.
"""

from __future__ import annotations

import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from dociq.gui.main_window import MainWindow
from dociq.gui.mock_pipeline import MockPipeline
from dociq.gui.widgets import ICON_ICO

MOCK_STEP_DELAY_S = 0.12
"""Sprint 1 only: the mock finishes instantly, and a progress screen that flicks
past in one frame cannot be reviewed. Logged as a deliberate delay so nobody
mistakes it for the pipeline's speed."""


def build_app(argv: list[str] | None = None) -> tuple[QApplication, MainWindow]:
    app = QApplication.instance() or QApplication(argv or sys.argv)
    app.setApplicationName("LI Document IQ")
    app.setOrganizationName("Long International")
    if ICON_ICO.is_file():
        app.setWindowIcon(QIcon(str(ICON_ICO)))
    window = MainWindow(pipeline=MockPipeline(step_delay_s=MOCK_STEP_DELAY_S))
    return app, window


def main(argv: list[str] | None = None) -> int:
    print("[dociq] Sprint-1 shell: the pipeline is MOCKED, no files are read "
          f"or written (artificial per-document delay {MOCK_STEP_DELAY_S}s).")
    app, window = build_app(argv)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
