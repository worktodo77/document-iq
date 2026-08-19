"""Codex round 1 on Sprint 4 — the five blockers, as executable statements.

Each test here reproduces one finding from
`docs/codex_reviews/sprint-4_2026-08-19_codex.md` **before** it is fixed, so the
fix is watched turning it green rather than asserted to have worked.

B-1 is the one that matters. The review request asked Codex to attack exactly
this argument — "a wrong token costs an offer, never a page" — and named the
shape to attack it in: a family whose approval is already engaged when the token
list changes. The argument is false in that shape, and the product's ordinary
corrective workflow reaches it: run, engage a lever, notice DocIQ missed
`BOMESC`, type it in, run again.
"""

from __future__ import annotations

import dataclasses
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from dociq.contracts import (  # noqa: E402
    DocumentRecord,
    PageKind,
    PageRecord,
    RecognitionTier,
    matter_key,
)
from dociq.gui.main_window import MainWindow  # noqa: E402
from dociq.gui.mock_pipeline import MockPipeline  # noqa: E402
from dociq.sections.apply import apply_sections  # noqa: E402
from dociq.sections.model import ApprovedOmission, SectionSpan  # noqa: E402
from dociq.sections.normalize import family_key  # noqa: E402
from dociq.sections.templates import PROGRESS_REPORT  # noqa: E402

MATTER = r"D:\matter-A"
LABEL = "MV32 TABLE OF CONTENTS"
"""A project-tokened label, from the review's own reproduction. With no tokens
it matches no family; with `MV32` supplied it normalizes to a family the shipped
template names."""


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])


def _document() -> DocumentRecord:
    return DocumentRecord(
        doc_id="LI-00001", rel_path="a.pdf", filename="a.pdf",
        sha256="0" * 64, size_bytes=1, ext=".pdf",
        pages=(PageRecord(page_no=1, text="x", kind=PageKind.NATIVE),),
    )


def _approval() -> ApprovedOmission:
    return ApprovedOmission(
        family_id="table-of-contents", approved_by="abachowski",
        approved_at="2026-08-19T12:00:00Z", matter="matter-A",
        matter_root=matter_key(MATTER),
        template_id=PROGRESS_REPORT.template_id,
        template_version=PROGRESS_REPORT.version,
    )


def _dropped(project_tokens: tuple[str, ...]) -> int:
    """Pages dropped for ONE unchanged approval, under one token set.

    The tokens reach Stage 4 through the span's family key — that is where
    `spans_from_pages` puts them — so this varies them exactly where a real run
    varies them, not through a parameter `apply_sections` does not have.
    """
    span = SectionSpan(LABEL, family_key(LABEL, project_tokens),
                       RecognitionTier.OUTLINE, 1, 1, "the outline")
    out = apply_sections(
        _document(), (span,), template=PROGRESS_REPORT,
        approvals=(_approval(),), matter_root=matter_key(MATTER),
        project_tokens=project_tokens,
    )
    return len(out.drops)


def test_b1_a_token_edit_cannot_widen_an_approval_already_given():
    """**The load-bearing D-39 argument, stated as a test.**

    One approval, unchanged, given once. Editing the token list between runs
    must not turn pages the expert never saw offered into pages that drop. The
    approval was reviewed against a recognition configuration; a different
    configuration is a different question, and answering it with the old
    approval is a drop no one attributed.
    """
    before = _dropped(())
    after = _dropped(("MV32",))
    assert after == before, (
        f"a token edit changed drops from {before} to {after} with no new "
        "approval — the approval was widened, not applied")


def test_b2_the_waterfall_is_built_with_the_tokens_the_run_used(tmp_path):
    """The screen must describe the run that happened.

    `run()` built the config from `request.project_tokens` and then built the
    waterfall from `self._project_tokens` — the constructor default, empty in
    the shipped GUI. A drop the run made was then redrawn for the operator as
    an unknown, non-engageable row, and an unapproved run never offered the
    lever at all.

    **Behavioral, over a real run**, as the review asked: tokens are supplied on
    the REQUEST, the adapter's constructor is left at its default, and what
    `_plan` actually received is recorded. Asserting on the source text would
    pass for a rewrite that reintroduced the defect by another spelling.
    """
    from dociq import adapter  # noqa: PLC0415
    from dociq.gui.pipeline import RunRequest  # noqa: PLC0415

    fixtures = Path(__file__).resolve().parent / "fixtures"
    seen: dict[str, tuple[str, ...]] = {}
    real_plan = adapter._plan

    def spy(*a, project_tokens=(), **kw):
        seen["tokens"] = tuple(project_tokens)
        return real_plan(*a, project_tokens=project_tokens, **kw)

    adapter._plan = spy
    try:
        pipe = adapter.RealPipeline()          # constructor default: no tokens
        request = RunRequest(str(fixtures), str(tmp_path / "out"),
                             project_tokens=("MV32",))
        pipe.run(request, lambda _e: None, lambda: False)
    finally:
        adapter._plan = real_plan

    assert seen.get("tokens") == ("MV32",), (
        f"the waterfall was built with {seen.get('tokens')!r}; the run was "
        "configured with ('MV32',)")


def test_a1_the_template_checklist_forward_button_does_not_crash(app):
    """D-38 removed `ProfileChecklistView.profile`; the forward button still
    dereferenced it. The existing test proved the button was ENABLED and never
    clicked it."""
    window = MainWindow(MockPipeline())
    try:
        window.show_template_checklist()
        assert window.checklist._view is not None, "checklist did not populate"
        window.checklist._emit_accept()  # must not raise
    finally:
        window.close()


class _PerMatter(MockPipeline):
    """A pipeline whose proposal depends on the folder, as a real one does."""

    BY_SOURCE = {r"D:\matter-A": ("MV32",), r"D:\matter-B": ("MI20",)}

    def propose_project_tokens(self, source):
        return self.BY_SOURCE.get(source, ())


def _pick(window, path: str) -> None:
    """Choose a folder through the product's own entry point.

    Calling `_tokens_proposed` directly would skip `begin_source` and test a
    path the operator cannot reach — which is how the first draft of this test
    stayed red against a working fix.
    """
    window.setup._source.setText(path)
    window._propose_tokens(path)
    _settle(window)


def _settle(window, deadline_ms: int = 5000) -> None:
    from PySide6.QtCore import QDeadlineTimer, QElapsedTimer  # noqa: PLC0415

    clock = QElapsedTimer()
    clock.start()
    while window._token_jobs and clock.elapsed() < deadline_ms:
        for thread, _ in list(window._token_jobs):
            thread.wait(QDeadlineTimer(50))
        QApplication.processEvents()
    QApplication.processEvents()
    assert clock.elapsed() < deadline_ms, "the proposal thread never finished"


def test_a2_choosing_a_second_matter_does_not_keep_the_first_matters_tokens(app):
    """`set_proposed_tokens` refuses to overwrite a non-empty field, which
    correctly protects a human edit — and, unscoped, wrongly treated matter A's
    PROPOSAL as a human edit belonging to matter B."""
    window = MainWindow(_PerMatter())
    try:
        _pick(window, r"D:\matter-A")
        assert window.setup.project_tokens() == ("MV32",)

        _pick(window, r"D:\matter-B")
        assert window.setup.project_tokens() == ("MI20",), (
            "matter B was configured, and hashed, with matter A's project names")
        assert window.setup.request().project_tokens == ("MI20",)
    finally:
        window.close()


def test_a2_a_human_edit_still_survives_within_one_matter(app):
    """The guard A-2 corrects must be SCOPED, not removed. An operator who types
    a name DocIQ missed must not have it wiped by a late proposal for the folder
    they are still on."""
    window = MainWindow(_PerMatter())
    try:
        _pick(window, r"D:\matter-A")
        window.setup._tokens.setText("BOMESC")
        window._tokens_proposed(r"D:\matter-A", ("MV32",))
        assert window.setup.project_tokens() == ("BOMESC",)
    finally:
        window.close()


def test_a2_returning_to_the_same_matter_does_not_wipe_the_edit(app):
    """`begin_source` must be a no-op for the folder already selected — an
    operator who re-picks the same folder, or whose pick fires twice, keeps the
    correction they typed."""
    window = MainWindow(_PerMatter())
    try:
        _pick(window, r"D:\matter-A")
        window.setup._tokens.setText("BOMESC, MV32")
        _pick(window, r"D:\matter-A")
        assert window.setup.project_tokens() == ("BOMESC", "MV32")
    finally:
        window.close()


def test_b3_the_manifest_claims_only_what_the_identity_actually_covers():
    """`IDENTITY_NOTE` is written into every `output_manifest.json` as
    `claim_identity`. D-38 removed the profile fields from `RunConfig`; the
    claim still named them, and a test pinned the false claim green."""
    import dataclasses  # noqa: PLC0415

    from dociq.contracts import RunConfig  # noqa: PLC0415
    from dociq.verify.manifest import IDENTITY_NOTE  # noqa: PLC0415

    live = {f.name for f in dataclasses.fields(RunConfig)}
    for retired in ("profile_hash", "profile snapshot", "profiles"):
        assert retired not in IDENTITY_NOTE, (
            f"the persisted identity claim names {retired!r}, which contract "
            f"2.0.0 removed from RunConfig (fields: {sorted(live)})")


def _dropped_reviewed_under(reviewed: tuple[str, ...],
                            run_tokens: tuple[str, ...]) -> int:
    """Drops for an approval REVIEWED under one token set, applied under another."""
    span = SectionSpan(LABEL, family_key(LABEL, run_tokens),
                       RecognitionTier.OUTLINE, 1, 1, "the outline")
    approval = dataclasses.replace(_approval(), project_tokens=reviewed)
    out = apply_sections(
        _document(), (span,), template=PROGRESS_REPORT,
        approvals=(approval,), matter_root=matter_key(MATTER),
        project_tokens=run_tokens,
    )
    return len(out.drops)


def test_b1_an_approval_reviewed_under_the_runs_tokens_still_drops():
    """**The half that proves the fix is a scope check and not an off switch.**

    Refusing every approval would also make B-1's test pass, and would make the
    product useless. An expert who approves a lever on a run configured with
    `MV32` must still get that omission on the next run with `MV32`.
    """
    assert _dropped_reviewed_under(("MV32",), ("MV32",)) == 1


def test_b1_the_refusal_is_symmetric_and_says_which_ruling_did_not_apply():
    """Fail-closed in both directions — adding a token and removing one are the
    same kind of change — and the operator is told, as they are for a wrong
    matter or a wrong template version."""
    assert _dropped_reviewed_under(("MV32",), ()) == 0
    assert _dropped_reviewed_under((), ("MV32",)) == 0

    span = SectionSpan(LABEL, family_key(LABEL, ("MV32",)),
                       RecognitionTier.OUTLINE, 1, 1, "the outline")
    out = apply_sections(
        _document(), (span,), template=PROGRESS_REPORT,
        approvals=(_approval(),), matter_root=matter_key(MATTER),
        project_tokens=("MV32",),
    )
    assert out.drops == ()
    joined = " ".join(out.warnings).lower()
    assert "project names" in joined and "table-of-contents" in joined, out.warnings


def test_b1_a_different_spelling_of_one_token_set_is_not_a_different_review():
    """Compared canonically. `("MV32","BOMESC")` and `("bomesc","mv32")` are one
    recognition configuration, and refusing between them would fail closed on a
    run that is not different at all."""
    assert _dropped_reviewed_under(("bomesc", "mv32"), ("MV32", "BOMESC")) == 1


def test_b1_editing_the_tokens_warns_that_retained_approvals_no_longer_apply(app):
    """Alex's ruling (2026-08-19): warn at setup AND fail closed at Stage 4.

    Stage 4 refuses either way. This is the half that stops the refusal being a
    surprise at the end of a ten-minute run over a real matter.
    """
    window = MainWindow(_PerMatter())
    try:
        _pick(window, r"D:\matter-A")
        window.setup._tokens.setText("MV32")
        # Three approvals, all reviewed under the same scope. The API takes one
        # scope PER APPROVAL rather than a count and an exemplar: describing a
        # mixed set by its first member was Codex round 2, A-R2-1.
        window.setup.set_retained_scopes((("MV32",),) * 3)
        assert "still apply" in window.setup._tokens_hint.text()

        window.setup._tokens.setText("MV32, BOMESC")
        hint = window.setup._tokens_hint.text()
        assert "NO LONGER APPLY" in hint, hint
        assert "3 approval" in hint
        # And it must say what happens to the pages, not only what is lost.
        assert "kept" in hint.lower()
    finally:
        window.close()


def test_b1_no_retained_approvals_means_no_warning(app):
    """A first run of a matter must not be told it is losing something."""
    window = MainWindow(_PerMatter())
    try:
        _pick(window, r"D:\matter-A")
        window.setup.set_retained_scopes(())
        window.setup._tokens.setText("MV32, BOMESC")
        assert "NO LONGER APPLY" not in window.setup._tokens_hint.text()
    finally:
        window.close()


def test_b1_the_approval_records_the_tokens_the_waterfall_was_built_under(app):
    """Captured from the RUN, not from the setup field.

    By the time a lever is engaged, the field may already have been edited for
    the next run — recording that would stamp an approval with a configuration
    the expert never reviewed, which is B-1 again with the inputs swapped.
    """
    from dociq.gui.pipeline import RunRequest  # noqa: PLC0415

    seen = {}

    class Capturing(MockPipeline):
        def set_omission(self, family_id, engaged, matter, source_root="",
                         project_tokens=()):
            seen["tokens"] = tuple(project_tokens)
            return None

    window = MainWindow(Capturing())
    try:
        window._request = RunRequest(
            source_root=r"D:\matter-A", output_root=r"D:\matter-A\out",
            project_tokens=("MV32",))
        window.setup._tokens.setText("MV32, BOMESC")   # already edited ahead
        window._capture_approval("table-of-contents", True)
        assert seen["tokens"] == ("MV32",), (
            "the approval recorded the setup field, not the configuration the "
            "operator actually reviewed")
    finally:
        window.close()
