"""PyInstaller runtime hook — runs before any DocIQ module imports.

TWO JOBS, AND ONE DELIBERATE NON-JOB.

**Job 1 — pin the bundled models before anything can look elsewhere.**
``dociq.ingest.extract.ocr_model_dir()`` already resolves ``sys._MEIPASS``
itself, so this is belt and braces rather than the mechanism; it matters
because a *third-party* module that resolves models on import would otherwise
run before the launcher's bootstrap.

**Job 2 — set the offline flags the ML ecosystem reads.** ``HF_HUB_OFFLINE``,
``TRANSFORMERS_OFFLINE`` and friends are how libraries are told not to reach
out. None of DocIQ's declared dependencies read them today — rapidocr 1.2.3
ships its weights in the wheel and contains no download code at all, which was
verified by reading the installed package, not assumed. They are set anyway
because the cost is nothing and the failure they prevent is silent: a future
dependency bump that introduces a fetch would otherwise fetch, and the run
would look normal.

**The non-job: this hook does NOT install a permanent socket blocker.**
It would be the strongest possible reading of Principle 4, and it is wrong
here for a concrete reason rather than a cautious one. ``socket.socketpair()``
on Windows is a real AF_INET loopback pair, and Qt uses exactly that for its
event notifier; a process-wide block would break the GUI in order to enforce a
property the application does not violate. Enforcement therefore lives where it
can be *measured* instead — ``DocumentIQ-cli.exe --offline-probe`` runs the
whole pipeline, cold OCR engine construction included, under
:class:`dociq.verify.offline.NetworkGuard`, and asserts ZERO attempts. That is
a check a client's IT reviewer can run on their own machine, which is what §10
asks for.
"""

import os
import sys
from pathlib import Path

_bundle = getattr(sys, "_MEIPASS", None)
if _bundle:
    _models = Path(_bundle) / "rapidocr_onnxruntime" / "models"
    if _models.is_dir() and not os.environ.get("DOCIQ_OCR_MODEL_DIR"):
        os.environ["DOCIQ_OCR_MODEL_DIR"] = str(_models)

for _flag in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE",
              "MODELSCOPE_OFFLINE", "RAPIDOCR_OFFLINE"):
    os.environ.setdefault(_flag, "1")
