"""§4 Stage 3's operator confirmation, end to end — rehearsal finding A4.

The finding this file exists to close: **Bates did nothing through the shipped
product.** ``RealPipeline.run`` passed ``auto_confirm_bates=False`` and the seam
carried no way to ask, so ``_bates_decision`` returned ``PENDING``,
``apply_bates_reported`` returned every document unchanged, and a Bates-stamped
production came out of the GUI with zero locators. Acceptance criterion 4's
92.130% was measured by ``tools/bates_acceptance.py``, which builds a CONFIRMED
decision in Python — a state the product could not reach.

So the tests here are deliberately end-to-end through :class:`RealPipeline` and
through :class:`MainWindow`'s worker thread, not over ``propose_format`` in
isolation. ``tests/test_bates.py`` already proves the detector; nothing it
asserts was ever false. What was false was that any of it ran.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dociq import adapter  # noqa: E402
from dociq.gui.pipeline import BatesProposal, RunRequest  # noqa: E402
from dociq.identify.bates import DecisionStatus  # noqa: E402

STAMPED_PAGES = 6
PREFIX = "IICON"


def _stamped_pdf(path: Path, prefix: str = PREFIX, start: int = 1,
                 pages: int = STAMPED_PAGES) -> None:
    """A small native-text PDF whose every page carries a Bates stamp.

    Built with the fixture module's own canvas helper so the file is
    byte-reproducible for the same reason every other fixture is.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
    import make_fixtures

    c = make_fixtures._pdf_canvas(path)
    for i in range(pages):
        make_fixtures._text_page(c, [
            "MONTHLY PROGRESS REPORT",
            f"Body text for page {i + 1} of the production volume.",
            "",
            f"{prefix} {str(start + i).zfill(6)}",
        ])
    c.save()


@pytest.fixture
def stamped_corpus(tmp_path) -> Path:
    src = tmp_path / "production"
    src.mkdir()
    _stamped_pdf(src / "vol1.pdf")
    return src


def _request(src: Path, out: Path) -> RunRequest:
    return RunRequest(str(src), str(out), None, None)


def _locators(outcome) -> list[str]:
    return [p.bates for d in outcome.result.documents for p in d.pages if p.bates]


# ---------------------------------------------------------------------------
# The finding itself, at the adapter
# ---------------------------------------------------------------------------


def test_a_gui_run_with_no_operator_still_applies_nothing(stamped_corpus, tmp_path):
    """The old behaviour, kept as an assertion rather than deleted.

    ``confirm_bates=None`` means *nobody was asked*. That is not a refusal and
    it is not a confirmation, and the run must say which it is: a machine
    confirmation, recorded in the warnings, exactly as ``auto_confirm_bates``
    already did. Without the flag the format stays PENDING and no locator is
    written — which is the correct answer to "no operator, no stored pattern",
    and was the WRONG answer for a GUI run because the GUI HAS an operator.
    """
    pipe = adapter.RealPipeline()
    outcome = pipe.run(_request(stamped_corpus, tmp_path / "out"),
                       lambda _e: None, lambda: False)
    assert _locators(outcome) == []
    assert any("NOT applied" in w for w in outcome.result.warnings)


def test_the_operator_confirming_writes_locators(stamped_corpus, tmp_path):
    """FAIL-BEFORE (rehearsal A4): before ``confirm_bates`` existed there was no
    argument to pass here, and a stamped production through the GUI path
    produced zero locators. This is the whole finding, measured."""
    asked: list[BatesProposal] = []

    def confirm(proposal: BatesProposal) -> bool:
        asked.append(proposal)
        return True

    pipe = adapter.RealPipeline()
    outcome = pipe.run(_request(stamped_corpus, tmp_path / "out"),
                       lambda _e: None, lambda: False, confirm_bates=confirm)

    assert len(asked) == 1, "Stage 3 asked the operator more or less than once"
    assert _locators(outcome) == [
        f"{PREFIX} {str(i + 1).zfill(6)}" for i in range(STAMPED_PAGES)
    ]
    assert any("confirmed by the operator" in w.lower()
               for w in outcome.result.warnings)


def test_the_proposal_carries_what_an_operator_can_judge(stamped_corpus, tmp_path):
    """A regex is not confirmable. A real locator off a real page is."""
    seen: list[BatesProposal] = []
    adapter.RealPipeline().run(
        _request(stamped_corpus, tmp_path / "out"),
        lambda _e: None, lambda: False, confirm_bates=lambda p: seen.append(p) or True)

    (proposal,) = seen
    assert proposal.example.startswith(f"{PREFIX} "), proposal.example
    assert proposal.example in [
        f"{PREFIX} {str(i + 1).zfill(6)}" for i in range(STAMPED_PAGES)
    ], "the example is not a locator that is actually on a page"
    assert proposal.documents == 1
    assert proposal.pages == STAMPED_PAGES
    assert proposal.coverage_pct == 100
    assert proposal.alternatives == ()


def test_the_alternatives_are_d28s_census_not_the_runner_up_shapes(tmp_path):
    """FOUND ON REAL CLIENT DATA, not reasoned.

    The screen states, as fact, that a non-empty ``alternatives`` means the
    matter is multi-series and D-28 therefore refuses prefix repair. Only
    ``identify.bates.matter_prefixes`` answers that: it applies the same two
    bars a proposal must clear. ``BatesProposal.alternatives`` is ``ranked[1:4]``
    with **no** bar, and on the MNFV production it came back as ``Check 0001``
    and ``retained 90095 49 00001`` — two stray lines in a single-series
    production. The first draft of the adapter passed those through and would
    have told the operator, on screen, that their production was multi-series.
    """
    from dociq.identify.bates import matter_prefixes, propose_format
    from tests.fixtures import page as _page, document as _document

    # One real series, plus a stray line that parses as a locator and clears
    # NEITHER bar. This is the shape the client corpus had.
    real = _document("production/vol1.pdf", tuple(
        _page(i, f"Body {i}.\nMNFV {str(2635 + i).zfill(5)}")
        for i in range(1, 9)))
    noise = _document("production/vol2.pdf", (
        _page(1, "Cover.\nCheck 0001"),
        _page(2, "Body with no stamp at all."),
        _page(3, "More body with no stamp."),
        _page(4, "Still no stamp."),
    ))
    docs = (real, noise)

    proposal = propose_format(docs)
    assert proposal is not None and proposal.format.prefix == "MNFV"
    assert proposal.alternatives, "the fixture no longer has a runner-up shape"
    assert matter_prefixes(docs) == ("MNFV",), "the fixture is really multi-series"

    from dociq.adapter import _proposal_for_gui

    census = tuple(p for p in matter_prefixes(docs) if p != proposal.format.prefix)
    assert _proposal_for_gui(proposal, census).alternatives == (), (
        "a stray line was rendered to the operator as a second stamp series")


def test_a_genuinely_multi_series_matter_reaches_the_screen(tmp_path):
    """The other direction: two REAL series must arrive as alternatives, or the
    screen would silently drop the one disclosure D-28 depends on."""
    from dociq.identify.bates import matter_prefixes, propose_format
    from tests.fixtures import page as _page, document as _document

    a = _document("production/a.pdf", tuple(
        _page(i, f"Body {i}.\nMNFV {str(i).zfill(5)}") for i in range(1, 7)))
    b = _document("production/b.pdf", tuple(
        _page(i, f"Body {i}.\nIICON {str(i).zfill(5)}") for i in range(1, 7)))
    docs = (a, b)

    assert set(matter_prefixes(docs)) == {"MNFV", "IICON"}
    proposal = propose_format(docs)
    assert proposal is not None

    from dociq.adapter import _proposal_for_gui

    others = tuple(p for p in matter_prefixes(docs) if p != proposal.format.prefix)
    assert _proposal_for_gui(proposal, others).alternatives == others
    assert len(others) == 1


def test_declining_is_a_decision_not_an_absence(stamped_corpus, tmp_path):
    """An unstamped production and a stamped one whose format was declined are
    different facts about the record, and the log must be able to tell them
    apart. The decision is REJECTED, not None, and it says so in words."""
    outcome = adapter.RealPipeline().run(
        _request(stamped_corpus, tmp_path / "out"),
        lambda _e: None, lambda: False, confirm_bates=lambda _p: False)

    assert _locators(outcome) == []
    declined = [w for w in outcome.result.warnings if "DECLINED" in w]
    assert declined, outcome.result.warnings
    # It must say the matter is NOT unstamped, rather than saying nothing and
    # leaving a reader to assume it.
    assert "not unstamped" in " ".join(declined).lower()


def test_a_declined_format_is_not_stored_for_the_next_run(stamped_corpus, tmp_path):
    """A rejection must not leave a pattern behind that a re-run loads as a
    confirmation — the operator's "no" silently promoted to "yes"."""
    out = tmp_path / "out"
    outcome = adapter.RealPipeline().run(
        _request(stamped_corpus, out), lambda _e: None, lambda: False,
        confirm_bates=lambda _p: False)
    assert outcome.result.config.bates_pattern is None


def test_a_confirmed_format_is_stored_for_the_next_run(stamped_corpus, tmp_path):
    """"Confirmed once per document set, then applied automatically" (§4)."""
    outcome = adapter.RealPipeline().run(
        _request(stamped_corpus, tmp_path / "out"), lambda _e: None,
        lambda: False, confirm_bates=lambda _p: True)
    assert outcome.result.config.bates_pattern
    assert "dociq-bates" in outcome.result.config.bates_pattern


def test_a_stored_confirmation_does_not_ask_again(stamped_corpus, tmp_path):
    """The second run over the same matter must not re-prompt: the pattern is
    already stored, and Stage 3 loads it. Asking again would be the tool
    doubting a ruling the expert already gave."""
    out = tmp_path / "out"
    first = adapter.RealPipeline().run(
        _request(stamped_corpus, out), lambda _e: None, lambda: False,
        confirm_bates=lambda _p: True)
    pattern = first.result.config.bates_pattern
    assert pattern

    asked = []
    outcome = adapter.RealPipeline().run(
        _request(stamped_corpus, out), lambda _e: None, lambda: False,
        confirm_bates=lambda p: asked.append(p) or True)
    assert asked == [], "Stage 3 re-asked a question the operator already answered"
    assert _locators(outcome), "the stored confirmation was not applied"


def test_aborting_at_the_confirmation_publishes_nothing(stamped_corpus, tmp_path):
    """The abort path through the REAL pipeline, not through a stand-in.

    Walking away at Stage 3 must take the ordinary cancellation road: nothing
    published, ``incomplete_run/`` written, terminal status CANCELLED — and, the
    part that is easy to lose, **the previous run's deliverables left exactly as
    they were** (Codex B-1). A second publication rule for a second abort site
    is a second chance to get publication wrong.
    """
    from dociq.runstate import RunAborted, TerminalStatus

    out = tmp_path / "out"
    # The first run DECLINES rather than confirms, and that is not incidental:
    # a confirmation is stored, so the second run would load it and never ask —
    # which is the correct behaviour and would make this test vacuous. A
    # declined run publishes deliverables and stores no pattern, so the matter
    # is still one the prompt can appear on.
    good = adapter.RealPipeline().run(
        _request(stamped_corpus, out), lambda _e: None, lambda: False,
        confirm_bates=lambda _p: False)
    assert good.published
    index_before = (out / "document_index.xlsx")
    stamp_before = index_before.stat().st_mtime_ns if index_before.exists() else None

    def walk_away(_proposal):
        raise RunAborted("the operator closed the window")

    outcome = adapter.RealPipeline().run(
        _request(stamped_corpus, out), lambda _e: None, lambda: False,
        confirm_bates=walk_away)

    assert not outcome.published
    assert outcome.termination.status is TerminalStatus.CANCELLED
    assert "Stage 3" in outcome.termination.reason
    assert (out / "incomplete_run").is_dir()
    if stamp_before is not None:
        assert index_before.stat().st_mtime_ns == stamp_before, \
            "an abandoned run overwrote the last complete run's deliverables"
    # NOT recorded as a refusal anywhere.
    assert not any("DECLINED" in w for w in outcome.result.warnings)


def test_the_summary_distinguishes_declined_from_not_yet_confirmed():
    """``_bates_note`` reaches ``run_summary.pdf``. "The operator has not
    confirmed it" and "the operator declined it" are different facts, and an
    expert forwards this sentence."""
    from dociq.identify.bates import BatesDecision, BatesFormat
    from dociq.pipeline import _bates_note

    fmt = BatesFormat(prefix="IICON", separator=" ", digit_widths=(6,))
    pending = _bates_note(BatesDecision(DecisionStatus.PENDING, fmt), {})
    rejected = _bates_note(BatesDecision(DecisionStatus.REJECTED, fmt), {})
    absent = _bates_note(None, {})

    assert "DECLINED" in rejected
    assert "has not confirmed" in pending
    assert len({pending, rejected, absent}) == 3


# ---------------------------------------------------------------------------
# The GUI round trip — worker thread to GUI thread and back
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class _SlowConfirmPipeline:
    """A stand-in whose run BLOCKS in ``confirm_bates`` on the worker thread.

    It exists to test the round trip rather than the pipeline: the property at
    stake — that a background thread can get an answer from the GUI thread
    without deadlocking either — is a property of :mod:`dociq.gui.main_window`,
    and testing it through a real 6-stage run would measure OCR.
    """

    def __init__(self, proposal: BatesProposal | None = None) -> None:
        self.proposal = proposal or BatesProposal(
            pattern="IICON 000001", example="IICON 000001", documents=1,
            pages=6, coverage_pct=100.0)
        self.answer: bool | None = None
        self.raised: BaseException | None = None
        self.done = threading.Event()

    def profiles(self):
        return ()

    def preview_folder(self, path):
        from dociq.gui.pipeline import FolderPreview

        return FolderPreview(0, 0)

    def disclosure(self):
        return ""

    def run(self, request, on_progress, should_cancel, confirm_bates=None):
        from dociq.gui.pipeline import RunOutcome, TokenEstimate
        from dociq.contracts import RunConfig, RunResult

        try:
            self.answer = confirm_bates(self.proposal)
        except BaseException as exc:  # noqa: BLE001 — recorded, then re-raised
            self.raised = exc
            self.done.set()
            raise
        self.done.set()
        result = RunResult(config=RunConfig(source_root=request.source_root,
                                            output_root=request.output_root),
                           documents=(), unsupported=(), warnings=())
        return RunOutcome(result=result,
                          tokens_before=TokenEstimate(0, 2.0, 3.0),
                          tokens_after=TokenEstimate(0, 2.0, 3.0),
                          output_root=request.output_root)


def _pump(app, predicate, timeout: float = 10.0) -> bool:
    """Spin the GUI event loop until ``predicate`` or the clock runs out.

    A bounded wait, not ``processEvents`` in an unbounded loop: a deadlock must
    make the test FAIL, not hang the suite until someone kills it.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return False


@pytest.mark.parametrize("answer", [True, False])
def test_the_confirmation_crosses_the_thread_boundary(app, answer, tmp_path):
    """The hard part, asserted: the run blocks on the worker thread, the prompt
    appears on the GUI thread, and the operator's answer travels back."""
    from dociq.gui.main_window import BATES, PROGRESS, MainWindow

    pipe = _SlowConfirmPipeline()
    window = MainWindow(pipeline=pipe)
    try:
        window.start_run(RunRequest(str(tmp_path), str(tmp_path / "out"), None, None))
        assert _pump(app, lambda: window.stack.currentIndex() == BATES), \
            "the confirmation screen never appeared — the prompt did not cross"
        assert window.bates.example_text() == "IICON 000001"

        window.bates.confirmed.emit() if answer else window.bates.declined.emit()
        assert _pump(app, pipe.done.is_set), "the answer never came back"
        assert pipe.answer is answer
        assert _pump(app, lambda: window.stack.currentIndex() != BATES)
    finally:
        window.close()
        _pump(app, lambda: not window.thread_running(), timeout=5.0)


def test_cancelling_while_the_prompt_is_open_aborts_rather_than_declining(
        app, tmp_path):
    """Closing the window with the prompt open must NOT be recorded as a
    refusal, and must not hang: the wait is interruptible, and it raises
    :class:`RunAborted` so Stage 3 takes the ordinary cancellation path."""
    from dociq.gui.main_window import BATES, MainWindow
    from dociq.runstate import RunAborted

    pipe = _SlowConfirmPipeline()
    window = MainWindow(pipeline=pipe)
    window.start_run(RunRequest(str(tmp_path), str(tmp_path / "out"), None, None))
    assert _pump(app, lambda: window.stack.currentIndex() == BATES)

    window.close()
    assert _pump(app, pipe.done.is_set), "the worker never unblocked"
    assert isinstance(pipe.raised, RunAborted), pipe.raised
    assert pipe.answer is None, "an abort was recorded as an answer"
    assert _pump(app, lambda: not window.thread_running(), timeout=5.0)


def test_stopping_from_the_prompt_screen_aborts(app, tmp_path):
    from dociq.gui.main_window import BATES, MainWindow
    from dociq.runstate import RunAborted

    pipe = _SlowConfirmPipeline()
    window = MainWindow(pipeline=pipe)
    try:
        window.start_run(RunRequest(str(tmp_path), str(tmp_path / "out"), None, None))
        assert _pump(app, lambda: window.stack.currentIndex() == BATES)
        window.bates.stop_requested.emit()
        assert _pump(app, pipe.done.is_set)
        assert isinstance(pipe.raised, RunAborted), pipe.raised
    finally:
        window.close()
        _pump(app, lambda: not window.thread_running(), timeout=5.0)


def test_the_prompt_names_a_multi_series_production(app, tmp_path):
    """D-28 refuses prefix repair when the matter carries more than one prefix.
    The operator must SEE that, not have it decided for them."""
    from dociq.gui.main_window import BATES, MainWindow

    pipe = _SlowConfirmPipeline(BatesProposal(
        pattern="IICON 000001", example="IICON 000123", documents=20,
        pages=306, coverage_pct=94.0, alternatives=("CP", "MNFV")))
    window = MainWindow(pipeline=pipe)
    try:
        window.start_run(RunRequest(str(tmp_path), str(tmp_path / "out"), None, None))
        assert _pump(app, lambda: window.stack.currentIndex() == BATES)
        text = window.bates.alternatives_text()
        assert "more than one" in text.lower() or "multi" in text.lower()
        assert "CP" in text and "MNFV" in text
        assert "D-28" in text
    finally:
        window.bates.stop_requested.emit()
        window.close()
        _pump(app, lambda: not window.thread_running(), timeout=5.0)


# ---------------------------------------------------------------------------
# The CLASS — every Stage-3-style decision the GUI could silently default
# ---------------------------------------------------------------------------


def test_no_pipeline_option_silently_decides_for_the_operator():
    """Global probe (fix-the-class).

    ``auto_confirm_bates`` was not a one-off: it is one member of a class —
    a ``PipelineOptions`` field that stands in for a ruling the operator was
    supposed to make. Every such field is enumerated here BY NAME with the
    reason it is safe, so that adding a new one breaks this test rather than
    shipping another silent default.
    """
    from dociq.pipeline import PipelineOptions

    # name -> why a GUI run may leave it at its default
    accounted = {
        "walk": "walker settings, not a ruling",
        "matter_name": "a label, not a ruling",
        "master_index": "the adapter passes the operator's chosen path instead",
        "master_index_path": "chosen by the operator on the setup screen",
        "profiles": "chosen by the operator on the setup screen, and §6's "
                    "checklist gates it",
        "bates_decision": "the pre-made decision used by harnesses; the GUI "
                          "supplies confirm_bates instead",
        "auto_confirm_bates": "headless only; the GUI supplies confirm_bates, "
                              "and _bates_decision prefers it",
        "confirm_bates": "THE operator ruling — supplied by the GUI (A-14)",
        "stamp": "who is running it, taken from the OS",
        "previous_ledger": "defaults to the ledger already in the output root, "
                           "which is the re-run case D-04(b) is about",
        "on_stage": "progress reporting",
        "write_workbook": "a deliverable switch; the GUI leaves it on",
        "write_summary_pdf": "a deliverable switch; the GUI leaves it on",
        "write_package": "a deliverable switch; the GUI leaves it on",
    }
    fields = set(PipelineOptions.__dataclass_fields__)
    assert fields == set(accounted), (
        "PipelineOptions grew or lost a field. Every field must be accounted "
        "for here: if the new one stands in for an operator ruling, the GUI "
        "must ASK rather than default it (rehearsal A4).\n"
        f"unaccounted: {sorted(fields - set(accounted))}\n"
        f"stale: {sorted(set(accounted) - fields)}"
    )


def test_every_pipeline_implementation_forwards_the_confirmation():
    """The seam is only closed if every implementation of it carries the
    parameter. A stand-in that silently dropped ``confirm_bates`` would put the
    finding straight back in a different file."""
    import inspect

    from dociq.adapter import RealPipeline
    from dociq.gui.mock_pipeline import MockPipeline
    from dociq.gui.pipeline import PipelineAPI

    for cls in (RealPipeline, MockPipeline, PipelineAPI):
        params = inspect.signature(cls.run).parameters
        assert "confirm_bates" in params, f"{cls.__name__}.run drops it"
        assert params["confirm_bates"].default is None, cls.__name__


def test_the_decision_status_enum_is_fully_reachable_from_the_gui():
    """PENDING, CONFIRMED and REJECTED all had to be reachable through the
    product. Before A4 only PENDING was."""
    assert {s.value for s in DecisionStatus} == {
        "pending", "confirmed", "rejected"}
