"""D-39: DocIQ proposes a matter's project tokens, and the operator corrects it.

The derivation is measurably unreliable — on the real corpus it returns four
genuine tokens out of seven and misses the two most frequent ones. These tests
therefore assert two different things, and the second matters more than the
first: that the rule works on the signal it was built for, and that **being
wrong is bounded** — a bad token cannot lose a page, and the operator's edit
always beats the proposal.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from dociq.gui.main_window import MainWindow  # noqa: E402
from dociq.gui.mock_pipeline import MockPipeline  # noqa: E402
from dociq.sections.project_tokens import (  # noqa: E402
    DOCUMENT_LIFECYCLE_WORDS,
    propose_tokens,
)


def test_a_token_in_both_the_labels_and_the_filenames_is_proposed():
    tokens = propose_tokens(
        {
            "a.pdf": ["MV32 SCHEDULE", "MV32 PROCUREMENT"],
            "b.pdf": ["MV32 ENGINEERING"],
        },
        ["MV32-001", "MV32-002"],
    )
    assert "MV32" in tokens


def test_a_section_word_absent_from_the_filenames_is_not_proposed():
    """The rule's whole purpose. `SCHEDULE` is frequent and names nothing."""
    tokens = propose_tokens(
        {
            "a.pdf": ["MV32 SCHEDULE", "MV32 SCHEDULE NARRATIVE"],
            "b.pdf": ["MV32 SCHEDULE"],
        },
        ["MV32-001", "MV32-002"],
    )
    assert "SCHEDULE" not in tokens


def test_a_token_in_one_document_only_is_not_proposed():
    """One document's quirk is not the matter's vocabulary."""
    tokens = propose_tokens(
        {"a.pdf": ["ACME OVERVIEW"], "b.pdf": ["MV32 OVERVIEW"]},
        ["ACME-1", "ACME-2", "MV32-1", "MV32-2"],
    )
    assert "ACME" not in tokens


def test_document_lifecycle_words_are_never_proposed():
    """`REV` scores exactly like a project identifier: it is in 21 filenames of
    the real corpus and inside its labels. It describes the document."""
    tokens = propose_tokens(
        {"a.pdf": ["REV 3 PROGRESS"], "b.pdf": ["REV 4 PROGRESS"]},
        # The bare tokens, deliberately: `REPORT-REV3` tokenizes to `REV3`, so
        # a fixture spelled that way would pass this test without the guard
        # ever running.
        ["REV-PROGRESS-A", "REV-PROGRESS-B"],
    )
    assert "REV" not in tokens
    assert "PROGRESS" not in tokens


def test_the_lifecycle_list_names_no_project():
    """D-24 forbids shipping anything attributable to a corpus project. This
    list is generic English and must stay that way — it would read identically
    for a matter DocIQ has never seen."""
    for word in ("MV32", "BOMESC", "PETROBRAS", "YARD", "TOPSIDE", "MI20"):
        assert word not in DOCUMENT_LIFECYCLE_WORDS


def test_pure_digits_are_not_proposed():
    """`001` recurs in filenames and labels and identifies nothing."""
    tokens = propose_tokens(
        {"a.pdf": ["001 INTRODUCTION"], "b.pdf": ["001 SUMMARY"]},
        ["001-A", "001-B"],
    )
    assert "001" not in tokens


def test_portuguese_articles_are_not_proposed():
    """This corpus is Brazilian; an English-only stopword list proposes `DE`."""
    tokens = propose_tokens(
        {
            "a.pdf": ["RELATORIO DE PROGRESSO", "PLANO DE ATAQUE"],
            "b.pdf": ["RESUMO DE OBRA"],
        },
        ["RELATORIO-DE-1", "RELATORIO-DE-2"],
    )
    assert "DE" not in tokens


def test_the_proposal_is_deterministic_under_input_ordering():
    """It is a hashed run input (A-19): two runs over one folder must propose
    the same list in the same order, whatever order the filesystem yielded."""
    labels = {
        "a.pdf": ["MV32 SCHEDULE", "MI20 SCHEDULE"],
        "b.pdf": ["MV32 COST", "MI20 COST"],
        "c.pdf": ["MV32 RISK", "MI20 RISK"],
    }
    names = ["MV32-1", "MV32-2", "MI20-1", "MI20-2"]
    forward = propose_tokens(labels, names)
    # Reversed WITHIN each document as well as across them. Reversing document
    # order alone changes nothing: every document here lists MV32 before MI20,
    # so first-seen order — and any ordering that leans on it — survives. The
    # two tokens are deliberately tied on label count, so only a total sort key
    # can separate them.
    backward = propose_tokens(
        {doc: list(reversed(ls))
         for doc, ls in reversed(list(labels.items()))},
        list(reversed(names)))
    assert forward == backward
    assert len(forward) >= 2


def test_a_matter_with_no_repeated_identifier_proposes_nothing():
    """The empty proposal is the safe one: no tokens means fewer sections
    recognized, never a page dropped. Measured on the real 11-file GTG folder,
    which proposes nothing at all."""
    assert propose_tokens(
        {"a.pdf": ["INTRODUCTION"], "b.pdf": ["SUMMARY"]},
        ["a", "b"],
    ) == ()


def test_an_empty_matter_proposes_nothing():
    assert propose_tokens({}, []) == ()


# --- the seam: what the operator types is what the run uses -----------------


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def setup(app):
    window = MainWindow(pipeline=MockPipeline())
    yield window.setup
    window.close()


def test_the_operators_edit_beats_the_proposal(setup):
    setup.set_proposed_tokens(("TOPSIDE", "MV32"))
    assert setup.project_tokens() == ("TOPSIDE", "MV32")
    setup._tokens.setText("MV32, BOMESC")
    assert setup.project_tokens() == ("MV32", "BOMESC")
    assert setup.request().project_tokens == ("MV32", "BOMESC")


def test_a_late_proposal_does_not_discard_a_typed_correction(setup):
    """The proposal is computed by opening every PDF's outline, so it can land
    after the operator has already typed. Overwriting them there would silently
    undo the correction that is the entire point of D-39."""
    setup._tokens.setText("BOMESC")
    setup.set_proposed_tokens(("TOPSIDE", "MV32"))
    assert setup.project_tokens() == ("BOMESC",)


def test_an_empty_proposal_says_so_rather_than_showing_a_blank_box(setup):
    setup.set_proposed_tokens(())
    assert setup.project_tokens() == ()
    assert "No project names found" in setup._tokens_hint.text()


def test_whitespace_and_empty_entries_are_not_proposed_as_tokens(setup):
    """An operator types a trailing comma. A run input of `''` would strip the
    empty string from every label, which is a no-op — but it would also enter
    the hashed identity, so two runs the operator considers identical would get
    different ids."""
    setup._tokens.setText("  MV32 , , BOMESC,  ")
    assert setup.project_tokens() == ("MV32", "BOMESC")


def _settle(window, deadline_ms: int = 5000) -> None:
    """Spin the event loop until the proposal thread has delivered.

    A bare `processEvents()` would pass whether or not the worker ever ran,
    which is the same defect as a `-k` that matches nothing.
    """
    from PySide6.QtCore import QDeadlineTimer, QElapsedTimer  # noqa: PLC0415

    clock = QElapsedTimer()
    clock.start()
    while window._token_jobs and clock.elapsed() < deadline_ms:
        for thread, _ in list(window._token_jobs):
            thread.wait(QDeadlineTimer(50))
        QApplication.processEvents()
    QApplication.processEvents()
    assert clock.elapsed() < deadline_ms, "the proposal thread never finished"


def test_a_folder_that_cannot_be_read_proposes_nothing_and_does_not_raise(app):
    """Non-fatal in the safe direction — a matter DocIQ cannot open must not
    stop the operator setting up a run."""

    class Exploding(MockPipeline):
        def propose_project_tokens(self, source):
            raise OSError("no such folder")

    window = MainWindow(pipeline=Exploding())
    try:
        window.setup._source.setText("nowhere")
        window._propose_tokens("nowhere")
        _settle(window)
        assert window.setup.project_tokens() == ()
    finally:
        window.close()


def test_the_proposal_does_not_block_the_window(app):
    """It opens every PDF in the tree for its outline — 2.79s over a 2,528-file
    tree, measured. On the GUI thread that is a three-second hang at the moment
    the operator picks a folder."""
    import threading  # noqa: PLC0415

    gui_thread = threading.current_thread().ident
    ran_on: list[int] = []

    class Slow(MockPipeline):
        def propose_project_tokens(self, source):
            ran_on.append(threading.current_thread().ident)
            return ("MV32",)

    window = MainWindow(pipeline=Slow())
    try:
        window.setup._source.setText(r"D:\m")
        window._propose_tokens(r"D:\m")
        _settle(window)
        assert ran_on, "the proposal never ran"
        assert ran_on[0] != gui_thread
        assert window.setup.project_tokens() == ("MV32",)
    finally:
        window.close()


def test_a_proposal_for_a_folder_the_operator_left_is_discarded(app):
    """Pick folder A, then folder B before A answers. Filling the field with
    A's names while B is selected is a confident, wrong and entirely silent
    statement about which matter is being processed."""
    window = MainWindow(pipeline=MockPipeline())
    try:
        window.setup._source.setText(r"D:\matter-B")
        window._tokens_proposed(r"D:\matter-A", ("MV32", "BOMESC"))
        assert window.setup.project_tokens() == ()
        window._tokens_proposed(r"D:\matter-B", ("MI20",))
        assert window.setup.project_tokens() == ("MI20",)
    finally:
        window.close()


# --- the identity: one spelling per behavior (A-19) ------------------------


def test_spellings_of_one_token_set_are_one_run_identity():
    """Matching folds case and ignores order, so these are all the same run.
    Before canonicalization they were five different run identities — an
    identity that moves when the reduction does not is a false "different
    configuration" the next time two runs are compared."""
    from dataclasses import replace  # noqa: PLC0415

    from dociq.contracts import RunConfig, run_identity  # noqa: PLC0415

    base = RunConfig(source_root=r"D:\m", output_root=r"D:\m\out")
    spellings = [
        ("MV32", "BOMESC"),
        ("BOMESC", "MV32"),
        ("mv32", "bomesc"),
        ("MV32", "BOMESC", "MV32"),
        (" MV32 ", "BOMESC", ""),
    ]
    ids = {run_identity(replace(base, project_tokens=t)) for t in spellings}
    assert len(ids) == 1


def test_a_genuinely_different_token_set_still_moves_the_identity():
    """The other half. Canonicalization must not flatten a real difference —
    with `MV32` supplied a label normalizes to a family that matches, and
    without it the page keeps."""
    from dataclasses import replace  # noqa: PLC0415

    from dociq.contracts import RunConfig, run_identity  # noqa: PLC0415

    base = RunConfig(source_root=r"D:\m", output_root=r"D:\m\out")
    one = run_identity(replace(base, project_tokens=("MV32",)))
    two = run_identity(replace(base, project_tokens=("MV32", "BOMESC")))
    none = run_identity(base)
    assert len({one, two, none}) == 3


def test_the_config_stores_the_canonical_spelling_not_the_typed_one():
    """Correct by construction: the value a later stage reads is already
    canonical, so no consumer has to remember to fold it."""
    from dociq.contracts import RunConfig  # noqa: PLC0415

    config = RunConfig(source_root=r"D:\m", output_root=r"D:\m\out",
                       project_tokens=(" mv32 ", "BOMESC", "", "MV32"))
    assert config.project_tokens == ("BOMESC", "MV32")


def test_same_named_files_in_different_folders_are_two_documents(tmp_path, app):
    """`min_documents` counts documents, so a key that collides undercounts the
    spread and quietly withholds a name. No folder in this corpus collides —
    which selects nothing: the failure would arrive on the first matter that
    does, and it would arrive silently."""
    import fitz  # noqa: PLC0415

    from dociq.adapter import RealPipeline  # noqa: PLC0415

    for folder in ("jan", "feb"):
        d = tmp_path / folder
        d.mkdir()
        doc = fitz.open()
        doc.new_page()
        doc.new_page()
        doc.set_toc([[1, "MV32 PROGRESS", 1], [1, "MV32 SCHEDULE", 2]])
        doc.save(str(d / "MV32 Weekly Report.pdf"))
        doc.close()

    assert "MV32" in RealPipeline().propose_project_tokens(str(tmp_path))


def test_the_mocks_proposal_is_what_the_rule_actually_returns():
    """The mock ships a literal because the GUI may not import a pipeline
    package (`test_import_graph`). This is the pin that keeps the literal
    honest: change the mock corpus or the rule, and it fails rather than
    quietly showing a stale list."""
    from pathlib import PurePosixPath  # noqa: PLC0415

    from dociq.gui.mock_pipeline import (  # noqa: PLC0415
        _CORPUS,
        MOCK_PROJECT_TOKENS,
        MockPipeline,
    )

    stems = [PurePosixPath(rel).stem for rel, *_ in _CORPUS]
    recomputed = propose_tokens(
        {rel: [PurePosixPath(rel).stem] for rel, *_ in _CORPUS}, stems)
    assert MOCK_PROJECT_TOKENS == recomputed
    assert MockPipeline().propose_project_tokens("anywhere") == recomputed
