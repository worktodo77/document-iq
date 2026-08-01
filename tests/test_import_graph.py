"""The freeze's Track-C rule, made unbreakable.

``docs/contracts/pagemodel_freeze.md``: the GUI "may not import anything from
``ingest/``, ``identify/``, ``docid/``, ``profiles/``, ``emit/`` or ``verify/``.
The GUI orchestrates and displays; it holds no pipeline logic. Enforced by a test
that asserts the import graph."

Checked two ways on purpose. The static scan catches an import that a lazy code
path would hide from the runtime check; the runtime check catches an import
performed by a string (``importlib``) that the static scan cannot see.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
GUI = SRC / "dociq" / "gui"
BRANDING = SRC / "dociq" / "branding"

FORBIDDEN = ("ingest", "identify", "docid", "profiles", "emit", "verify")


def _module_names(path: Path) -> list[str]:
    """Every module named by an import statement in ``path``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # a relative import cannot reach another package
                continue
            if node.module:
                names.append(node.module)
                names.extend(f"{node.module}.{a.name}" for a in node.names)
    return names


@pytest.mark.parametrize("path", sorted(GUI.rglob("*.py")), ids=lambda p: p.name)
def test_gui_imports_no_pipeline_package(path: Path) -> None:
    for name in _module_names(path):
        for banned in FORBIDDEN:
            assert not name.startswith(f"dociq.{banned}"), (
                f"{path.name} imports {name}: the GUI may not depend on the "
                f"pipeline package '{banned}' (pagemodel freeze, Track C)"
            )


@pytest.mark.parametrize("path", sorted(BRANDING.rglob("*.py")), ids=lambda p: p.name)
def test_branding_is_toolkit_free(path: Path) -> None:
    """The generators must run without Qt.

    They are build tooling: the icon has to be generatable in a packaging step
    that has Pillow and no display, and the GUI has to be able to load the
    palette before a QApplication exists.
    """
    for name in _module_names(path):
        assert not name.startswith("PySide6"), (
            f"{path.name} imports {name}: the brand generators must stay "
            "toolkit-free so they run in a headless build step"
        )


def test_importing_the_whole_gui_pulls_in_no_pipeline_module() -> None:
    """A subprocess, because the check is about what is in ``sys.modules`` and
    the test session has already imported plenty."""
    code = (
        "import importlib, sys\n"
        "for m in ('dociq.gui.app', 'dociq.gui.main_window', 'dociq.gui.screens',\n"
        "          'dociq.gui.widgets', 'dociq.gui.mock_pipeline',\n"
        "          'dociq.gui.view_models', 'dociq.branding.make_icon',\n"
        "          'dociq.branding.make_logo'):\n"
        "    importlib.import_module(m)\n"
        f"banned = [m for m in sys.modules if any(m.startswith('dociq.' + b) for b in {FORBIDDEN!r})]\n"
        "print(','.join(sorted(banned)))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, cwd=str(SRC.parent),
        env={**_env(), "QT_QPA_PLATFORM": "offscreen"},
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "", f"pipeline modules imported: {out.stdout}"


def _env() -> dict[str, str]:
    import os

    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    return env
