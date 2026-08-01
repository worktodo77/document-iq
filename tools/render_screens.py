"""Render every screen offscreen to PNG, for design review.

A native desktop app cannot be reviewed in a browser, so this is how a screen
gets looked at: it drives the real widgets with the mock pipeline under
``QT_QPA_PLATFORM=offscreen`` and grabs each one. Every claim about how a screen
looks is made against these files.

    python tools/render_screens.py --out <folder>
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Offscreen, and the window is really shown — a grab of a never-shown window
# captures pre-visibility state (an auto-hiding scrollbar is still painted), so
# the render disagrees with the running app in exactly the details a design
# review is looking at.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtGui import QFontDatabase  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from dociq.gui.theme import font_report  # noqa: E402

# The offscreen plugin ships NO font database, so the design's faces have to be
# registered by hand or every screen renders in a substitute nobody will see.
_FONT_FILES = (
    "georgia.ttf", "georgiab.ttf",          # headline + figures
    "segoeui.ttf", "seguisb.ttf", "segoeuib.ttf",  # body + labels
    "consola.ttf", "consolab.ttf",          # gauge captions
)


def _load_fonts() -> None:
    missing = []
    for name in _FONT_FILES:
        path = Path("C:/Windows/Fonts") / name
        if not path.is_file() or QFontDatabase.addApplicationFont(str(path)) < 0:
            missing.append(name)
    if missing:
        print(f"[render] WARNING: fonts not registered, renders will substitute: "
              f"{', '.join(missing)}")

from dociq.gui.main_window import DETAIL, PROGRESS, SETUP, SUMMARY, MainWindow  # noqa: E402
from dociq.gui.mock_pipeline import MockPipeline, at_measured_scale  # noqa: E402
from dociq.gui.view_models import FLAG_OCR, FLAG_RECONCILIATION  # noqa: E402
from dociq.gui.pipeline import RunRequest  # noqa: E402

SIZE = (1180, 800)
TALL = 1100
"""Height used for the summary renders — the waterfall makes that page taller
than a laptop viewport, and it scrolls in the app."""


def _grab(window: MainWindow, path: Path) -> None:
    # Settled before grabbing, not grabbed and hoped for: the first pass after
    # a screen is populated still carries the pre-layout geometry, and the
    # summary was captured with a scrollbar that the settled layout does not
    # show. A render that disagrees with the running app is worse than none.
    for _ in range(3):
        window.layout().activate()
        QApplication.processEvents()
    window.repaint()
    QApplication.processEvents()
    window.grab().save(str(path))
    print(f"[render] {path.name}")


def render(out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    _load_fonts()
    print(f"[render] {font_report()}")
    pipeline = MockPipeline()
    window = MainWindow(pipeline=pipeline)
    window.resize(*SIZE)
    window.show()  # offscreen: no window reaches anyone's desktop
    QApplication.processEvents()

    # 1 — setup, empty
    window.stack.setCurrentIndex(SETUP)
    _grab(window, out / "01_setup_empty.png")

    # 2 — setup, filled in
    request = RunRequest(
        source_root=r"D:\Matters\MODEC Petrobras\Project Files",
        output_root=r"D:\Matters\MODEC Petrobras\DocIQ output",
        profile=pipeline.profiles()[0],
        master_index_path=r"D:\Matters\MODEC Petrobras\LI Master Index 495.xlsx",
    )
    window.setup.set_paths(request.source_root, request.output_root,
                           request.master_index_path)
    window.setup.set_preview(pipeline.preview_folder(request.source_root))
    _grab(window, out / "02_setup_filled.png")

    # 3 — progress, part way through (driven synchronously, no thread)
    window.stack.setCurrentIndex(PROGRESS)
    window.progress.reset()
    seen: list = []

    def on_progress(event) -> None:
        seen.append(event)
        if len(seen) <= 9:
            window.progress.append(event)

    pipeline.run(request, on_progress, lambda: False)
    _grab(window, out / "03_progress.png")

    # 4 — summary, with a master index (reconciliation flag present).
    #     Taller window: the waterfall plus the accounting row does not fit in
    #     800 px, and a render that stops half way is not a review.
    outcome = pipeline.run(request, lambda _e: None, lambda: False)
    window.resize(SIZE[0], TALL)
    window.show_outcome(outcome)
    _grab(window, out / "04_summary.png")

    # 4b — the same run after the operator drops two more sections from the
    #      waterfall itself. This is the interaction Alex ruled on, so it gets
    #      its own render: the stack re-flows and the screen says the files on
    #      disk have not caught up.
    plan = outcome.plan
    for key in ("Organization Charts", "Transmittal Sheets"):
        window.summary.plan_changed.emit(plan.with_toggled(key))
        plan = plan.with_toggled(key)
    _grab(window, out / "04b_summary_toggled.png")

    # 4d — the fixture at the MEASURED record's scale (19.4M token floor, ~97×
    #      capacity). Reviewed separately because a two-digit multiplier and a
    #      three-digit one are different layout problems, and the real record is
    #      the one the screen has to survive.
    window.summary.plan_changed.emit(at_measured_scale(outcome.plan))
    _grab(window, out / "04d_summary_measured_scale.png")

    # 4c — every expert lever off: the record at full size, still not a failure
    #      state. This is the case a first-time operator lands in.
    plan = outcome.plan
    for lever in outcome.plan.levers:
        if not lever.locked and lever.engaged:
            plan = plan.with_toggled(lever.key)
    window.summary.plan_changed.emit(plan)
    _grab(window, out / "04c_summary_nothing_dropped.png")
    window.resize(*SIZE)

    # 5 — detail behind the OCR chip
    window.show_flag(FLAG_OCR)
    _grab(window, out / "05_detail_ocr.png")

    # 6 — detail behind the reconciliation chip
    window.show_flag(FLAG_RECONCILIATION)
    _grab(window, out / "06_detail_reconciliation.png")

    # 7 — summary without a profile and without an index: nothing dropped, so
    #     the corpus stays over capacity. The gauge's overrun state is the case
    #     most likely to be drawn wrong, so it gets its own render.
    plain = RunRequest(
        source_root=request.source_root,
        output_root=request.output_root,
        profile=pipeline.profiles()[2],
        master_index_path=None,
    )
    window.resize(SIZE[0], TALL)
    window.show_outcome(pipeline.run(plain, lambda _e: None, lambda: False))
    _grab(window, out / "07_summary_no_profile.png")

    window.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="render_screens")
    ap.add_argument("--out", type=Path, required=True)
    return render(ap.parse_args(argv).out)


if __name__ == "__main__":
    raise SystemExit(main())
