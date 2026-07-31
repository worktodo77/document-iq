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


def test_every_early_return_in_the_walk_is_enumerated_here():
    """The class, not the three instances.

    ``walker.run`` itself may return in exactly three places: two preflights
    and the cancellation-aware normal return. Each early return must set
    ``notes.termination`` before it returns, because a return that forgets to
    would fail OPEN — the pipeline would treat an aborted walk as a complete one
    and publish over a good corpus, which is the whole finding.

    Asserted from the source rather than by behaviour, because behaviour can
    only cover the paths someone thought to exercise. A fourth return added
    later fails this test and lands the author here.
    """
    src = Path(walker.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "run")

    # Returns of ``run`` itself. Its nested helpers (``timed``,
    # ``emit_progress``) return to the loop, not to the pipeline.
    nested = {
        id(r)
        for n in ast.walk(fn)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n is not fn
        for r in ast.walk(n)
        if isinstance(r, ast.Return)
    }
    returns = [n for n in ast.walk(fn)
               if isinstance(n, ast.Return) and id(n) not in nested]
    assert len(returns) == 3, (
        f"walker.run has {len(returns)} return statements; this test knows "
        "about 3 (two preflights and the normal return). A new one must set "
        "notes.termination or the pipeline will publish an aborted run.")

    assignments = {
        n.lineno for n in ast.walk(fn)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Attribute) and t.attr == "termination"
                for t in n.targets)
    }
    early = sorted(r.lineno for r in returns)[:-1]
    for lineno in early:
        assert any(lineno - 12 < a < lineno for a in assignments), (
            f"the early return at walker.py:{lineno} does not set "
            "notes.termination immediately before returning")


@pytest.mark.parametrize(
    "status",
    [TerminalStatus.BLOCKED, TerminalStatus.CANCELLED],
)
def test_the_purge_refuses_any_run_that_did_not_complete(tmp_path, status):
    """The second, independent defence.

    The pipeline returns before Stage 5 on an aborted run, so this raise is
    unreachable from the shipped path. It exists because the ordering is the
    kind of thing a refactor moves: the function that does the deleting takes a
    proof of completion as a required argument and checks it, so destroying a
    prior corpus cannot be reached by rearranging call order.
    """
    layout = OutputLayout.at(tmp_path).ensure()
    victim = layout.index_csv
    victim.write_text("Doc ID\n", encoding="utf-8", newline="")

    with pytest.raises(ContractViolation):
        pipeline._purge_stale_deliverables(
            layout, RunTermination(status, "stopped"))
    assert victim.exists(), "the refused purge deleted a file anyway"

    removed = pipeline._purge_stale_deliverables(layout, COMPLETED)
    assert "document_index.csv" in removed


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
    completed = _run(tmp_path / "done")
    assert completed.termination == COMPLETED
    assert completed.termination.status is TerminalStatus.COMPLETED
    log = json.loads(
        completed.layout.processing_log.read_text(encoding="utf-8"))
    assert log["run"]["terminal_status"] == "completed"
    assert log["run"]["published"] is True
    # In `run`, never in `content`: a cancellation is a fact about the
    # invocation, and hashing it would make an interrupted run and a clean one
    # differ inside the byte-identical claim.
    assert "terminal_status" not in json.dumps(log["content"])


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
    assert any(r.startswith(f"{INCOMPLETE_DIR}/") for r in good.stale_removed)


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
