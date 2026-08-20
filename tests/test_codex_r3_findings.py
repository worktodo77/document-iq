"""Codex round 3 on Sprint 4 — both blockers, as executable statements.

Both are mine, and both were introduced by the fixes for the previous round.

**A-R3-1** is the sharper lesson. `run()` already derives an effective OCR state
at line 1265 — `(opts.walk or walker.WalkOptions()).ocr_enabled` — because
`PipelineOptions.walk` is optional and `run(config)` constructs `PipelineOptions()`
itself. My A-23 wiring, two hundred lines below that, read `opts.walk.ocr_enabled`
directly. The suite stayed green because every completed-run case in it supplies
explicit walk options, so nothing exercised the function's own documented default.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from dociq import pipeline as core  # noqa: E402
from dociq.contracts import RunConfig, matter_key  # noqa: E402
from dociq.gui.main_window import MainWindow  # noqa: E402
from dociq.gui.mock_pipeline import MockPipeline  # noqa: E402
from dociq.sections.model import ApprovedOmission  # noqa: E402
from dociq.sections.templates import PROGRESS_REPORT  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])


def test_a_r3_1_the_documented_default_invocation_completes():
    """`run(config)` with no options at all — the signature's own default.

    Not a contrived call: `options` is declared optional and the function builds
    its own `PipelineOptions()`. A crash here is a crash in the public core API's
    simplest use.
    """
    out = pathlib.Path(tempfile.mkdtemp(prefix="r3-default-"))
    try:
        outcome = core.run(
            RunConfig(source_root=str(FIXTURES), output_root=str(out)))
        assert outcome.result.documents, "the default run produced no documents"
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_a_r3_1_the_default_path_also_completes_with_an_approval():
    """Stage 4 is where it crashed, so the approval path is the one to drive:
    without an approval the fingerprint is computed and never compared."""
    out = pathlib.Path(tempfile.mkdtemp(prefix="r3-default-appr-"))
    try:
        approval = ApprovedOmission(
            family_id="progress-photographs", approved_by="abachowski",
            approved_at="2026-08-19T12:00:00Z", matter="fixtures",
            matter_root=matter_key(str(FIXTURES)),
            template_id=PROGRESS_REPORT.template_id,
            template_version=PROGRESS_REPORT.version,
        )
        outcome = core.run(
            RunConfig(source_root=str(FIXTURES), output_root=str(out)),
            core.PipelineOptions(template=PROGRESS_REPORT,
                                 approvals=(approval,),
                                 matter_name="fixtures"),
        )
        assert outcome.result.documents
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_a_r3_2_withdrawing_the_last_approval_clears_the_message(app):
    """The empty case returned early without clearing what it had written, so
    the screen went on claiming an approval after the collection was empty."""
    window = MainWindow(MockPipeline())
    try:
        window.setup.set_retained_scopes((("MV32",),))
        window.setup._tokens.setText("MV32")
        assert "still apply" in window.setup._tokens_hint.text()

        window.setup.set_retained_scopes(())
        hint = window.setup._tokens_hint.text()
        assert "approval" not in hint.lower(), hint
    finally:
        window.close()


def test_a_r3_2_the_stale_message_is_cleared_from_either_prior_state(app):
    """It goes stale the same way whether the previous message said the
    approvals still applied or that they no longer did."""
    window = MainWindow(MockPipeline())
    try:
        window.setup.set_retained_scopes((("MV32",),))
        window.setup._tokens.setText("BOMESC")
        assert "NO LONGER APPLY" in window.setup._tokens_hint.text()

        window.setup.set_retained_scopes(())
        assert "approval" not in window.setup._tokens_hint.text().lower()
    finally:
        window.close()


def test_a_r3_2_clearing_approvals_does_not_erase_the_proposal_guidance(app):
    """The required direction's second half: removing the retained-approval
    status must not take the token-proposal hint with it. They share one label,
    which is why the naive fix — blank it — would be wrong."""
    window = MainWindow(MockPipeline())
    try:
        window.setup.set_proposed_tokens(())
        proposal_hint = window.setup._tokens_hint.text()
        assert "No project names found" in proposal_hint

        window.setup.set_retained_scopes((("MV32",),))
        assert "approval" in window.setup._tokens_hint.text().lower()

        window.setup.set_retained_scopes(())
        assert window.setup._tokens_hint.text() == proposal_hint
    finally:
        window.close()


# --- the gate question, ruled by Alex 2026-08-19 ----------------------------

def _apply(approval_fp: str, run_fp: str) -> tuple[int, tuple[str, ...]]:
    """(drops, warnings) for one approval under one run fingerprint."""
    import dataclasses

    from dociq.contracts import RecognitionTier  # noqa: PLC0415
    from dociq.sections.apply import apply_sections  # noqa: PLC0415
    from dociq.sections.model import SectionSpan  # noqa: PLC0415
    from dociq.sections.normalize import family_key  # noqa: PLC0415
    from tests.test_codex_r1_findings import _document  # noqa: PLC0415

    label = "TABLE OF CONTENTS"
    approval = ApprovedOmission(
        family_id="table-of-contents", approved_by="abachowski",
        approved_at="2026-08-19T12:00:00Z", matter="fixtures",
        matter_root=matter_key(str(FIXTURES)),
        template_id=PROGRESS_REPORT.template_id,
        template_version=PROGRESS_REPORT.version,
        recognition=approval_fp,
    )
    span = SectionSpan(label, family_key(label, ()), RecognitionTier.OUTLINE,
                       1, 1, "the outline")
    out = apply_sections(
        _document(), (span,), template=PROGRESS_REPORT, approvals=(approval,),
        matter_root=approval.matter_root, project_tokens=(),
        recognition=run_fp)
    return len(out.drops), out.warnings


def test_an_approval_with_a_fingerprint_is_refused_when_the_run_states_none():
    """Alex's ruling on Codex's gate question.

    The approval asserts it was reviewed under a particular recognition
    configuration. A run that cannot produce a fingerprint cannot show it
    matches, and the safe reading of "cannot show" is "does not".
    """
    drops, warnings = _apply(approval_fp="a-recorded-fingerprint", run_fp="")
    assert drops == 0
    assert any("states none" in w for w in warnings), warnings


def test_an_approval_without_a_fingerprint_still_falls_back_to_named_fields():
    """The asymmetry is the point. An approval given before contract 2.2.0
    carries no fingerprint, and voiding it would discard real expert work for a
    field that did not exist when it was given."""
    drops, _ = _apply(approval_fp="", run_fp="a-fingerprint-it-never-saw")
    assert drops == 1
