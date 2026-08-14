"""Fail when an amendment is referenced but absent, or declared but not wired.

Codex review #2's answer to the question the Sprint-2 relay asked: *is there a
structural check for the gap between "amendment raised" and "amendment wired
end-to-end" that does not depend on someone remembering?*

Two amendments made that gap real in one sprint, and the second one proved the
first was not a fluke:

* **A-12** was raised by Track D *and* Track E and applied by neither. Both
  behaved correctly — refused to work around a frozen seam, documented the gap,
  moved on — and the product shipped Path A's button permanently disabled
  behind its own polite explanation.
* **A-14** was raised, applied to the seam, wired, measured, and shipped — and
  then had no entry in ``amendments.md`` for a day, while the Codex relay
  pointed reviewers at that file to read it. Codex found it immediately.

A prose register cannot catch either. It cannot distinguish "raised" from
"wired", and it cannot notice its own omission. So the register gets a
machine-readable half, and this check fails the build on four conditions:

1. an amendment is **referenced** anywhere in the repo but has **no entry**;
2. an entry is marked APPLIED but its **seam symbols are absent** from the code
   it claims to have changed — declared, not wired;
3. an entry is marked APPLIED but names **no adopting commit**;
4. an entry claims tests that **do not exist**.

Run: ``python tools/check_amendments.py``. Exit 0 clean, 1 with the reasons.
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "docs" / "contracts" / "amendments.toml"
PROSE = ROOT / "docs" / "contracts" / "amendments.md"

# Where a reference to an amendment may legitimately appear. The prose register
# is excluded as a *source* of references — it is the thing being checked, and
# letting it satisfy its own requirement is the vacuous-probe failure this
# project keeps finding in its own tests.
SEARCH_ROOTS = ("src", "tests", "tools", "docs", "packaging")

REF = re.compile(r"\bA-(\d{2}[a-z]?)\b")
"""Amendment ids, INCLUDING a letter suffix.

The first version was ``\\bA-(\\d{2})\\b``, which cannot match ``A-11b``. Of the
fifteen amendments this repository has, exactly one carries a letter suffix —
and it was the one that turned out to be declared, documented, probe-covered and
**populated by nobody**: A-11b's ``rule`` and ``note``, the profile's verbatim
matching pattern and the expert's own stated reason for an omission, reached no
adapter and no screen, on the §6 checklist the amendment exists for.

So the single id this check could not see was the single id that needed seeing.
That is not luck. A pattern written from the ids you happen to remember will
have its blind spot exactly where the naming was irregular, and irregular naming
is what an entry gets when it was added as an afterthought — which is also what
makes it the one most likely to be half-applied.
"""


def _load_registry() -> dict[str, dict]:
    """Parse the TOML registry. tomllib is stdlib from 3.11 — no new dependency,
    which matters because D-11 declares the complete dependency set and a check
    that needed a package would be a check nobody could run offline."""
    import tomllib

    if not REGISTRY.exists():
        raise SystemExit(f"missing registry: {REGISTRY.relative_to(ROOT)}")
    with REGISTRY.open("rb") as fh:
        return tomllib.load(fh).get("amendment", {})


def _referenced() -> dict[str, set[str]]:
    """Every A-nn mentioned in the repo, and where."""
    found: dict[str, set[str]] = {}
    for root in SEARCH_ROOTS:
        base = ROOT / root
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".md", ".toml", ".spec"}:
                continue
            if path == PROSE or path == REGISTRY:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for m in REF.finditer(text):
                found.setdefault(f"A-{m.group(1)}", set()).add(
                    str(path.relative_to(ROOT)).replace("\\", "/"))
    return found


def _module_symbols(rel: str) -> set[str]:
    """Top-level names a module defines — classes, functions, assignments, and
    the methods of any class. Parsed, not grepped: a symbol named in a comment
    or a docstring is not a symbol that exists, and a check that accepted one
    would pass on exactly the documentation-only 'fix' it exists to reject."""
    path = ROOT / rel
    if not path.exists():
        return set()
    tree = ast.parse(path.read_text(encoding="utf-8"), rel)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            # A RE-EXPORTED name is a name the module provides. `runstate`
            # imports TerminalStatus from `contracts` and lists it in __all__;
            # asking "does runstate define it" answered no and reported a wired
            # amendment as unwired. The question the check means to ask is
            # whether the module OFFERS the symbol, not where it was typed.
            for alias in node.names:
                if alias.name != "*":
                    names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


SYMBOLIC = {"HEAD", "ORIG_HEAD", "FETCH_HEAD", "MERGE_HEAD", "CHERRY_PICK_HEAD"}
"""Refs whose meaning MOVES. Rejected outright as an adopting commit.

``adopted_in = "HEAD"`` shipped as a placeholder and passed this check for a
whole fix round, because ``git cat-file -t HEAD`` always names a commit. So the
registry recorded "whatever is checked out" as the immutable fact of which
commit adopted an amendment, and moving the branch changed what the record
meant without changing the record. Codex filed it as D-2.
"""


def _commit_problem(sha: str) -> str:
    """Empty string when the value is a usable historical commit id."""
    if not sha:
        return "names no adopting commit"
    if sha in SYMBOLIC or sha.startswith(("HEAD~", "HEAD^", "@")):
        return (f"records the SYMBOLIC ref {sha!r} as its adopting commit — that "
                f"names whatever is checked out, so the record changes meaning "
                f"when the branch moves. Use the immutable commit id")
    if not re.fullmatch(r"[0-9a-f]{7,40}", sha):
        return f"records {sha!r}, which is not a commit id"
    try:
        out = subprocess.run(
            ["git", "cat-file", "-t", sha], cwd=ROOT,
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        # Git unavailable. Reported rather than silently passed: this check's
        # own subject matter is claims that go unverified, and "could not
        # verify" reported as "verified" is the defect it polices.
        print(f"  note: git unavailable — {sha} not verified to exist")
        return ""
    if out.returncode != 0 or out.stdout.strip() != "commit":
        return f"names adopting commit {sha}, which does not exist"
    return ""


def main() -> int:
    entries = _load_registry()
    problems: list[str] = []

    # 1. Referenced but absent — the A-14 case.
    for ident, where in sorted(_referenced().items()):
        if ident not in entries:
            problems.append(
                f"{ident} is referenced in {', '.join(sorted(where))} but has no "
                f"registry entry. An amendment a reader is pointed at must exist "
                f"in the place they are pointed to.")

    for ident, entry in sorted(entries.items()):
        status = entry.get("status", "")
        if status != "applied":
            continue

        # 2. Declared but not wired — the A-12 case.
        for target, symbols in (entry.get("wired_in") or {}).items():
            present = _module_symbols(target)
            missing = [s for s in symbols if s not in present]
            if missing:
                problems.append(
                    f"{ident} is marked APPLIED and claims {target} defines "
                    f"{', '.join(missing)} — absent. An amendment declared on "
                    f"the seam but not wired through leaves the product doing "
                    f"nothing while every file reads as correct.")

        # 3. Applied with no usable adopting commit.
        trouble = _commit_problem(entry.get("adopted_in", ""))
        if trouble:
            problems.append(f"{ident} is marked APPLIED but {trouble}.")

        # 4. Claims tests that are not there.
        for rel in entry.get("tests") or []:
            if not (ROOT / rel).exists():
                problems.append(f"{ident} claims test file {rel}, which does not exist.")

    if problems:
        print("amendment registry check FAILED:\n")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"amendment registry OK — {len(entries)} entries, all applied ones wired.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
