"""The frozen entry point — ``DocumentIQ.exe``.

Not ``dociq.gui.app`` directly, for three reasons that are all about the
*shipped* artifact rather than about the source tree:

1. **The bundle needs its own bootstrap.** ``sys._MEIPASS`` is the only
   reliable answer to "where did my data go", and the OCR model directory has
   to be pinned to the bundle before anything imports the extractor.
2. **A GUI-only exe cannot be verified.** "Verify the built artifact actually
   runs" means running it, and a window that opens on a developer's desktop is
   not a check anyone can re-run. The launcher therefore carries the gates —
   ``--selftest``, ``--offline-probe``, ``--version`` — so the *packaged* build
   proves itself with the same code the source tree does.
3. **The determinism proof re-enters the executable.** See
   :data:`dociq.verify.determinism.DETERMINISM_RUN_FLAG`.

Nothing here is GUI-track code and nothing here duplicates pipeline logic; it
is argument routing and bundle bootstrap only.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

_T0 = time.perf_counter()
"""Process start, captured before any heavy import. ``--version`` prints the
delta, which is how the D-22 cold-start figure is measured on the artifact
rather than estimated from the source tree."""


def _bundle_dir() -> Path | None:
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass else None


def _bootstrap() -> None:
    """Pin every bundled data location before the first heavy import.

    ``DOCIQ_OCR_MODEL_DIR`` is set only when it is not already set: an operator
    who points the run at their own model directory keeps that choice, and the
    run identity records which models were used either way
    (``RunConfig.limits.ocr_model_id`` hashes the bytes, not the path).
    """
    bundle = _bundle_dir()
    if bundle is None:
        # Running from a checkout — put src/ on the path so the launcher works
        # unfrozen too, which is what makes the build command testable before
        # a build exists.
        src = Path(__file__).resolve().parents[1] / "src"
        if src.is_dir() and str(src) not in sys.path:
            sys.path.insert(0, str(src))
        return
    if not os.environ.get("DOCIQ_OCR_MODEL_DIR"):
        models = bundle / "rapidocr_onnxruntime" / "models"
        if models.is_dir():
            os.environ["DOCIQ_OCR_MODEL_DIR"] = str(models)


def _cmd_version() -> int:
    from dociq.ingest import extract as ex

    bundle = _bundle_dir()
    print("LI Document IQ (DocIQ)")
    print(f"  frozen           : {bool(getattr(sys, 'frozen', False))}")
    print(f"  bundle           : {bundle or '(source tree)'}")
    print(f"  python           : {sys.version.split()[0]}")
    print(f"  ocr model dir    : {ex.ocr_model_dir()}")
    ok, msg = ex.ocr_models_present()
    print(f"  ocr models       : {'present' if ok else 'MISSING — ' + msg}")
    print(f"  ocr model id     : {ex.ocr_model_id()}")
    print(f"  cold start to    : {time.perf_counter() - _T0:.3f} s "
          "(process start to this line, imports included)")
    return 0 if ok else 1


def _cmd_diagnose() -> int:
    """Why the frozen build differs from the source tree — the two questions
    the packaged offline probe asked and the source tree never had to.

    Kept as a shipped command rather than a throwaway script: both findings it
    was written for (OCR silently unavailable in the bundle, and fetch-capable
    modules pulled in by the frozen import graph) are invisible from a
    checkout, so the next person to hit them needs this to exist.
    """
    import importlib
    import sys as _sys

    print("import probe — each dependency the OCR path needs")
    for name in ("fitz", "rapidocr_onnxruntime", "cv2", "numpy",
                 "onnxruntime", "PIL.Image", "yaml", "shapely", "pyclipper"):
        try:
            m = importlib.import_module(name)
            print(f"  OK    {name:22s} {getattr(m, '__file__', '')}")
        except Exception as exc:
            print(f"  FAIL  {name:22s} {type(exc).__name__}: {exc}")

    from dociq.ingest import extract as ex

    print(f"\nocr_models_present : {ex.ocr_models_present()}")
    print(f"ocr_available      : {ex.ocr_available()}")

    print("\nfetch-capable modules already in sys.modules, and who pulled "
          "them in:")
    baseline = set(_sys.modules)
    from dociq.verify import offline

    for name in offline.audit_model_fetch_imports():
        importers = sorted(
            m for m in baseline
            if getattr(_sys.modules.get(m), "__dict__", None)
            and name.split(".")[0] in getattr(_sys.modules[m], "__dict__", {}))
        print(f"  {name}: referenced by {importers[:8] or '(no direct '
              'attribute reference found — imported for its side effects or by '
              'the bootstrap)'}")
    return 0


def _cmd_offline_probe(argv: list[str]) -> int:
    """Run the whole pipeline over the fixture corpus under the network guard.

    The unpackaged suite covers this too (``tests/test_offline.py``), and it
    must ALSO run here: the frozen build has a different import graph — hooks
    pull in modules the source tree never imports — so "the source makes no
    outbound call" does not imply "the exe makes no outbound call".
    """
    import argparse
    import shutil
    import tempfile

    ap = argparse.ArgumentParser(prog="DocumentIQ --offline-probe")
    ap.add_argument("--source", help="a real folder to run over; default is the "
                                     "bundled synthetic fixture corpus")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args(argv)

    from dociq import pipeline, selftest
    from dociq.contracts import RunConfig
    from dociq.ingest import extract as ex
    from dociq.ingest import walker
    from dociq.profiles.model import OperatorStamp
    from dociq.verify import offline

    work = Path(tempfile.mkdtemp(prefix="dociq-offline-"))
    failures: list[str] = []
    try:
        src = Path(args.source) if args.source else selftest._fixture_root(work)
        print(f"LI Document IQ — offline probe\n  source: {src}\n  work:   {work}")

        holes = offline.audit_siblings()
        if holes:
            failures.append(f"unguarded outbound-capable socket entry points: "
                            f"{', '.join(holes)}")
        print(f"  guarded entry points: "
              f"{', '.join(offline.enumerate_guarded_entry_points())}")

        # The engine is torn down so construction — the historical fetch site —
        # happens INSIDE the guard, not before it.
        ex._OCR_ENGINE = None
        t0 = time.perf_counter()
        with offline.no_network() as guard:
            cfg = RunConfig(source_root=str(src), output_root=str(work / "out"),
                            ocr_engine_version=ex.ocr_engine_version())
            outcome = pipeline.run(cfg, pipeline.PipelineOptions(
                walk=walker.WalkOptions(resume=False),
                matter_name="offline probe",
                stamp=OperatorStamp("offline-probe", "2026-08-01T00:00:00Z",
                                    "probe")))
        elapsed = time.perf_counter() - t0
        result = outcome.result

        print(f"\n  pipeline: {len(result.documents)} document(s), "
              f"{result.pages_in} page(s) in {elapsed:.1f} s")
        print(f"  accounting: {'OK' if outcome.accounting.ok else 'DISCREPANT'}")
        if not outcome.accounting.ok:
            failures.append("page accounting did not reconcile under the guard")
        ocr_pages = [p for d in result.documents for p in d.pages
                     if p.kind.value == "ocr"]
        print(f"  OCR pages under the guard: {len(ocr_pages)}")
        if not ocr_pages:
            # Not a pass. A run that never reached the OCR path proves nothing
            # about the path the model fetch used to live on.
            failures.append("no page took the OCR path, so this probe did not "
                            "exercise the model-load path it exists to cover")
        print(f"  {guard.render()}")
        if not guard.clean:
            failures.append(f"{len(guard.attempts)} outbound attempt(s)")
            print(guard.render())
        loaded = offline.audit_model_fetch_imports()
        if loaded:
            failures.append(f"fetch-client modules imported: {', '.join(loaded)}")
        print(f"  fetch-client modules imported: {', '.join(loaded) or 'none'}")
        print(f"  stdlib transport imported (disclosed, see offline.py): "
              f"{', '.join(offline.audit_transport_imports()) or 'none'}")

        if failures:
            print(f"\nOFFLINE PROBE FAILED — {len(failures)} finding(s)")
            for f in failures:
                print(f"  - {f}")
            return 1
        print("\nOFFLINE PROBE PASSED — zero outbound attempts across a full "
              "pipeline run including cold OCR engine construction")
        return 0
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)
        else:
            print(f"\nwork dir kept: {work}")


def _cmd_determinism_run(argv: list[str]) -> int:
    """One determinism repetition, re-entered inside the frozen executable."""
    from dociq.verify import determinism

    exec(compile(determinism._RUNNER, "<determinism-runner>", "exec"),
         {"__name__": "__determinism__", "sys": sys},
         )
    return 0


USAGE = """LI Document IQ (DocIQ)

  DocumentIQ.exe                    open the application
  DocumentIQ.exe --version          build, model and cold-start facts
  DocumentIQ.exe --selftest [...]   the full end-to-end gate (exit 0 = pass)
  DocumentIQ.exe --offline-probe    Principle 4: zero outbound attempts
  DocumentIQ.exe --help             this text
"""


def main(argv: list[str] | None = None) -> int:
    _bootstrap()
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--determinism-run":
        # Positional args are consumed by the runner via sys.argv, so shift
        # them into the shape the runner expects before handing over.
        sys.argv = [sys.argv[0]] + args[1:]
        return _cmd_determinism_run(args[1:])
    if args and args[0] in ("--help", "-h"):
        print(USAGE)
        return 0
    if args and args[0] == "--version":
        return _cmd_version()
    if args and args[0] == "--selftest":
        from dociq.selftest import main as selftest_main

        return selftest_main(args[1:])
    if args and args[0] == "--diagnose":
        return _cmd_diagnose()
    if args and args[0] == "--offline-probe":
        return _cmd_offline_probe(args[1:])
    if args and args[0].startswith("--"):
        print(f"unknown option {args[0]!r}\n\n{USAGE}", file=sys.stderr)
        return 2
    from dociq.gui.app import main as gui_main

    return gui_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
