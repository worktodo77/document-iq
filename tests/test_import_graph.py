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

FORBIDDEN = ("ingest", "identify", "docid", "profiles", "sections", "emit",
             "verify")
"""The freeze names five packages; ``sections`` is the sixth and it did not
exist when the rule was written.

Added by enumerating what the rule is FOR rather than what it lists. Every name
here is a pipeline package the GUI must not reach into, and ``dociq.sections``
is one — it holds the tier resolvers and ``apply_sections``, the function that
decides whether a page is dropped. A rule that enumerates the packages present
on the day it was written has its blind spot exactly where the codebase grew,
which is the same shape as the ``A-11b`` reference pattern that could not match
the one amendment nobody had checked.

The GUI reaches an approval through :class:`dociq.gui.pipeline.OmissionApproval`,
a seam record the adapter converts. That indirection is the rule being obeyed,
not worked around."""


def _package_of(path: Path) -> str:
    """The dotted package a module inside ``src/`` belongs to.

    ``src/dociq/gui/widgets.py`` → ``dociq.gui``; ``src/dociq/gui/__init__.py``
    → ``dociq.gui``. Needed to resolve relative imports, which is the whole
    reason this function exists.
    """
    parts = list(path.relative_to(SRC).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    elif parts:
        parts.pop()
    return ".".join(parts)


def _resolve_relative(path: Path, level: int, module: str | None) -> str:
    """``from ..ingest import dating`` in ``dociq/gui/`` → ``dociq.ingest``.

    **The premise this replaces was false.** The check used to skip every
    relative import outright, on the stated reasoning that "a relative import
    cannot reach another package". It plainly can: one extra dot walks up out of
    ``dociq.gui`` and straight into ``dociq.ingest``, and the freeze's whole
    rule is about which ``dociq`` sub-packages the GUI may reach. Reproduced by
    appending such a function to ``gui/widgets.py``: all fourteen tests passed.

    The runtime subprocess check could not have covered it either — it *imports*
    GUI modules and never calls their functions, so an import deferred inside a
    function body is invisible to it. The static scan is the only check that can
    see this, which is why it may not have a hole in it.
    """
    parts = _package_of(path).split(".") if _package_of(path) else []
    # level 1 is "this package"; each further dot walks one package up.
    up = level - 1
    base = parts[:len(parts) - up] if up else parts
    if up and up > len(parts):  # walked past the top; nothing resolvable
        return ""
    return ".".join([*base, module] if module else base)


def _module_names(path: Path, source: str | None = None) -> list[str]:
    """Every module named by an import statement in ``path``, ABSOLUTE.

    Relative imports are resolved rather than skipped — see
    :func:`_resolve_relative`. ``source`` overrides what is read from disk, so
    the scan can be exercised against a module body without writing one into
    ``src/``.
    """
    if source is None:
        source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                resolved = _resolve_relative(path, node.level, node.module)
                if not resolved:
                    continue
                names.append(resolved)
                names.extend(f"{resolved}.{a.name}" for a in node.names)
            elif node.module:
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


def test_the_contract_imports_no_pipeline_package() -> None:
    """`contracts.py` states this rule about itself and nothing enforced it.

    `OmissionSnapshot`'s docstring: "dociq.sections imports the contract, so the
    contract cannot import it back." D-39's first draft put `canonical_tokens`
    in `dociq.sections.project_tokens` and reached back for it from
    `RunConfig.__post_init__`. Deferred inside the method, so no import cycle
    fired at load and every test passed — the rule was broken all the same.

    `_module_names` walks the whole AST, so a function-level import is caught
    exactly like a module-level one. That is the point: the version that broke
    this was function-level, and a check that only read the file's header would
    have agreed with it.
    """
    path = SRC / "dociq" / "contracts.py"
    for name in _module_names(path):
        for banned in FORBIDDEN:
            assert not name.startswith(f"dociq.{banned}"), (
                f"contracts.py imports {name}: the contract is what the pipeline "
                f"packages import, so it cannot import '{banned}' back "
                f"(OmissionSnapshot, and the pagemodel freeze)"
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


def _gui_modules() -> tuple[str, ...]:
    """Every module under ``dociq/gui/``, discovered rather than listed.

    The hard-coded list this replaces named eight modules and had gone stale:
    ``dociq.gui.pipeline`` and ``dociq.gui.theme`` were both absent, so the
    runtime half of the check silently did not cover the seam module itself.
    A list that has to be maintained by hand is a list that stops being true.
    """
    names = []
    for path in sorted(GUI.rglob("*.py")):
        parts = list(path.relative_to(SRC).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        names.append(".".join(parts))
    return tuple(n for n in names if n)


def test_the_runtime_check_covers_every_gui_module() -> None:
    """The list below is derived; this asserts it actually reaches the modules
    whose absence was the defect."""
    mods = _gui_modules()
    assert "dociq.gui.pipeline" in mods and "dociq.gui.theme" in mods
    assert len(mods) >= 8


def test_importing_the_whole_gui_pulls_in_no_pipeline_module() -> None:
    """A subprocess, because the check is about what is in ``sys.modules`` and
    the test session has already imported plenty.

    **What this check cannot do**, stated because the static scan above is what
    actually carries the rule: it only *imports* these modules. An import
    performed inside a function body — the deferred form the GUI uses all over —
    never runs here, so a lazy `from ..ingest import dating` is invisible to it.
    Its distinct value is the opposite case: an import performed by a string
    through ``importlib``, which no AST scan can see.
    """
    modules = _gui_modules() + ("dociq.branding.make_icon",
                                "dociq.branding.make_logo")
    code = (
        "import importlib, sys\n"
        f"for m in {modules!r}:\n"
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


# --- B3: the scan must SEE a relative import ------------------------------
#
# ``if node.level: continue  # a relative import cannot reach another package``
# — the comment's premise is false, and the skip it justified was the only hole
# either half of this check had. Reproduced by appending the function below to
# ``gui/widgets.py``: all fourteen tests passed.


RELATIVE_REACH = '''
def dates():
    from ..ingest import dating
    return dating
'''


def test_the_scan_sees_a_relative_import_reaching_a_pipeline_package() -> None:
    """FAIL-BEFORE: returns ``[]``, and the GUI rule is unenforced for every
    relative import in the tree."""
    names = _module_names(GUI / "widgets.py", RELATIVE_REACH)
    assert "dociq.ingest" in names, names
    assert "dociq.ingest.dating" in names


def test_the_rule_itself_rejects_that_module() -> None:
    """The scan seeing it is worth nothing unless the assertion then fires."""
    with pytest.raises(AssertionError):
        for name in _module_names(GUI / "widgets.py", RELATIVE_REACH):
            for banned in FORBIDDEN:
                assert not name.startswith(f"dociq.{banned}"), name


@pytest.mark.parametrize("level,module,expected", [
    (1, "widgets", "dociq.gui.widgets"),      # from .widgets import X
    (1, None, "dociq.gui"),                   # from . import widgets
    (2, "ingest", "dociq.ingest"),            # from ..ingest import dating
    (2, None, "dociq"),                       # from .. import contracts
    (2, "ingest.extract", "dociq.ingest.extract"),
])
def test_relative_imports_resolve_to_the_module_they_actually_reach(
        level, module, expected) -> None:
    assert _resolve_relative(GUI / "widgets.py", level, module) == expected


def test_a_relative_import_inside_a_function_is_still_seen() -> None:
    """``ast.walk`` covers nested bodies, and it must: the GUI defers imports
    inside functions all over, and the runtime subprocess check never calls a
    function, so a deferred import is visible to nothing else."""
    nested = '''
class W:
    def go(self):
        if True:
            from ..verify import tokens
            return tokens
'''
    names = _module_names(GUI / "widgets.py", nested)
    assert "dociq.verify" in names, names


def test_no_module_references_a_name_it_never_defined_or_imported() -> None:
    """Undefined names, caught at collection rather than at the call site.

    `compileall` accepts a `NameError` that only fires when a branch runs, and
    three times in this sprint a patch script "added" an import by matching a
    spelling the file does not use, printed success, and changed nothing. Each
    time the full suite caught it and the targeted runs did not — because the
    failing line is inside a method nobody had reason to call yet.

    A module-level scan is enough to catch that class: it compares every bare
    name a module reads against what it defines, imports or gets from builtins.
    Deliberately conservative — attribute access, locals and comprehension
    targets are not flagged — because a check that cries wolf gets deleted.
    """
    import ast
    import builtins

    # Module dunders are bound by the import machinery, not by builtins.
    known_builtins = set(dir(builtins)) | {
        "__file__", "__name__", "__doc__", "__package__", "__spec__",
        "__loader__", "__builtins__", "__debug__",
    }
    offenders: list[str] = []

    for path in sorted((SRC / "dociq").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        defined: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                defined.update(a.asname or a.name.split(".")[0]
                               for a in node.names)
            elif isinstance(node, ast.ClassDef):
                defined.add(node.name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defined.add(node.name)
                args = node.args
                defined.update(a.arg for a in
                               (*args.posonlyargs, *args.args, *args.kwonlyargs))
                if args.vararg:
                    defined.add(args.vararg.arg)
                if args.kwarg:
                    defined.add(args.kwarg.arg)
            elif isinstance(node, ast.Lambda):
                args = node.args
                defined.update(a.arg for a in
                               (*args.posonlyargs, *args.args, *args.kwonlyargs))
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                defined.add(node.id)
            elif isinstance(node, (ast.comprehension,)):
                for t in ast.walk(node.target):
                    if isinstance(t, ast.Name):
                        defined.add(t.id)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                defined.add(node.name)
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    if item.optional_vars is not None:
                        for t in ast.walk(item.optional_vars):
                            if isinstance(t, ast.Name):
                                defined.add(t.id)
            elif isinstance(node, ast.Global):
                defined.update(node.names)

        for node in ast.walk(tree):
            if (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
                    and node.id not in defined
                    and node.id not in known_builtins):
                offenders.append(
                    f"{path.relative_to(SRC)}:{node.lineno}: {node.id}")

    assert not offenders, (
        "names read but never defined, imported or built in:\n  "
        + "\n  ".join(offenders))
