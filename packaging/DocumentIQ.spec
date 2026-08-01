# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for LI Document IQ — the D-22 one-folder build.

Build it with the documented driver, never by hand:

    PYTHONPATH=src python packaging/build.py

The driver exists because "reproducible from a committed spec file plus a
documented command" is the requirement, and a spec alone is not reproducible:
PyInstaller resolves `--distpath`, `--workpath` and the clean flag from the
command line, and a build assembled from shell history is a build nobody else
can repeat.

WHY --onedir AND NOT --onefile  (D-22, Alex 2026-08-01)
The bundled ONNX OCR models are ~13.7 MB of weights on top of onnxruntime,
OpenCV, PySide6 and PyMuPDF; the payload clears 100 MB. `--onefile` re-extracts
all of it to a temp directory on EVERY launch — multi-second cold starts, and a
temp-extract-then-execute pattern that endpoint protection on a locked-down
law-firm machine quarantines. A tool that does not open at a client site has no
other qualities. The zip preserves the intent (one thing to hand over) without
the failure mode.

TWO EXECUTABLES, ONE PAYLOAD
`DocumentIQ.exe` is windowed: a console flashing behind a law-firm desktop app
is not shippable. But a windowed exe has no stdout, so it cannot be *verified* —
and it cannot answer the question a client's IT reviewer actually asks, which is
"prove it makes no outbound calls". `DocumentIQ-cli.exe` is the same application
with a console, sharing the identical Analysis and the identical payload (a
second EXE() over one Analysis costs a bootloader, ~1 MB, not a second copy of
the runtime). `DocumentIQ-cli.exe --offline-probe` is the artifact that
discharges §10's "must be verifiable" clause on the client's own machine.

WHAT IS BUNDLED AND WHY EACH ENTRY IS LOAD-BEARING
- the three ONNX models, so OCR NEVER fetches (Principle 4). Not a convenience:
  the vendored MIP 3.9 extractor's `enable_os_trust()` existed precisely to let
  a one-time model download through a corporate proxy, and closing that path
  means the weights must already be here.
- rapidocr's own `config.yaml` files. The library reads them at construction
  even though DocIQ passes explicit model paths, and their absence is an
  import-time failure that only appears in the frozen build.
- `assets/branding`, for the D-08 icon, the D-09 window lockup, and — less
  obviously — `li_monogram_source.png`, which `dociq.branding.palette` SAMPLES
  the brand colors from at import. Omit it and the packaged GUI raises before it
  draws anything.
- the fixture builder, as an importable module, so `--selftest` runs on the
  shipped artifact rather than only on a checkout.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

SPEC_DIR = Path(SPECPATH).resolve()
REPO = SPEC_DIR.parent
SRC = REPO / "src"

ICON = REPO / "assets" / "branding" / "li_dociq_icon.ico"
if not ICON.is_file():  # fail at build time, not at a client's desktop
    raise SystemExit(f"D-08 icon missing: {ICON}")

# rapidocr ships its models and per-stage config.yaml inside the package;
# collect_data_files picks up every non-.py file, which is exactly the set.
_rapidocr_data = collect_data_files("rapidocr_onnxruntime", include_py_files=False)
_model_files = [d for d in _rapidocr_data if d[0].endswith(".onnx")]
if len(_model_files) != 3:
    # "The corpus doesn't exercise it selects nothing": a build that silently
    # shipped two of three models would OCR nothing and blame the corpus.
    raise SystemExit(
        f"expected 3 bundled ONNX models, collected {len(_model_files)}: "
        f"{[Path(p).name for p, _ in _model_files]}"
    )

datas = list(_rapidocr_data)
datas += [(str(p), "assets/branding")
          for p in (REPO / "assets" / "branding").iterdir() if p.is_file()]
# The fixture builder is bundled as a MODULE, not as a data file. A .py file
# copied into the bundle as data is not importable: frozen imports come out of
# the PYZ archive, and the loader never looks at loose files. That mistake fails
# only in the packaged build (`ModuleNotFoundError: no module named ...` from
# --selftest), which is precisely the class of defect that reaches a client
# because the source tree is green. It goes in via `pathex` + `hiddenimports`.
FIXTURES = REPO / "tests" / "fixtures"
if not (FIXTURES / "make_fixtures.py").is_file():
    raise SystemExit(f"fixture builder missing: {FIXTURES / 'make_fixtures.py'}")

# Qt is the largest single contributor and DocIQ imports exactly three modules
# of it (QtCore, QtGui, QtWidgets — grepped, not assumed). Everything else is
# excluded by name: WebEngine alone is ~150 MB and would double the zip.
_PYSIDE6_UNUSED = [
    "PySide6.Qt3DAnimation", "PySide6.Qt3DCore", "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput", "PySide6.Qt3DLogic", "PySide6.Qt3DRender",
    "PySide6.QtBluetooth", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtHttpServer",
    "PySide6.QtLocation", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
    "PySide6.QtNetwork", "PySide6.QtNetworkAuth", "PySide6.QtNfc",
    "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtPdf",
    "PySide6.QtPdfWidgets", "PySide6.QtPositioning", "PySide6.QtQml",
    "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickControls2",
    "PySide6.QtQuickWidgets", "PySide6.QtRemoteObjects", "PySide6.QtScxml",
    "PySide6.QtSensors", "PySide6.QtSerialBus", "PySide6.QtSerialPort",
    "PySide6.QtSpatialAudio", "PySide6.QtSql", "PySide6.QtStateMachine",
    "PySide6.QtSvg", "PySide6.QtSvgWidgets", "PySide6.QtTest",
    "PySide6.QtTextToSpeech", "PySide6.QtUiTools", "PySide6.QtWebChannel",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebEngineWidgets", "PySide6.QtWebSockets",
]

excludes = _PYSIDE6_UNUSED + [
    # Other GUI toolkits. PyQt5/6 and PySide2 would each drag a second Qt in.
    "tkinter", "PyQt5", "PyQt6", "PySide2",
    # Scientific stack DocIQ does not use; numpy is used and stays.
    "matplotlib", "pandas", "scipy", "IPython", "notebook", "sympy",
    # Test and build tooling has no business in a client deliverable.
    "pytest", "_pytest", "setuptools", "pip", "distutils",
]

a = Analysis(
    [str(SPEC_DIR / "dociq_launcher.py")],
    pathex=[str(SRC), str(FIXTURES)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        # Reached only through the launcher's argument routing, so the module
        # graph does not see them from the entry script alone.
        "make_fixtures",   # the bundled fixture corpus builder (--selftest)
        "dociq.selftest",
        "dociq.pipeline",
        "dociq.verify.offline",
        "dociq.verify.determinism",
        "dociq.gui.app",
        # Format backends behind try/except ImportError at their call sites —
        # the §11 audit's finding, and the reason pyproject declares them
        # explicitly. A module graph cannot see through a guarded import.
        "xlrd", "extract_msg", "pptx", "docx", "openpyxl", "reportlab", "yaml",
        "pypdf", "fitz", "cv2", "PIL.Image", "PIL.ExifTags",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(SPEC_DIR / "rthook_offline.py")],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe_gui = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DocumentIQ",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,           # UPX-packed binaries are a classic AV false positive,
    console=False,       # which is the same failure mode D-22 rejected onefile
    icon=str(ICON),      # for. Not worth the megabytes.
)

exe_cli = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DocumentIQ-cli",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    icon=str(ICON),
)

coll = COLLECT(
    exe_gui,
    exe_cli,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="DocumentIQ",
)
