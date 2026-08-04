"""Publication is REFUSED, not merely reported on — Codex review #2, B-1 and B-2.

Two findings, one sentence between them: *state was computed and then not acted
on*.

**B-1.** §4 Stage 6 computed page accounting and built the manifest, and then
called ``mark_ready`` and ``commit_staging`` unconditionally. A red set replaced
the last good deliverables, and ``PipelineOutcome.ok`` reported the fact
afterwards to an in-memory caller who need never look. The heading said "the
gates" and the Sprint-2 relay said the set was "gated there, marked, then
swapped"; neither was true.

**B-2.** The readiness marker was written with a plain ``open(...).write``, so a
crash mid-write could leave truncated JSON, and ``commit_staging`` caught
``ValueError`` and substituted an EMPTY supersede list. Run 1 publishes 100
files; run 2 legitimately produces 80, stages them, and dies writing the marker;
the next run moves 80 files over the destination, leaves run 1's other 20, and
deletes the marker. The folder is then a hundred-file evidence set carrying an
eighty-file manifest with nothing on disk saying so — and the recovery that
created that state deleted the only signal that would have disclosed it.

Every test below was watched RED against the pre-fix code (the reversions are
named in ``docs/verification/codex_r2_gate_2026-08-04.md``). The B-1 cases are an
ENUMERATION of the gate class rather than one repro: one per check Stage 6
computes, so a gate added between any two of them has neighbours.
"""

from __future__ import annotations

import hashlib
import json
import shutil

import pytest

from dociq import pipeline
from dociq.contracts import RunConfig, TerminalStatus
from dociq.emit import paths as emit_paths
from dociq.emit.paths import OutputLayout
from dociq.ingest import extract as ex
from dociq.ingest import walker
from dociq.profiles.model import OperatorStamp
from dociq.verify import accounting
from dociq.verify import manifest as mf

from .conftest import FIXTURES

STAMP = OperatorStamp("test", "2026-07-30T00:00:00Z", "test-host")


def _run(out, source=None, **kw):
    cfg = RunConfig(
        source_root=str(source or FIXTURES),
        output_root=str(out),
        ocr_engine_version=ex.ocr_engine_version(),
    )
    return pipeline.run(
        cfg,
        pipeline.PipelineOptions(
            walk=walker.WalkOptions(ocr_enabled=False, resume=False),
            matter_name="fixture corpus",
            stamp=STAMP,
            **kw,
        ),
    )


def _deliverables(root) -> dict[str, str]:
    """Every published file in the matter folder, hashed.

    DocIQ's own run state is excluded because it is scratch. ``incomplete_run/``
    is excluded because it is the RECORD of the refusal, not a deliverable — a
    refused run is required to add it, and a comparison that counted it could
    never distinguish "the previous output survived" from "nothing happened".
    """
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith(f"{emit_paths.STATE_DIRNAME}/"):
            continue
        if rel.startswith("incomplete_run/"):
            continue
        out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


# ---------------------------------------------------------------------------
# B-1 — the gate. One case per check Stage 6 computes.
# ---------------------------------------------------------------------------


def _force_accounting_red(monkeypatch):
    real = accounting.check

    def red(result):
        rep = real(result)
        rep.discrepancies.append(accounting.Discrepancy(
            "LI-00003.pdf", "page-count", "18 pages in, 17 accounted for"))
        return rep

    monkeypatch.setattr("dociq.pipeline.accounting.check", red)


def _force_unclassified(monkeypatch):
    """The scenario Codex named: a Stage-5 emitter writes an artifact the
    byte-identical claim does not classify. Injected through a real emitter so
    the file lands in staging by the real write path, at the real time."""
    real = pipeline.write_processing_log

    def also_write_something_new(bundle, layout):
        emit_paths.write_text_deterministic(
            layout.root / "exhibit_bundle.idx", "a new emitter's artifact\n")
        return real(bundle, layout)

    monkeypatch.setattr("dociq.pipeline.write_processing_log",
                        also_write_something_new)


def _force_out_of_order(monkeypatch):
    monkeypatch.setattr("dociq.pipeline.corpus_sort_check", lambda result: False)


GATES = {
    "accounting": _force_accounting_red,
    "unclassified-output": _force_unclassified,
    "corpus-order": _force_out_of_order,
}


@pytest.mark.parametrize("gate", sorted(GATES))
def test_a_red_stage_6_gate_refuses_to_publish(tmp_path, monkeypatch, gate):
    """FAIL-BEFORE: with the unconditional ``mark_ready`` / ``commit_staging``
    pair restored, every one of these three publishes the red set over the good
    one and returns ``ok=False`` about a folder that has already been replaced.

    What is asserted is deliberately not "``ok`` is False" — that was true
    before the fix and is exactly the observation Codex refused to accept as a
    gate. It is that the DISK is unchanged.
    """
    out = tmp_path / "matter"
    good = _run(out)
    assert good.published and good.ok
    before = _deliverables(out)
    assert before, "the first run published nothing to protect"

    GATES[gate](monkeypatch)
    refused = _run(out)

    assert not refused.published, "a run that failed its own gates published"
    assert not refused.ok
    assert refused.termination.status is TerminalStatus.BLOCKED
    assert _deliverables(out) == before, (
        f"the {gate} gate went red and the matter folder changed anyway; the "
        f"last good deliverables must survive a refused run byte for byte")
    assert not emit_paths.pending_swap(OutputLayout.at(out)), (
        "a refused run wrote a readiness marker — the one thing that "
        "authorises deleting the previous run's files")
    assert not (out / emit_paths.STATE_DIRNAME / emit_paths.STAGING_DIRNAME).exists(), (
        "the refused set was left staged, where a later hand could mark it ready")


@pytest.mark.parametrize("gate", sorted(GATES))
def test_a_refused_run_says_so_on_disk_and_names_the_gate(tmp_path, monkeypatch, gate):
    """A refusal an operator cannot see is the finding again, one layer out.

    The refusal has to be legible from the FOLDER, not from a return value: the
    shipped GUI is a windowed executable and the caller's ``PipelineOutcome`` is
    exactly the in-memory value Codex declined to count.
    """
    out = tmp_path / "matter"
    _run(out)
    GATES[gate](monkeypatch)
    refused = _run(out)

    assert refused.incomplete_dir is not None
    status = json.loads(
        (refused.incomplete_dir / "run_status.json").read_text(encoding="utf-8"))
    assert status["published"] is False
    assert status["complete"] is False
    assert "Stage 6 REFUSED" in status["terminal_status_reason"]
    assert "NO DELIVERABLES WERE WRITTEN" in status["headline"]

    # The accounting report names WHICH gate refused, so a reader of that alone
    # learns more than "something did".
    kinds = {d.kind for d in refused.accounting.discrepancies}
    assert f"refused-{gate}" in kinds, sorted(kinds)

    log = json.loads(
        (refused.incomplete_dir / "processing_log.json").read_text(encoding="utf-8"))
    assert log["run"]["published"] is False
    assert any("PUBLICATION REFUSED" in n for n in log["run"]["invocation_notes"])


def test_a_refused_run_keeps_the_diagnosis_it_earned(tmp_path, monkeypatch):
    """A Stage-6 refusal knows things a Stage-1 abort cannot, and must not be
    flattened into one.

    ``_abort`` blanks the assignment with "no identifier was issued" — correct
    for a walk that stopped before Stage 3b, and false about a run that assigned
    an identifier to every document and was then refused publication. This is
    the reason ``_abort`` grew overrides instead of the refusal growing its own
    unpublishing path."""
    out = tmp_path / "matter"
    _force_accounting_red(monkeypatch)
    refused = _run(out)

    assert not refused.published
    assert refused.assignment.assignments, (
        "the refused run reported no identifiers for documents it identified")
    assert refused.result.documents, "the refused run reported an empty corpus"
    assert refused.manifest.deterministic, (
        "the refused run reported an empty manifest of the set it built")


def test_the_gate_does_not_reach_the_hashed_content(tmp_path, monkeypatch):
    """Criterion 7 survives the fix.

    A refusal is a fact about an invocation. Two clean runs into two
    destinations must still be byte-identical, and neither the gate nor the
    refusal vocabulary may appear in anything hashed."""
    a, b = tmp_path / "a", tmp_path / "b"
    first, second = _run(a), _run(b)
    assert first.published and second.published
    assert first.manifest.corpus_sha256 == second.manifest.corpus_sha256
    for rel in ("sources.json", "document_index.csv"):
        assert (a / rel).read_bytes() == (b / rel).read_bytes(), rel
    log = json.loads((a / "processing_log.json").read_text(encoding="utf-8"))
    assert "refused" not in json.dumps(log["content"]).lower()


# ---------------------------------------------------------------------------
# B-2 — the marker. Atomic to write, fail-closed to read.
# ---------------------------------------------------------------------------


def test_a_crash_while_writing_the_marker_leaves_no_marker(tmp_path, monkeypatch):
    """FAIL-BEFORE: with ``mark_ready`` calling ``write_text_deterministic``
    directly, this leaves ``staging_ready.json`` holding half a JSON document —
    which is the exact input the permissive reader then guessed through."""
    out = tmp_path / "matter"
    layout = OutputLayout.at(out).ensure()

    real_open = emit_paths.Path.open

    def die_halfway(self, *a, **kw):
        fh = real_open(self, *a, **kw)
        if self.name.startswith(emit_paths.MARKER_NAME):
            real_write = fh.write

            def half(text):
                real_write(text[: len(text) // 2])
                raise KeyboardInterrupt("the process was killed mid-write")

            fh.write = half  # type: ignore[method-assign]
        return fh

    monkeypatch.setattr(emit_paths.Path, "open", die_halfway)
    with pytest.raises(KeyboardInterrupt):
        emit_paths.mark_ready(layout, ("document_index.csv",))
    monkeypatch.undo()

    state = out / emit_paths.STATE_DIRNAME
    assert not emit_paths.pending_swap(layout), (
        "a half-written marker exists, and its existence authorises deletion")
    litter = [p.name for p in state.glob("*") if p.is_file()]
    assert litter == [], f"the failed write left {litter} beside the marker"


def test_a_marker_that_survives_is_always_complete(tmp_path):
    """The other half of the same property: what is on disk after a successful
    write is the whole document, and it round-trips."""
    out = tmp_path / "matter"
    layout = OutputLayout.at(out).ensure()
    marker = emit_paths.mark_ready(layout, ("document_index.csv", "clean_text"))
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["superseded"] == ["clean_text", "document_index.csv"]
    assert payload["staging"] == emit_paths.STAGING_DIRNAME
    assert marker.read_bytes().endswith(b"}\n")


@pytest.mark.parametrize(
    "corrupt",
    [
        pytest.param('{"staging": "staging", "superseded": ["clean',
                     id="truncated-mid-write"),
        pytest.param("", id="zero-length"),
        pytest.param('{"staging": "staging"}', id="no-superseded-key"),
        pytest.param('{"staging": "staging", "superseded": "clean_text"}',
                     id="superseded-not-a-list"),
        pytest.param('{"staging": "staging", "superseded": [17]}',
                     id="entry-not-a-string"),
        pytest.param('["staging"]', id="top-level-not-an-object"),
        pytest.param('{"staging": "staging", "superseded": ["../../victim.txt"]}',
                     id="entry-escapes-the-matter-folder"),
        pytest.param('{"staging": "staging", "superseded": ["C:/Windows/notepad.exe"]}',
                     id="entry-is-absolute"),
    ],
)
def test_an_unreadable_marker_moves_and_deletes_nothing(tmp_path, corrupt):
    """FAIL-BEFORE: with ``except (OSError, ValueError): superseded = ()``
    restored, the first six of these move the staged set into the matter folder,
    delete the marker, and report success.

    The last two are the same defect one layer down and were never handled at
    all: a supersede entry was joined to the matter root and unlinked unchecked,
    so a corrupt or hand-edited marker could delete outside the output folder.
    """
    out = tmp_path / "matter"
    layout = OutputLayout.at(out).ensure()
    (out / "document_index.csv").write_text("Doc ID\n", encoding="utf-8", newline="")
    staging = out / emit_paths.STATE_DIRNAME / emit_paths.STAGING_DIRNAME
    (staging / "clean_text").mkdir(parents=True)
    (staging / "sources.json").write_text("{}\n", encoding="utf-8", newline="")
    victim = tmp_path / "victim.txt"
    victim.write_text("not DocIQ's to delete\n", encoding="utf-8", newline="")

    marker = out / emit_paths.STATE_DIRNAME / emit_paths.MARKER_NAME
    marker.write_text(corrupt, encoding="utf-8", newline="")
    before = _deliverables(out)

    with pytest.raises(emit_paths.PendingSwapUnreadable) as caught:
        emit_paths.commit_staging(layout)

    assert "REFUSING" in str(caught.value)
    assert emit_paths.STAGING_DIRNAME in str(caught.value), (
        "the refusal must name where the complete set is waiting")
    assert _deliverables(out) == before, "the refused swap changed the folder"
    assert marker.is_file(), "the refused swap deleted the evidence of itself"
    assert (staging / "sources.json").is_file(), (
        "the refused swap moved the staged set anyway")
    assert victim.is_file(), "a marker entry deleted a file outside the matter folder"


def test_mark_ready_refuses_to_record_an_escaping_supersede_entry(tmp_path):
    """Checked on the way in as well as on the way out. The marker is the only
    input to a deletion, so both ends of it are validated rather than one."""
    layout = OutputLayout.at(tmp_path / "matter").ensure()
    for bad in ("../victim.txt", "C:/Windows/notepad.exe", "/etc/passwd", ""):
        with pytest.raises(ValueError):
            emit_paths.mark_ready(layout, (bad,))
    assert not emit_paths.pending_swap(layout), (
        "a marker was written before its entries were validated")


def test_a_truncated_marker_cannot_publish_a_mixed_set_on_a_shrinking_rerun(tmp_path):
    """Codex's scenario, at full size and end to end.

    Run 1 publishes the whole fixture corpus. Run 2 legitimately SHRINKS the
    output — a smaller source set, so fewer ``clean_text/`` files — finishes
    staging, and dies while writing the marker. The next run must not be able to
    turn that into a folder holding run 2's files beside run 1's leftovers.

    FAIL-BEFORE: with the permissive read restored, the next run moves run 2's
    files over the destination, leaves every ``clean_text/`` file run 1 wrote
    that run 2 did not, deletes the marker, and reports a published, ``ok`` run.
    The folder then carries a manifest describing a set it is not.
    """
    out = tmp_path / "matter"
    big = _run(out)
    assert big.published
    before = _deliverables(out)
    big_texts = {r for r in before if r.startswith("clean_text/")}

    # A genuinely smaller run: two source documents instead of fourteen.
    small_src = tmp_path / "small_source"
    small_src.mkdir()
    for name in ("08_daily_log.txt", "07_ncr_log.csv"):
        shutil.copy2(FIXTURES / name, small_src / name)
    small_out = tmp_path / "small"
    assert _run(small_out, source=small_src).published
    small_texts = {
        p.relative_to(small_out).as_posix()
        for p in (small_out / "clean_text").rglob("*") if p.is_file()
    }
    assert len(small_texts) < len(big_texts), (
        "the rerun did not actually shrink, so this proves nothing")

    # Run 2 stages its complete, smaller set — and dies writing the marker.
    staging = out / emit_paths.STATE_DIRNAME / emit_paths.STAGING_DIRNAME
    shutil.copytree(small_out, staging,
                    ignore=shutil.ignore_patterns(emit_paths.STATE_DIRNAME))
    marker = out / emit_paths.STATE_DIRNAME / emit_paths.MARKER_NAME
    marker.write_text('{"staging": "staging", "superseded": ["clean_te',
                      encoding="utf-8", newline="")

    # The recovery IN ISOLATION first, because that is where the mixing happens
    # and an end-to-end assertion cannot see it: run 3 walks the same source as
    # run 1, so it republishes the same seventeen files either way and the
    # folder-comparison below is satisfied by a mixed set and a clean one alike.
    # Measured on the pre-fix code at this point: seventeen clean_text files
    # visible, a manifest claiming four, fifteen stale survivors, marker deleted.
    with pytest.raises(emit_paths.PendingSwapUnreadable):
        emit_paths.recover_pending(OutputLayout.at(out))
    mid = {
        p.relative_to(out).as_posix()
        for p in (out / "clean_text").rglob("*") if p.is_file()
    }
    assert mid == big_texts, (
        f"the recovery published a MIXED set: {len(mid)} clean_text files "
        f"visible, from a run that produced {len(small_texts)}")

    third = _run(out)

    assert not third.published, "a run published on top of an unreadable swap"
    assert third.termination.status is TerminalStatus.BLOCKED
    assert "REFUSING" in third.termination.reason
    assert _deliverables(out) == before, (
        "the visible matter folder is no longer the last good set — the mixed "
        "set this test exists to forbid")
    assert marker.is_file(), "the marker that records the pending swap was deleted"
    assert (staging / "sources.json").is_file(), (
        "the complete staged set was discarded by a run that refused to use it")

    # And it is recoverable rather than a dead end: a readable marker puts the
    # folder back on a road the code can reason about, and the shrink then
    # happens properly — run 1's extra clean_text files are REMOVED, not left.
    superseded = sorted(
        p.relative_to(out).as_posix()
        for p in out.iterdir()
        if p.name not in (emit_paths.STATE_DIRNAME, "doc_ids_issued.json")
    )
    emit_paths.mark_ready(OutputLayout.at(out), tuple(superseded))
    assert emit_paths.recover_pending(OutputLayout.at(out))
    survived = {
        p.relative_to(out).as_posix()
        for p in (out / "clean_text").rglob("*") if p.is_file()
    }
    assert survived == small_texts, (
        f"the roll-forward left {sorted(survived - small_texts)} behind from "
        f"the larger run")


# ---------------------------------------------------------------------------
# The CLASS, not the two repros
# ---------------------------------------------------------------------------
#
# B-1 and B-2 are one sentence: state was computed and then not acted on. The
# two probes below hold the class rather than the instances.


_REAL_MF_BUILD = mf.build


def _force_no_log_hash(monkeypatch, out):
    """The class-B sibling: `manifest._log_content_hash` returns None when the
    processing log cannot be read, and `corpus_sha256` folds that in as `""`."""
    def build(root, config=None):
        man = _REAL_MF_BUILD(root, config=config)
        man.log_content_sha256 = None
        return man

    monkeypatch.setattr("dociq.pipeline.mf.build", build)
    return {}


def _force_unreadable_marker(monkeypatch, out):
    layout = OutputLayout.at(out).ensure()
    staging = out / emit_paths.STATE_DIRNAME / emit_paths.STAGING_DIRNAME
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "sources.json").write_text("{}\n", encoding="utf-8", newline="")
    (out / emit_paths.STATE_DIRNAME / emit_paths.MARKER_NAME).write_text(
        '{"superseded": ["clean', encoding="utf-8", newline="")
    return {}


def _force_blocked_walk(monkeypatch, out):
    """A source root DocIQ cannot reach — the oldest unpublishable route there
    is, closed by Codex review #1's B-1 and included here on purpose."""
    return {"source": out.parent / "no_such_source_folder"}


UNPUBLISHABLE = {
    # Every route by which a run can end without publishing, and how to force
    # it. Enumerated rather than sampled: the invariant below is only as wide as
    # this dict, so a route missing from it is a hole in the probe.
    "walk-blocked": _force_blocked_walk,
    "gate-accounting": lambda mp, out: (_force_accounting_red(mp), {})[1],
    "gate-unclassified": lambda mp, out: (_force_unclassified(mp), {})[1],
    "gate-corpus-order": lambda mp, out: (_force_out_of_order(mp), {})[1],
    "gate-log-content-hash": _force_no_log_hash,
    "unreadable-swap-marker": _force_unreadable_marker,
}


@pytest.mark.parametrize("route", sorted(UNPUBLISHABLE))
def test_a_run_that_is_not_ok_never_published(tmp_path, monkeypatch, route):
    """THE class invariant: ``ok is False`` implies ``published is False``.

    Both findings are instances of its violation, and it is what a future
    regression would break first. Before the fix it was reachable and measured:
    a forced page-accounting discrepancy produced ``published=True, ok=False``
    with the matter folder already replaced.

    The walk-blocked route is not a new defect — Codex review #1 closed it — and
    it is here on purpose. An invariant proven only over the paths a fix just
    touched is an invariant that stops being checked the moment someone adds a
    seventh route.
    """
    out = tmp_path / "matter"
    if route != "walk-blocked":
        assert _run(out).published, "nothing was published to protect"
    before = _deliverables(out)

    kwargs = UNPUBLISHABLE[route](monkeypatch, out)
    outcome = _run(out, **kwargs)

    assert not outcome.ok, f"{route} did not produce a not-ok run"
    assert not outcome.published, (
        f"{route}: a run that is not ok published its output set")
    assert outcome.incomplete_dir is not None, (
        f"{route}: nothing on disk records why this run published nothing")
    assert _deliverables(out) == before, (
        f"{route}: the matter folder changed under a run that published nothing")


def test_the_swap_is_unreachable_without_passing_the_gate(tmp_path):
    """A structural probe over ``pipeline.run``'s own source.

    The runtime invariant above proves the gate WORKS; this proves it is still
    in the road. They fail differently: deleting the ``if refusals`` block
    breaks both, but reordering the gate below ``mark_ready`` — which is exactly
    what the pre-fix code was — could leave a red set published while every
    forced-gate test above still passed by luck of what it asserted.

    It is deliberately a source check and not a coverage trick: there is no
    runtime observation that distinguishes "the swap ran after the gate" from
    "the swap ran, and the gate happened to be green".
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(pipeline.run))
    body = tree.body[0].body

    def index_of(predicate) -> int:
        for i, node in enumerate(body):
            if predicate(node):
                return i
        return -1

    def calls(node, name) -> bool:
        return any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == name for n in ast.walk(node))

    gate = index_of(lambda n: isinstance(n, ast.If) and any(
        isinstance(s, ast.Return) and isinstance(s.value, ast.Call)
        and getattr(s.value.func, "id", "") == "_refuse_publication"
        for s in ast.walk(n)))
    mark = index_of(lambda n: calls(n, "mark_ready"))
    commit = index_of(lambda n: calls(n, "commit_staging"))

    assert gate >= 0, (
        "pipeline.run has no top-level gate that returns _refuse_publication — "
        "Stage 6 is computing checks and publishing regardless (Codex B-1)")
    assert mark >= 0 and commit >= 0, "the swap left pipeline.run's top level"
    assert gate < mark < commit, (
        f"the swap is not downstream of the gate: gate at statement {gate}, "
        f"mark_ready at {mark}, commit_staging at {commit}")
