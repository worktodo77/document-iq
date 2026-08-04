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

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def real_run(tmp_path_factory):
    """ONE real end-to-end run through :class:`dociq.adapter.RealPipeline`.

    Shared rather than per-module so that ``test_adapter``, the seam-population
    probe and the GUI's rendered-state assertions all read the SAME run. Two
    separately built runs could disagree about what the pipeline produced, and
    a probe that proves a field is populated in its own run while a screen reads
    a different one proves nothing about the product — which is the shape of
    Codex review #2's B-3.

    OCR is off: every assertion drawn from it is about the seam and the
    presentation records, and ``tests/test_ocr_ordering.py`` owns OCR.
    """
    from dociq import adapter
    from dociq.gui.pipeline import RunRequest

    out = tmp_path_factory.mktemp("adapter")
    pipe = adapter.RealPipeline(ocr_enabled=False)
    events: list = []
    outcome = pipe.run(RunRequest(str(FIXTURES), str(out / "matter")),
                       events.append, lambda: False)
    return outcome, events, out / "matter"
