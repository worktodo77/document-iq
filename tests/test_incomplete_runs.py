"""Runs that did NOT complete — Codex review #1, finding B-1.

The finding: a disk-preflight failure returned an empty ``RunResult`` and a
cancellation returned partial documents, and the pipeline could tell neither
from a completed walk. Both went on through ID assignment, the *stale
deliverable purge*, emission, accounting and the manifest, and both could
report ``ok=True`` — zero pages in equals zero kept plus zero dropped. A failed
disk check therefore deleted the last complete reduction of a matter and wrote
an empty set over it.

So the tests here are mostly about what is NOT on disk afterwards. The
byte-for-byte survival of a previous complete run is the assertion that matters;
everything else is the disclosure that makes the refusal auditable.

``tests/test_pipeline.py`` covers the completed path. This file covers the
class of paths that end early, and :func:`test_every_early_return_in_the_walk_is_enumerated_here`
is the guard that keeps the class closed.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from dociq import pipeline
from dociq.contracts import ContractViolation, RunConfig
from dociq.emit import paths as emit_paths
from dociq.emit.paths import OutputLayout
from dociq.ingest import extract as ex
from dociq.ingest import walker
from dociq.profiles.model import OperatorStamp
from dociq.runstate import COMPLETED, INCOMPLETE_DIR, RunTermination, TerminalStatus
from dociq.verify import manifest as mf

from .conftest import FIXTURES

STAMP = OperatorStamp("test", "2026-07-30T00:00:00Z", "test-host")


def _run(out: Path, *, source: Path | str = FIXTURES, **walk_kw):
    cfg = RunConfig(
        source_root=str(source),
        output_root=str(out),
        ocr_engine_version=ex.ocr_engine_version(),
    )
    return pipeline.run(
        cfg,
        pipeline.PipelineOptions(
            walk=walker.WalkOptions(ocr_enabled=False, resume=False, **walk_kw),
            matter_name="fixture corpus",
            stamp=STAMP,
        ),
    )


def _fingerprint(root: Path) -> dict[str, str]:
    """Every file under ``root`` and its hash, except the incomplete-run record.

    The record is excluded because it is the one thing an aborted run is
    *supposed* to add; including it would make the survival assertion below
    pass or fail for the wrong reason.
    """
    return {
        p.relative_to(root).as_posix(): mf.sha256_file(p)
        for p in sorted(root.rglob("*"))
        if p.is_file() and INCOMPLETE_DIR not in p.relative_to(root).parts
    }


# ---------------------------------------------------------------------------
# The three abort paths, and the enumeration that keeps them a closed class
# ---------------------------------------------------------------------------


def _blocked_by_disk(monkeypatch) -> None:
    monkeypatch.setattr(
        walker, "preflight_disk",
        lambda entries, output_root: "Insufficient disk to process ~9.9 GB.")


def test_a_failed_disk_preflight_is_blocked_and_writes_nothing(tmp_path, monkeypatch):
    out = tmp_path / "matter"
    good = _run(out)
    assert good.ok and good.published
    before = _fingerprint(out)
    assert before, "the first run wrote nothing to protect"

    _blocked_by_disk(monkeypatch)
    blocked = _run(out)

    assert blocked.termination.status is TerminalStatus.BLOCKED
    assert blocked.published is False
    assert blocked.ok is False
    assert _fingerprint(out) == before, (
        "a blocked run altered the previous complete run's deliverables")


@pytest.mark.parametrize("where", ["inside", "same", "parent"])
def test_a_run_may_not_eat_its_own_output(tmp_path, where):
    """MEASURED before it was fixed, not hypothesized.

    A one-document matter, run twice with the output folder inside the source
    folder, inventoried 1 document the first time and **6** the second: `a.txt`
    plus the first run's `clean_text/DIQ-000001.txt`, its `document_index.csv`
    and three files of its `upload_package/`. The page count, the token figures
    and the index then describe a corpus that is partly DocIQ's own output, and
    every re-run compounds it. Only `.dociq/` was ever excluded, because it is
    scratch.

    All three overlapping arrangements are refused, not just the one that was
    measured — the reverse nesting matters too, because the swap removes the
    previous run's deliverables BY NAME and a source folder underneath the
    output root could have an operator's own `document_index.csv` deleted.
    """
    matter = tmp_path / "matter"
    (matter / "docs").mkdir(parents=True)
    (matter / "docs" / "a.txt").write_text("Notice of delay.\n",
                                           encoding="utf-8", newline="\n")
    source, out = {
        "inside": (matter, matter / "out"),
        "same": (matter, matter),
        "parent": (matter / "docs", matter),
    }[where]

    blocked = _run(out, source=source)

    assert blocked.termination.status is TerminalStatus.BLOCKED
    assert blocked.published is False
    assert blocked.ok is False
    assert "will not run this way" in blocked.termination.reason
    assert not (out / "sources.json").exists(), "a refused run published"
    assert (matter / "docs" / "a.txt").is_file(), "a refused run deleted input"


def test_the_overlap_check_resolves_the_path_rather_than_comparing_strings(tmp_path):
    """The same folder reaches DocIQ as ``C:\\m``, ``c:\\m\\``, ``C:\\m\\.`` and
    ``C:\\m\\docs\\..`` — a string comparison calls three of those four a
    different folder, and the check would then pass on the arrangement it
    exists to refuse."""
    matter = tmp_path / "matter"
    (matter / "docs").mkdir(parents=True)
    (matter / "docs" / "a.txt").write_text("x\n", encoding="utf-8", newline="\n")

    for spelling in (matter / "out", matter / "docs" / ".." / "out",
                     Path(str(matter).upper()) / "out"):
        blocked = _run(spelling, source=matter)
        assert blocked.termination.status is TerminalStatus.BLOCKED, spelling


def test_an_unreachable_source_folder_is_blocked_not_an_empty_green_run(tmp_path):
    """The path that reaches publication without any early return at all.

    A mistyped path, a disconnected share and an unmounted volume all produce
    an empty scan, which used to be indistinguishable from a folder that
    genuinely holds nothing: the run went green, purged, and wrote an empty
    deliverable set over a good one.
    """
    out = tmp_path / "matter"
    good = _run(out)
    before = _fingerprint(out)

    blocked = _run(out, source=tmp_path / "not-a-folder")

    assert blocked.termination.status is TerminalStatus.BLOCKED
    assert blocked.ok is False
    assert _fingerprint(out) == before
    assert good.manifest.corpus_sha256  # the first run's claim still stands


def test_a_cancelled_run_publishes_nothing(tmp_path):
    out = tmp_path / "matter"
    _run(out)
    before = _fingerprint(out)

    cancelled = _run(out, cancelled=lambda: True)

    assert cancelled.termination.status is TerminalStatus.CANCELLED
    assert cancelled.published is False
    assert cancelled.ok is False
    assert _fingerprint(out) == before


def test_a_first_ever_blocked_run_writes_no_deliverable_at_all(tmp_path, monkeypatch):
    """The no-previous-run case. Nothing to protect, and still nothing to ship:
    an empty ``document_index.csv`` beside an empty ``sources.json`` is a
    complete-looking output set describing a run that never read a file."""
    out = tmp_path / "fresh"
    _blocked_by_disk(monkeypatch)
    blocked = _run(out)

    layout = OutputLayout.at(out)
    for path in (layout.sources_json, layout.index_csv, layout.processing_log,
                 layout.run_summary, layout.index_xlsx, layout.issued_ids,
                 out / mf.MANIFEST_NAME):
        assert not path.exists(), path
    assert list(layout.clean_text.glob("*.txt")) == []
    assert blocked.published is False


def _walk_run_returns() -> list[ast.Return]:
    """Every ``return`` belonging to ``walker.run`` itself.

    Its nested helpers (``timed``, ``emit_progress``, ``blocked_result``)
    return to the loop or to a caller inside ``run``, not to the pipeline.
    """
    tree = ast.parse(Path(walker.__file__).read_text(encoding="utf-8"))
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "run")
    nested = {
        id(r)
        for n in ast.walk(fn)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n is not fn
        for r in ast.walk(n)
        if isinstance(r, ast.Return)
    }
    return [n for n in ast.walk(fn)
            if isinstance(n, ast.Return) and id(n) not in nested]


def test_every_return_in_the_walk_carries_a_stamped_terminal_status():
    """The class, not the instances — restated for round-2 F-1.

    The earlier version of this test asserted that each early return set
    ``notes.termination`` nearby, and every early return did. It still missed
    the finding, because setting the out-parameter and stamping the
    :class:`~dociq.contracts.RunResult` are two different acts and only the
    first was checked: the walk reported ``blocked`` in ``RunNotes`` and
    ``completed`` in the machine contract, from the same return statement.

    So the invariant is now the stronger one. Every value ``walker.run``
    returns must come out of a termination's :meth:`~dociq.runstate.
    RunTermination.stamp` — either directly, or via the ``blocked_result``
    helper that does nothing else. A return that constructs a bare
    ``RunResult`` fails here and lands the author on this docstring, because
    the contract's COMPLETED default means such a return fails OPEN and
    silently.

    Asserted from the source, because behavior can only cover the paths
    someone thought to exercise — which is precisely how round 1 shipped this.
    """
    returns = _walk_run_returns()
    assert len(returns) == 5, (
        f"walker.run has {len(returns)} return statements; this test knows "
        "about 5 (four preflights and the normal return).")

    for r in returns:
        call = r.value
        assert isinstance(call, ast.Call), (
            f"walker.py:{r.lineno} does not return a call")
        fname = call.func
        stamped = (
            # notes.termination.stamp(RunResult(...))
            (isinstance(fname, ast.Attribute) and fname.attr == "stamp")
            # blocked_result(...) — which stamps, and does nothing else
            or (isinstance(fname, ast.Name) and fname.id == "blocked_result")
        )
        assert stamped, (
            f"walker.py:{r.lineno} returns a RunResult that was not stamped "
            "with a terminal status. It will take the contract's COMPLETED "
            "default and tell a consumer the opposite of the outcome.")


def test_blocked_result_sets_the_notes_and_the_contract_from_one_value():
    """The helper the guard above trusts. If it stopped setting either half,
    every early return would quietly go back to disagreeing with itself."""
    src = Path(walker.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "run")
    helper = next(n for n in ast.walk(fn)
                  if isinstance(n, ast.FunctionDef) and n.name == "blocked_result")
    body = ast.dump(helper)
    assert "attr='termination'" in body, (
        "blocked_result no longer sets notes.termination")
    assert "attr='stamp'" in body, (
        "blocked_result no longer stamps the RunResult")


@pytest.mark.parametrize(
    "status",
    [TerminalStatus.BLOCKED, TerminalStatus.CANCELLED],
)
def test_the_purge_refuses_any_run_that_did_not_complete(tmp_path, status):
    """The second and third independent defences.

    The pipeline returns before Stage 5 on an aborted run, so this raise is
    unreachable from the shipped path. It exists because the ordering is the
    kind of thing a refactor moves: the function that decides what gets deleted
    takes a proof of completion as a required argument and checks it, so
    destroying a prior corpus cannot be reached by rearranging call order.

    Sprint 2 moved the deletion itself into the staging swap
    (:func:`dociq.emit.paths.commit_staging`), so the guarded function is now the
    ENUMERATOR — and the swap adds a third defence of its own: it deletes
    nothing without a readiness marker, and only a run that reached the end of
    Stage 6 writes one.
    """
    layout = OutputLayout.at(tmp_path).ensure()
    victim = layout.index_csv
    victim.write_text("Doc ID\n", encoding="utf-8", newline="")

    with pytest.raises(ContractViolation):
        pipeline._stale_deliverables(
            layout, RunTermination(status, "stopped"))
    assert victim.exists(), "the refused enumeration deleted a file anyway"

    # Third defence: no marker, no deletion — whatever else has happened.
    assert emit_paths.commit_staging(layout) == ()
    assert victim.exists(), "the swap deleted a file with no readiness marker"

    listed = pipeline._stale_deliverables(layout, COMPLETED)
    assert "document_index.csv" in listed
    assert victim.exists(), "enumerating must not delete"


# ---------------------------------------------------------------------------
# The disclosure: an aborted run is refused, not silent
# ---------------------------------------------------------------------------


def test_an_aborted_run_records_itself_where_it_cannot_collide(tmp_path, monkeypatch):
    out = tmp_path / "matter"
    _run(out)
    _blocked_by_disk(monkeypatch)
    blocked = _run(out)

    quarantine = out / INCOMPLETE_DIR
    assert blocked.incomplete_dir == quarantine
    status = json.loads((quarantine / "run_status.json").read_text(encoding="utf-8"))
    assert status["terminal_status"] == "blocked"
    assert status["published"] is False
    assert status["complete"] is False
    assert "Insufficient disk" in status["terminal_status_reason"]

    # The aborted run's own log, under a name that cannot shadow the last
    # complete run's audit trail at the root.
    log = json.loads((quarantine / "processing_log.json").read_text(encoding="utf-8"))
    assert log["run"]["terminal_status"] == "blocked"
    assert (quarantine / "run_summary.pdf").exists()


def test_the_terminal_status_is_in_the_machine_readable_result_of_every_run(tmp_path):
    """Codex round 2 named this test by name, and it deserved it.

    It asserted ``PipelineOutcome.termination`` and the log — both of which were
    already right — and never ``PipelineOutcome.result.terminal_status``, which
    was wrong on every abort path. A test whose name promises "the machine-
    readable result" has to look at the machine-readable result.
    """
    completed = _run(tmp_path / "done")
    assert completed.termination == COMPLETED
    assert completed.termination.status is TerminalStatus.COMPLETED

    # The assertion the name promised, which was missing.
    assert completed.result.terminal_status is TerminalStatus.COMPLETED
    assert completed.result.terminal_status_reason == ""

    log = json.loads(
        completed.layout.processing_log.read_text(encoding="utf-8"))
    assert log["run"]["terminal_status"] == "completed"
    assert log["run"]["published"] is True
    # In `run`, never in `content`. A cancellation is a fact about the
    # invocation; hashing it would make an interrupted run and a clean one
    # differ inside a byte-identical claim about the CORPUS — and an incomplete
    # run publishes no corpus for a completed one to collide with (A-07).
    assert "terminal_status" not in json.dumps(log["content"])


# ---------------------------------------------------------------------------
# Round-2 F-1 — the machine contract must not contradict the outcome
# ---------------------------------------------------------------------------


def test_there_is_exactly_one_terminal_status_enumeration():
    """Round-2 F-1, first half.

    Amendment A-06 added :class:`dociq.contracts.TerminalStatus` while
    :mod:`dociq.runstate` still declared a value-identical one, so the walk
    carried one class and :class:`~dociq.contracts.RunResult` declared the
    other. Two enumerations spelled the same way compare ``==`` on their string
    values and ``is`` never — a consumer writing the identity check the typed
    status exists to enable gets ``False`` about two statuses that are the same
    status. Amendment A-07 leaves one definition.
    """
    from dociq import contracts, runstate

    assert runstate.TerminalStatus is contracts.TerminalStatus
    # Counted from the enum rather than pinned to a literal. The literal was 3
    # and A-15 added REFUSED, so the test failed for the one reason it must not:
    # a legitimate new member is not a second enumeration. What A-07 forbids is
    # runstate declaring its OWN members, which the identity comparison above
    # already establishes and this now states without a number to bump.
    members = {id(m) for m in contracts.TerminalStatus}
    assert members == {id(m) for m in runstate.TerminalStatus}
    assert len(members) == len(list(contracts.TerminalStatus))


@pytest.mark.parametrize("case", ["completed", "missing-root", "cancelled",
                                  "blocked-disk", "unlistable-root"])
def test_the_contract_status_agrees_with_the_outcome_on_every_run(
    tmp_path, monkeypatch, case
):
    """Round-2 F-1, second half — and it is the assertion round 1 missed.

    The predecessor of this test checked ``PipelineOutcome.termination`` and
    the processing log, both of which were right, and never checked
    ``PipelineOutcome.result.terminal_status``, which was wrong on all three
    abort paths. Codex's probe: ``RunNotes termination = blocked`` beside
    ``RunResult terminal_status = completed``. A consumer holding the machine
    contract — which is the object the contract freeze exists to make
    trustworthy — was told the opposite of the outcome wrapper.

    Every ending is exercised, the completed one included. A test that only
    looks at the failure modes cannot notice the day the *good* path stops
    agreeing with itself.
    """
    out = tmp_path / "matter"
    if case == "completed":
        outcome = _run(out)
        expected = TerminalStatus.COMPLETED
    elif case == "missing-root":
        outcome = _run(out, source=tmp_path / "not-a-folder")
        expected = TerminalStatus.BLOCKED
    elif case == "cancelled":
        outcome = _run(out, cancelled=lambda: True)
        expected = TerminalStatus.CANCELLED
    elif case == "blocked-disk":
        _blocked_by_disk(monkeypatch)
        outcome = _run(out)
        expected = TerminalStatus.BLOCKED
    else:
        _unlistable(monkeypatch, tmp_path / "corpus")
        outcome = _run(out, source=tmp_path / "corpus")
        expected = TerminalStatus.BLOCKED

    assert outcome.termination.status is expected
    assert outcome.result.terminal_status is expected, (
        "the machine-readable RunResult disagrees with the outcome wrapper "
        "about how the run ended")
    assert outcome.result.terminal_status_reason == outcome.termination.reason
    assert outcome.result.terminal_status.complete is (
        expected is TerminalStatus.COMPLETED)


# ---------------------------------------------------------------------------
# Round-2 F-2 — an inventory that could not be enumerated cannot publish
# ---------------------------------------------------------------------------


def _unlistable(monkeypatch, root: Path, *, only: str | None = None) -> Path:
    """A corpus at ``root`` where one directory refuses to list.

    ``only=None`` breaks the root itself (nothing is inventoried); ``only="sub"``
    breaks one subtree, so the walk returns a partial inventory — the case a
    warning used to be considered sufficient for.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "top.txt").write_text("top level", encoding="utf-8")
    sub = root / "sub"
    sub.mkdir(exist_ok=True)
    (sub / "buried.txt").write_text("under the broken folder", encoding="utf-8")

    target = root if only is None else root / only
    real = Path.iterdir

    def refuse(self):
        if self == target or str(self) == str(target):
            raise PermissionError(f"listing denied: {self}")
        return real(self)

    monkeypatch.setattr(Path, "iterdir", refuse)
    return root


def test_a_root_that_cannot_be_listed_blocks_the_run(tmp_path, monkeypatch):
    """Codex's probe verbatim: ``termination = completed, documents = 0`` with
    a warning attached. A warning does not make an incomplete corpus safe to
    publish, and this one published an EMPTY set over a complete one."""
    out = tmp_path / "matter"
    good = _run(out)
    assert good.published
    before = _fingerprint(out)
    assert before

    corpus = _unlistable(monkeypatch, tmp_path / "corpus")
    blocked = _run(out, source=corpus)

    assert blocked.termination.status is TerminalStatus.BLOCKED
    assert blocked.published is False
    assert blocked.ok is False
    assert not blocked.result.documents
    assert _fingerprint(out) == before, (
        "an un-enumerable folder replaced a complete prior corpus")
    assert any("could not be fully inventoried" in w
               for w in blocked.result.warnings)


def test_a_subtree_that_cannot_be_listed_blocks_the_run(tmp_path, monkeypatch):
    """The partial case, which is the more dangerous of the two.

    The root lists, one folder under it does not, and the walk comes back with
    real documents — so every downstream check passes, the accounting balances
    against itself, and the deliverables assert completeness over a corpus that
    is short by an unknown amount.
    """
    out = tmp_path / "matter"
    _run(out)
    before = _fingerprint(out)

    corpus = _unlistable(monkeypatch, tmp_path / "corpus", only="sub")
    blocked = _run(out, source=corpus)

    assert blocked.termination.status is TerminalStatus.BLOCKED
    assert blocked.published is False
    assert _fingerprint(out) == before


def test_a_folder_that_is_empty_but_readable_is_a_completed_run(tmp_path):
    """The boundary Codex made load-bearing, asserted so the F-2 fix cannot
    over-reach into it.

    "Successfully enumerated and contains no files" is a legitimate empty
    completed run, and it MAY replace prior deliverables. Only a folder DocIQ
    could not read is blocked. Without this test the safe fix for F-2 is to
    refuse every empty result, which would break the honest empty matter.
    """
    empty = tmp_path / "empty"
    empty.mkdir()
    outcome = _run(tmp_path / "matter", source=empty)

    assert outcome.termination.status is TerminalStatus.COMPLETED
    assert outcome.result.terminal_status is TerminalStatus.COMPLETED
    assert outcome.published is True
    assert outcome.result.documents == ()
    assert outcome.layout.processing_log.is_file()


def test_the_status_reaches_the_operator_facing_warning_list_first(tmp_path, monkeypatch):
    _blocked_by_disk(monkeypatch)
    blocked = _run(tmp_path / "matter")
    assert blocked.result.warnings
    assert blocked.result.warnings[0].startswith("RUN BLOCKED")


def test_the_pdf_states_the_run_status(tmp_path, monkeypatch):
    pypdf = pytest.importorskip("pypdf")
    completed = _run(tmp_path / "done")
    text = pypdf.PdfReader(str(completed.layout.run_summary)).pages[0].extract_text()
    assert "Run status: completed" in text

    _blocked_by_disk(monkeypatch)
    blocked = _run(tmp_path / "stopped")
    pdf = blocked.incomplete_dir / "run_summary.pdf"
    assert "RUN BLOCKED" in pypdf.PdfReader(str(pdf)).pages[0].extract_text()


def test_the_accounting_gate_fails_as_well_as_publication_being_withheld(tmp_path, monkeypatch):
    """Codex offered "prevent publication" OR "fail the gates". Both, so a
    consumer reading only ``accounting.ok`` — which is what
    ``PipelineOutcome.ok`` used to be derived from — still cannot mistake an
    aborted run for a good one."""
    _blocked_by_disk(monkeypatch)
    blocked = _run(tmp_path / "matter")
    assert not blocked.accounting.ok
    assert blocked.accounting.discrepancies[0].kind == "run-blocked"
    assert "PAGE ACCOUNTING FAILED" in blocked.accounting.render()


# ---------------------------------------------------------------------------
# The interaction with a LATER good run
# ---------------------------------------------------------------------------


def test_an_incomplete_record_never_makes_a_later_good_run_fail_its_own_gate(
        tmp_path, monkeypatch):
    out = tmp_path / "matter"
    with monkeypatch.context() as m:
        m.setattr(walker, "preflight_disk",
                  lambda entries, output_root: "Insufficient disk.")
        _run(out)
    assert (out / INCOMPLETE_DIR / "run_status.json").exists()

    good = _run(out)
    assert good.ok, good.manifest.render()
    assert good.manifest.unclassified == []
    # The record of a failed attempt does not sit beside a good output set.
    assert not (out / INCOMPLETE_DIR / "run_status.json").exists()
    assert any(r.startswith(f"{INCOMPLETE_DIR}/") for r in good.stale_removed), (
        good.stale_removed)


def test_the_manifest_classifies_an_incomplete_run_record_rather_than_flagging_it(
        tmp_path):
    out = tmp_path / "matter"
    _run(out)
    (out / INCOMPLETE_DIR).mkdir(parents=True, exist_ok=True)
    (out / INCOMPLETE_DIR / "run_status.json").write_text(
        "{}", encoding="utf-8", newline="")

    man = mf.build(out)
    assert man.unclassified == []
    assert f"{INCOMPLETE_DIR}/run_status.json" in man.excluded


def test_a_blocked_run_does_not_disturb_the_byte_identical_claim(tmp_path, monkeypatch):
    """The corpus hash of the surviving deliverables is the previous run's,
    unchanged — the aborted attempt did not rewrite the manifest either."""
    out = tmp_path / "matter"
    first = _run(out)
    with monkeypatch.context() as m:
        m.setattr(walker, "preflight_disk",
                  lambda entries, output_root: "Insufficient disk.")
        _run(out)
    assert mf.build(out).corpus_sha256 == first.manifest.corpus_sha256
