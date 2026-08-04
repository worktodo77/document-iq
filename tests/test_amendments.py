"""The amendment registry is checked by the suite, not by remembering.

Codex review #2's answer to the question the Sprint-2 relay asked. Two
amendments made the gap between "raised" and "wired end-to-end" real in one
sprint — A-12, raised by two tracks and applied by neither, and A-14, applied
and shipped and then absent from the register a reviewer was pointed at. A
prose register cannot catch either: it cannot distinguish a declaration from a
wiring, and it cannot notice its own omission.

This runs the check in-process so it fails an ordinary test run, not only a CI
step somebody has to add.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_amendment_registry_is_consistent_with_the_code():
    out = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "check_amendments.py")],
        cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, (
        "amendment registry check failed:\n" + out.stdout + out.stderr)


def test_every_amendment_in_the_prose_register_has_a_machine_readable_entry():
    """The specific omission Codex found: A-14 existed in the seam, in three
    commits, in the decision register and in the relay — and not in the file the
    relay sent reviewers to. The two halves of the register must agree."""
    import re
    import tomllib

    prose = (ROOT / "docs" / "contracts" / "amendments.md").read_text(encoding="utf-8")
    with (ROOT / "docs" / "contracts" / "amendments.toml").open("rb") as fh:
        entries = set(tomllib.load(fh).get("amendment", {}))

    headings = set(re.findall(r"^## (A-\d{2}) —", prose, re.MULTILINE))
    missing = sorted(headings - entries)
    assert not missing, (
        f"amendments documented in prose with no machine-readable entry: "
        f"{missing} — the half that can be checked is the half that must be "
        f"complete")
