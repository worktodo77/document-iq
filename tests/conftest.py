"""Shared fixture-corpus setup.

The corpus is generated, not committed (see ``make_fixtures``), so it is built
once per session here. Building is idempotent and cheap next to the OCR the
tests then run over it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))

import make_fixtures  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]

FIXTURES = make_fixtures.build()
"""The generated matter root — ``tests/fixtures/generated/matter``."""
