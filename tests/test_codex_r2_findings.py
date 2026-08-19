"""Codex round 2 on Sprint 4 — the two blockers, as executable statements.

**B-R2-1 is the shape to learn from.** Contract 2.1.0 added
`OmissionSnapshot.project_tokens` and amendment A-22 stated it is "hashed like
every other field of the snapshot." Nothing populated it. The field existed, the
amendment claimed it, the tests exercised `apply_sections` directly — and the
one construction site that turns approvals into the persisted, hashed snapshot
silently took the default. Two runs, one dropping three pages and one dropping
none, shared a run identity: exactly the collision the amendment says it closes.

So the first test here is not about project tokens at all. It is the guard that
would have caught it and will catch the next one: **every field of the snapshot
must be populated from the approval, derived from the dataclass rather than from
a list somebody maintains.**
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
import re
import shutil
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from dociq import pipeline as core  # noqa: E402
from dociq.contracts import (  # noqa: E402
    Disposition,
    OmissionSnapshot,
    RunConfig,
    matter_key,
    run_identity,
)
from dociq.gui.main_window import MainWindow  # noqa: E402
from dociq.gui.mock_pipeline import MockPipeline  # noqa: E402
from dociq.ingest import extract as ex  # noqa: E402
from dociq.ingest import walker  # noqa: E402
from dociq.sections.model import ApprovedOmission  # noqa: E402
from dociq.sections.templates import PROGRESS_REPORT  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])


def _approval(project_tokens: tuple[str, ...] = ()) -> ApprovedOmission:
    """The one family this corpus exercises: approving it drops 3 pages."""
    return ApprovedOmission(
        family_id="progress-photographs", approved_by="abachowski",
        approved_at="2026-08-19T12:00:00Z", matter="fixtures",
        matter_root=matter_key(str(FIXTURES)),
        template_id=PROGRESS_REPORT.template_id,
        template_version=PROGRESS_REPORT.version,
        project_tokens=project_tokens,
    )


def _real_run(approval: ApprovedOmission):
    """A real pipeline run. Returns (dropped, effective config)."""
    out = pathlib.Path(tempfile.mkdtemp(prefix="r2-"))
    try:
        config = RunConfig(source_root=str(FIXTURES), output_root=str(out),
                           ocr_engine_version=ex.ocr_engine_version())
        outcome = core.run(config, core.PipelineOptions(
            walk=walker.WalkOptions(ocr_enabled=False, resume=False),
            template=PROGRESS_REPORT, approvals=(approval,),
            matter_name="fixtures"))
        pages = [p for d in outcome.result.documents for p in d.pages]
        dropped = sum(1 for p in pages if p.disposition is not Disposition.KEEP)
        return dropped, outcome.result.config
    finally:
        shutil.rmtree(out, ignore_errors=True)


# --- the class, not the instance -------------------------------------------

def test_every_snapshot_field_is_populated_from_the_approval():
    """The guard that would have caught B-R2-1, derived rather than listed.

    A hand-written list of fields to check has to be maintained by the same
    change that adds a field — which is precisely the change that forgets. This
    reads the dataclass and the construction site, so a field added tomorrow and
    left unpopulated fails here tomorrow.
    """
    source = (Path(__file__).resolve().parents[1]
              / "src" / "dociq" / "pipeline.py").read_text(encoding="utf-8")
    block = source[source.index("OmissionSnapshot("):]
    block = block[:block.index("for a in opts.approvals")]
    snapshot_fields = {f.name for f in dataclasses.fields(OmissionSnapshot)}
    approval_fields = {f.name for f in dataclasses.fields(ApprovedOmission)}
    # A field counts as populated when it is BOTH passed as a keyword and fed
    # from the approval. Matching only `name=a.name` would miss a value that is
    # normalized on the way through — `project_tokens=canonical_tokens(a.
    # project_tokens)` is the shipped form — and a guard that misses the real
    # spelling of the fix is a guard that passes for the wrong reason.
    populated = {
        f for f in snapshot_fields & approval_fields
        if re.search(rf"{f}\s*=", block) and f"a.{f}" in block
    }
    missing = (snapshot_fields & approval_fields) - populated
    assert not missing, (
        f"{sorted(missing)} exist on both OmissionSnapshot and ApprovedOmission "
        "but are not copied at the one site that builds the persisted, hashed "
        "snapshot — they silently take their defaults, and the run identity "
        "stops covering what the amendment says it covers")


# --- B-R2-1 ----------------------------------------------------------------

def test_b_r2_1_applied_and_refused_scopes_do_not_share_a_run_identity():
    """A real pipeline run, not a direct `apply_sections` call.

    That distinction is the finding: `apply_sections` was tested and correct,
    while the code that persists the approval into the hashed configuration was
    not, so the defect lived in the gap between them.
    """
    applied_drops, applied_config = _real_run(_approval(()))
    refused_drops, refused_config = _real_run(_approval(("MV32",)))

    assert applied_drops == 3, applied_drops
    assert refused_drops == 0, refused_drops
    assert applied_config.omissions[0].project_tokens == ()
    assert refused_config.omissions[0].project_tokens == ("MV32",)
    assert run_identity(applied_config) != run_identity(refused_config), (
        "two runs that dropped a different number of pages share one run "
        "identity — the persisted evidence says they had the same input")


def test_b_r2_1_behaviorally_identical_spellings_still_mint_one_identity():
    """The other direction, retained as the review asked. Canonicalization must
    not be lost by persisting the scope: `("bomesc","mv32")` and
    `("MV32","BOMESC")` are one review and must stay one identity."""
    _, a = _real_run(_approval(("bomesc", "mv32")))
    _, b = _real_run(_approval(("MV32", "BOMESC")))
    assert a.omissions[0].project_tokens == b.omissions[0].project_tokens
    assert run_identity(a) == run_identity(b)


# --- A-R2-1 ----------------------------------------------------------------

def _window_with(approvals):
    window = MainWindow(MockPipeline())
    window._approvals = tuple(approvals)
    window._publish_retained_approvals()
    return window


def test_a_r2_1_a_reordered_or_recased_edit_does_not_warn(app):
    """Stage 4 considers these sets equivalent and applies the approval. The
    screen warned that the pages would be kept and need re-review, immediately
    before the run dropped them under that very approval."""
    window = _window_with([_approval(("BOMESC", "MV32"))])
    try:
        window.setup._tokens.setText("MV32, BOMESC")
        hint = window.setup._tokens_hint.text()
        assert "NO LONGER APPLY" not in hint, hint
        assert "still apply" in hint, hint

        window.setup._tokens.setText("  mv32 ,BOMESC ")
        assert "NO LONGER APPLY" not in window.setup._tokens_hint.text()
    finally:
        window.close()


def test_a_r2_1_a_genuine_change_still_warns(app):
    """The guard must still fire for a real difference."""
    window = _window_with([_approval(("MV32",))])
    try:
        window.setup._tokens.setText("MV32, BOMESC")
        assert "NO LONGER APPLY" in window.setup._tokens_hint.text()
    finally:
        window.close()


def test_a_r2_1_a_mixed_scope_collection_is_counted_per_scope(app):
    """An expert approves under A, edits to B, approves another family under B.
    The retained set now holds two scopes. Reporting only the first approval's
    scope described all of them as one, so the message said every approval
    applies or none does — decided by insertion order."""
    window = _window_with([_approval(("MV32",)),
                           _approval(("MV32", "BOMESC"))])
    try:
        window.setup._tokens.setText("MV32, BOMESC")
        hint = window.setup._tokens_hint.text()
        # One of the two applies under this field; one does not. The message
        # must not claim both, in either direction.
        assert "1" in hint, hint
        assert "2 approval(s) ... NO LONGER APPLY" not in hint
        assert "2 approval(s) carried" not in hint
    finally:
        window.close()


# --- the recognition fingerprint (Alex's ruling, 2026-08-19) ----------------

def test_the_fingerprint_covers_the_sibling_the_named_fields_did_not():
    """Whether OCR ran changes which family a page lands in.

    Measured: a photographed schedule table classifies as `Photograph / figure
    page` unread and as `Schedule / activity table` once OCR recovers its grid.
    An unchanged approval for progress photographs drops it in one run and keeps
    it in the other, and no named scope field on the approval covered that.
    """
    from dociq.contracts import recognition_fingerprint  # noqa: PLC0415

    on = recognition_fingerprint(project_tokens=("MV32",), template_id="t",
                                 template_version="1", ocr_ran=True)
    off = recognition_fingerprint(project_tokens=("MV32",), template_id="t",
                                  template_version="1", ocr_ran=False)
    assert on != off


def test_the_fingerprint_is_stable_across_spellings_that_do_not_change_behavior():
    """It must not become a second way to invalidate an approval that Stage 4
    would have applied — that was Codex A-R2-1, one layer down."""
    from dociq.contracts import recognition_fingerprint  # noqa: PLC0415

    a = recognition_fingerprint(project_tokens=("MV32", "BOMESC"),
                                template_id="t", template_version="1")
    b = recognition_fingerprint(project_tokens=(" bomesc ", "mv32", "MV32"),
                                template_id="t", template_version="1")
    assert a == b


def test_the_fingerprint_parts_cannot_be_confused_with_each_other():
    """Joined on a separator no part can contain. `("A","B|C")` and `("A|B","C")`
    are different reviews and must not share a fingerprint."""
    from dociq.contracts import recognition_fingerprint  # noqa: PLC0415

    # Tokens cannot collide: `fold_label` collapses every non-alphanumeric to a
    # space, so no token can contain a separator at all. The template fields
    # can, and this pair collides under ANY printable separator — `a|b` + `""`
    # and `a` + `"b"` both render as `a|b`. It passes only because the parts are
    # joined on a unit separator (0x1f) that a template id cannot carry.
    assert (recognition_fingerprint(template_id="a|b", template_version="c")
            != recognition_fingerprint(template_id="a", template_version="b|c"))
    assert (recognition_fingerprint(template_id="a,b", template_version="c")
            != recognition_fingerprint(template_id="a", template_version="b,c"))


def test_an_approval_with_no_fingerprint_is_neither_widened_nor_voided():
    """Every approval given before this field existed carries `""`.

    Voiding them would silently discard real expert work; enforcing against an
    empty value would compare a run to nothing. They fall back to the named
    fields, which is what they were reviewed under.
    """
    from dociq.contracts import RecognitionTier  # noqa: PLC0415
    from dociq.sections.apply import apply_sections  # noqa: PLC0415
    from dociq.sections.model import SectionSpan  # noqa: PLC0415
    from dociq.sections.normalize import family_key  # noqa: PLC0415
    from tests.test_codex_r1_findings import _document  # noqa: PLC0415

    # A label that matches WITHOUT any project token, so the token scope check
    # is satisfied and the fingerprint is the only thing under test.
    label = "TABLE OF CONTENTS"
    legacy = dataclasses.replace(_approval(()), family_id="table-of-contents",
                                 recognition="")
    span = SectionSpan(label, family_key(label, ()), RecognitionTier.OUTLINE,
                       1, 1, "the outline")
    out = apply_sections(
        _document(), (span,), template=PROGRESS_REPORT, approvals=(legacy,),
        matter_root=legacy.matter_root, project_tokens=(),
        recognition="a-fingerprint-this-approval-never-saw")
    assert len(out.drops) == 1, out.warnings


def test_an_approval_whose_fingerprint_differs_is_refused():
    """The guard the previous test does NOT make.

    "A legacy approval still drops" stays green whether or not the fingerprint
    is enforced — it is a control for the fallback, not a check on the
    enforcement. This is the one that goes red if the comparison is removed.
    """
    from dociq.contracts import (  # noqa: PLC0415
        RecognitionTier,
        recognition_fingerprint,
    )
    from dociq.sections.apply import apply_sections  # noqa: PLC0415
    from dociq.sections.model import SectionSpan  # noqa: PLC0415
    from dociq.sections.normalize import family_key  # noqa: PLC0415
    from tests.test_codex_r1_findings import _document  # noqa: PLC0415

    label = "TABLE OF CONTENTS"
    span = SectionSpan(label, family_key(label, ()), RecognitionTier.OUTLINE,
                       1, 1, "the outline")
    reviewed_with_ocr = recognition_fingerprint(
        project_tokens=(), template_id=PROGRESS_REPORT.template_id,
        template_version=PROGRESS_REPORT.version, ocr_ran=True)
    run_without_ocr = recognition_fingerprint(
        project_tokens=(), template_id=PROGRESS_REPORT.template_id,
        template_version=PROGRESS_REPORT.version, ocr_ran=False)
    assert reviewed_with_ocr != run_without_ocr

    approval = dataclasses.replace(
        _approval(()), family_id="table-of-contents",
        recognition=reviewed_with_ocr)
    out = apply_sections(
        _document(), (span,), template=PROGRESS_REPORT, approvals=(approval,),
        matter_root=approval.matter_root, project_tokens=(),
        recognition=run_without_ocr)
    assert out.drops == (), "an approval reviewed under a different recognition dropped pages"
    assert any("recognition configuration" in w for w in out.warnings), out.warnings


def test_a_matching_fingerprint_still_drops():
    """And the fix must remain a scope check rather than an off switch."""
    from dociq.contracts import (  # noqa: PLC0415
        RecognitionTier,
        recognition_fingerprint,
    )
    from dociq.sections.apply import apply_sections  # noqa: PLC0415
    from dociq.sections.model import SectionSpan  # noqa: PLC0415
    from dociq.sections.normalize import family_key  # noqa: PLC0415
    from tests.test_codex_r1_findings import _document  # noqa: PLC0415

    label = "TABLE OF CONTENTS"
    fp = recognition_fingerprint(
        project_tokens=(), template_id=PROGRESS_REPORT.template_id,
        template_version=PROGRESS_REPORT.version, ocr_ran=True)
    approval = dataclasses.replace(
        _approval(()), family_id="table-of-contents", recognition=fp)
    span = SectionSpan(label, family_key(label, ()), RecognitionTier.OUTLINE,
                       1, 1, "the outline")
    out = apply_sections(
        _document(), (span,), template=PROGRESS_REPORT, approvals=(approval,),
        matter_root=approval.matter_root, project_tokens=(), recognition=fp)
    assert len(out.drops) == 1, out.warnings


def test_the_recognition_fingerprint_survives_to_the_persisted_snapshot():
    """B-R2-1's own lesson, applied to the field B-R2-1's fix introduced.

    A-23 added `recognition` to the snapshot. The derived guard above proves the
    construction site FEEDS it; this proves a real run carries a real value all
    the way to the hashed configuration, which is the half that was missing when
    `project_tokens` was added the same way and shipped empty.
    """
    from dociq.contracts import recognition_fingerprint  # noqa: PLC0415

    fp = recognition_fingerprint(
        project_tokens=(), template_id=PROGRESS_REPORT.template_id,
        template_version=PROGRESS_REPORT.version, ocr_ran=False)
    approval = dataclasses.replace(_approval(()), recognition=fp)

    dropped, config = _real_run(approval)
    assert config.omissions[0].recognition == fp, (
        "the run persisted an empty recognition — the field is on the contract "
        "and the amendment claims it, exactly as project_tokens was when it "
        "shipped unpopulated")
    assert dropped == 3, "a matching fingerprint must still apply the approval"


def test_a_stale_fingerprint_is_persisted_and_moves_the_identity():
    """Refused and applied must be distinguishable in the evidence, for the
    fingerprint as much as for the tokens — B-R2-1's requirement, restated for
    the new field."""
    from dociq.contracts import recognition_fingerprint  # noqa: PLC0415

    matching = recognition_fingerprint(
        project_tokens=(), template_id=PROGRESS_REPORT.template_id,
        template_version=PROGRESS_REPORT.version, ocr_ran=False)
    stale = recognition_fingerprint(
        project_tokens=(), template_id=PROGRESS_REPORT.template_id,
        template_version=PROGRESS_REPORT.version, ocr_ran=True)
    assert matching != stale

    applied_drops, applied_cfg = _real_run(
        dataclasses.replace(_approval(()), recognition=matching))
    refused_drops, refused_cfg = _real_run(
        dataclasses.replace(_approval(()), recognition=stale))

    assert applied_drops == 3 and refused_drops == 0
    assert applied_cfg.omissions[0].recognition != refused_cfg.omissions[0].recognition
    assert run_identity(applied_cfg) != run_identity(refused_cfg)
