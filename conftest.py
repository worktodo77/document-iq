"""Make ``src/`` and the repo root importable without installing the package.

Sprint 1 runs three tracks in three worktrees against one frozen contract. A
``pip install -e .`` in any of them would put that worktree's ``dociq`` on the
interpreter's path for *all* of them, so a track could pass its tests against
another track's half-finished code. Path injection keeps each worktree's test
run reading only its own source.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
for candidate in (_ROOT / "src", _ROOT):
    text = str(candidate)
    if text not in sys.path:
        sys.path.insert(0, text)
