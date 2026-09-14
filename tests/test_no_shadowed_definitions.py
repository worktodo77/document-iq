"""No module in ``src/dociq`` defines the same top-level name twice.

Sprint 5 built three packages in parallel branches. The Word package defined
``_local(el)``, taking an XML element, in ``ingest/extract.py``; the spreadsheet
package defined ``_local(tag)``, taking a tag string, in the same module. Each
branch was green alone. Merged, the later definition silently replaced the
earlier, and every Word file came out FAILED with no page, because Python lets a
module rebind a name without a word. Git reports no conflict for two additions
hundreds of lines apart. This test is the guard for that class: a second
top-level ``def``, ``class`` or constant assignment under a name the module
already bound.

Deliberate rebinding inside ``try``/``except ImportError`` or ``if`` blocks is
not top-level and is not flagged.
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "dociq"


def _top_level_bindings(tree: ast.Module) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append((node.name, node.lineno))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name) and t.id.isupper():
                    out.append((t.id, node.lineno))
    return out


def test_no_module_defines_a_top_level_name_twice():
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        seen: dict[str, int] = {}
        for name, line in _top_level_bindings(tree):
            if name in seen:
                offenders.append(f"{path.relative_to(SRC.parent)}: '{name}' at line "
                                 f"{seen[name]} is rebound at line {line}")
            else:
                seen[name] = line
    assert not offenders, ("a later definition silently replaces an earlier one:\n"
                           + "\n".join(offenders))
