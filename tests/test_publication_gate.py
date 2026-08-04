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
    # Patches the function the gate actually READS. It used to patch
    # `corpus_sort_check`, the boolean; the gate now reads
    # `corpus_sort_disagreements`, because a refusal that cannot name the
    # documents it refused over is unactionable at 9,000 of them (the class
    # D-30 came out of). A stub that returned a bare False would have gone on
    # passing against a gate that no longer consulted it.
    monkeypatch.setattr(
        "dociq.pipeline.corpus_sort_disagreements",
        lambda result: ("position 3: 'DIQ-000009' is here, canonical order "
                        "puts 'DIQ-000004'",))


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
    # REFUSED, not BLOCKED (A-15). A blocked run never established a corpus;
    # this one established a complete corpus and failed its own Stage-6 gate.
    assert refused.termination.status is TerminalStatus.REFUSED
    assert _deliverables(out) == before, (
        f"the {gate} gate went red and the matter folder changed anyway; the "
        f"last good deliverables must survive a refused run byte for byte")
    assert not emit_paths.pending_swap(OutputLayout.at(out)), (
        "a refused run wrote a readiness marker — the one thing that "
        "authorizes deleting the previous run's files")
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
        "a half-written marker exists, and its existence authorizes deletion")
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


# ---------------------------------------------------------------------------
# The defect the repetition found
# ---------------------------------------------------------------------------


def test_a_transient_lock_on_the_marker_is_retried_not_refused(tmp_path):
    """FOUND BY REPETITION, on run 6 of 30 of this very file.

    An ordinary run went red with ``PermissionError`` reading the marker DocIQ
    had written one statement earlier — a Windows on-access scanner holding a
    transient deny-write, not corruption. The B-2 fix had turned that into a
    blocked run.

    The defect is older than the fix. Under the permissive read B-2 replaced,
    that same ``PermissionError`` was swallowed into ``superseded = ()``, so an
    antivirus scan at the wrong moment produced B-2's mixed evidence set on a
    real matter folder with no crash involved at all. This is the finding
    reached by the ordinary path.
    """
    out = tmp_path / "matter"
    layout = OutputLayout.at(out).ensure()
    (out / "document_index.csv").write_text("Doc ID\n", encoding="utf-8",
                                            newline="")
    staging = out / emit_paths.STATE_DIRNAME / emit_paths.STAGING_DIRNAME
    (staging / "clean_text").mkdir(parents=True)
    (staging / "sources.json").write_text("{}\n", encoding="utf-8", newline="")
    emit_paths.mark_ready(layout, ("document_index.csv",))

    real_read = emit_paths.Path.read_text
    attempts = {"n": 0}

    def locked_twice(self, *a, **kw):
        if self.name == emit_paths.MARKER_NAME:
            attempts["n"] += 1
            if attempts["n"] <= 2:
                raise PermissionError(13, "Permission denied", str(self))
        return real_read(self, *a, **kw)

    mp = pytest.MonkeyPatch()
    mp.setattr(emit_paths.Path, "read_text", locked_twice)
    try:
        removed = emit_paths.commit_staging(layout)
    finally:
        mp.undo()

    assert attempts["n"] == 3, "the transient lock was not retried"
    assert removed == ("document_index.csv",)
    assert (out / "sources.json").is_file(), "the swap did not complete"


def test_a_lock_that_never_clears_still_fails_closed(tmp_path):
    """The retry is a retry, not a way out. When the file genuinely cannot be
    read, the refusal is the same one B-2 requires."""
    out = tmp_path / "matter"
    layout = OutputLayout.at(out).ensure()
    (out / "document_index.csv").write_text("Doc ID\n", encoding="utf-8",
                                            newline="")
    staging = out / emit_paths.STATE_DIRNAME / emit_paths.STAGING_DIRNAME
    staging.mkdir(parents=True)
    (staging / "sources.json").write_text("{}\n", encoding="utf-8", newline="")
    emit_paths.mark_ready(layout, ("document_index.csv",))
    before = _deliverables(out)

    real_read = emit_paths.Path.read_text

    def always_locked(self, *a, **kw):
        if self.name == emit_paths.MARKER_NAME:
            raise PermissionError(13, "Permission denied", str(self))
        return real_read(self, *a, **kw)

    mp = pytest.MonkeyPatch()
    mp.setattr(emit_paths.Path, "read_text", always_locked)
    try:
        with pytest.raises(emit_paths.PendingSwapUnreadable):
            emit_paths.commit_staging(layout)
    finally:
        mp.undo()

    assert _deliverables(out) == before
    assert (out / emit_paths.STATE_DIRNAME / emit_paths.MARKER_NAME).is_file()


def test_corrupt_json_is_not_retried(tmp_path):
    """Transient I/O and corrupt state are different things.

    Re-reading the same bytes cannot make invalid JSON valid, so a retry there
    would be a delay dressed up as a check — and a 2.5-second one on every
    hand-edited marker. Asserted by counting the reads, because the only visible
    symptom of getting this wrong is that the suite is slower.
    """
    out = tmp_path / "matter"
    layout = OutputLayout.at(out).ensure()
    marker = out / emit_paths.STATE_DIRNAME / emit_paths.MARKER_NAME
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text('{"superseded": ["clean', encoding="utf-8", newline="")

    real_read = emit_paths.Path.read_text
    reads = {"n": 0}

    def counted(self, *a, **kw):
        if self.name == emit_paths.MARKER_NAME:
            reads["n"] += 1
        return real_read(self, *a, **kw)

    mp = pytest.MonkeyPatch()
    mp.setattr(emit_paths.Path, "read_text", counted)
    try:
        with pytest.raises(emit_paths.PendingSwapUnreadable):
            emit_paths.commit_staging(layout)
    finally:
        mp.undo()

    assert reads["n"] == 1, (
        f"a corrupt marker was read {reads['n']} times — content corruption is "
        f"not transient and must not be retried")


# ---------------------------------------------------------------------------
# B-5 — the quarantined log is the record that outlives the process
# ---------------------------------------------------------------------------


def _refused_log(tmp_path, monkeypatch, force=_force_accounting_red):
    """A real Stage-6 refusal, and the log it left ON DISK.

    Returns the outcome and the parsed ``incomplete_run/processing_log.json``,
    because every assertion in this section is a comparison between the two.
    That pairing is the finding: the fix round asserted only the outcome, and
    the outcome is the one artifact that does not exist once DocIQ has exited.
    """
    out = tmp_path / "matter"
    force(monkeypatch)
    refused = _run(out)
    assert not refused.published
    assert refused.incomplete_dir is not None
    log = json.loads(
        (refused.incomplete_dir / "processing_log.json").read_text(encoding="utf-8"))
    return refused, log


def test_the_refusal_log_on_disk_carries_the_assignment_and_reconciliation(
    tmp_path, monkeypatch
):
    """FAIL-BEFORE (Codex review #2 fix round, B-5).

    ``_abort`` accepted the real assignment and reconciliation, returned them on
    the outcome, and called ``build_log()`` without them. So the durable record
    said no identifier was issued about a run that issued one per document.
    Codex measured 19 assignments in memory against ``[]`` on disk; these
    assertions compare the two directly rather than trusting either alone.
    """
    refused, log = _refused_log(tmp_path, monkeypatch)

    on_disk = log["content"]["doc_ids"]["assignments"]
    assert on_disk, (
        "the quarantined log serialized no assignments for a run that assigned "
        f"{len(refused.assignment.assignments)} identifiers")
    assert len(on_disk) == len(refused.assignment.assignments), (
        "the durable record and the in-memory outcome disagree on how many "
        "identifiers this run issued")
    assert [a["doc_id"] for a in on_disk] == [
        a.doc_id for a in refused.assignment.assignments]

    assert log["content"]["reconciliation"] is not None, (
        "the quarantined log serialized `reconciliation: null` for a run that "
        "reconciled the matter")
    assert (
        log["content"]["reconciliation"]["totals"]
        == refused.reconciliation.totals
    )


def test_the_refusal_log_on_disk_names_the_discrepancies_that_refused_it(
    tmp_path, monkeypatch
):
    """FAIL-BEFORE: ``build_log()`` had no input for them at all, so the gate
    discrepancies existed only in memory.

    The forced ``page-count`` discrepancy is the one that made Stage 6 refuse,
    and the ``<run>`` entries name which gate did it. Both have to be readable
    from the file, because "why did this refuse" is the question the folder is
    opened to answer.
    """
    refused, log = _refused_log(tmp_path, monkeypatch)

    gate = log["run"]["accounting_gate"]
    assert gate["ok"] is False
    on_disk = [(d["rel_path"], d["kind"], d["detail"]) for d in gate["discrepancies"]]
    in_memory = [
        (d.rel_path, d.kind, d.detail) for d in refused.accounting.discrepancies
    ]
    assert on_disk == in_memory, (
        "the discrepancies that refused publication are not the ones the "
        "durable record carries")
    assert any(k == "refused-accounting" for _, k, _ in on_disk), (
        "the file does not say which gate refused")
    assert any(k == "page-count" for _, k, _ in on_disk), (
        "the file does not carry the underlying discrepancy")


def test_the_refusal_log_on_disk_carries_the_manifest_of_the_discarded_set(
    tmp_path, monkeypatch
):
    """The same class, one field further out.

    A refused run DISCARDS its staging directory, so ``output_manifest.json``
    -- the record of what the refused set contained, including the unclassified
    outputs that may be the reason for the refusal -- exists nowhere else on
    disk. Enumerated as part of the B-5 class rather than reported by the
    reviewer.
    """
    refused, log = _refused_log(tmp_path, monkeypatch, _force_unclassified)

    man = log["run"]["output_manifest"]
    assert man["unclassified"] == sorted(refused.manifest.unclassified)
    assert man["unclassified"], "the fixture did not produce an unclassified output"
    assert man["corpus_sha256"] == refused.manifest.corpus_sha256


def test_the_refusal_log_keeps_criterion_7(tmp_path, monkeypatch):
    """The diagnosis went into ``run``, and nothing else moved.

    Two refused runs into two destinations, over the same corpus, with the same
    forced gate: the hashed ``content`` must be byte-identical, and the refusal
    vocabulary must appear nowhere inside it. This project has already put
    ``output_root`` and an elapsed-time string into hashed content and had to
    unpick both, and B-5's fix is precisely the shape of change that would do it
    a third time.
    """
    _force_accounting_red(monkeypatch)
    a, b = tmp_path / "a", tmp_path / "b"
    first, second = _run(a), _run(b)
    assert not first.published and not second.published

    log_a = json.loads(
        (first.incomplete_dir / "processing_log.json").read_text(encoding="utf-8"))
    log_b = json.loads(
        (second.incomplete_dir / "processing_log.json").read_text(encoding="utf-8"))

    assert log_a["content_sha256"] == log_b["content_sha256"], (
        "two refused runs over one corpus produced different hashed content")
    assert log_a["content"] == log_b["content"]

    blob = json.dumps(log_a["content"]).lower()
    for word in ("refused", "accounting_gate", "output_manifest", "stage_ms"):
        assert word not in blob, f"{word!r} reached the hashed content"
    # The destination, checked directly as well as by the equality above. Both
    # separators, because a JSON dump escapes the Windows one.
    for dest in (a, b):
        for form in (str(dest).lower(), str(dest).lower().replace("\\", "\\\\"),
                     dest.as_posix().lower()):
            assert form not in blob, "the output root reached the hashed content"

    # And a refused run's content must equal a PUBLISHED run's content over the
    # same corpus: the two differ in invocation, not in evidence. The gate that
    # refuses is injected after the log is built, which is what makes this a
    # real check rather than a tautology.
    monkeypatch.undo()
    clean = _run(tmp_path / "clean")
    assert clean.published
    published = json.loads(
        (tmp_path / "clean" / "processing_log.json").read_text(encoding="utf-8"))
    assert published["content"]["doc_ids"] == log_a["content"]["doc_ids"]
    assert published["content"]["documents"] == log_a["content"]["documents"]
    assert published["content"]["drops"] == log_a["content"]["drops"]
    assert published["content"]["bates"] == log_a["content"]["bates"]


# The class probe. Every fact the in-memory outcome carries has to have a
# declared home in the durable record, or a written reason why it has none.
#
# B-5 is not "assignments were missing"; it is "a value lived in memory and
# never reached the artifact that outlives the process", and that shape had
# four instances in one function. A test that checked the two fields the
# reviewer named would have left the other two. This one fails when a NEW field
# is added to `PipelineOutcome` without anybody deciding where it is recorded --
# which is the only way to stop the class coming back.
_DURABLE_HOME = {
    "result": lambda log: log["content"]["documents"],
    "layout": lambda log: log["run"]["output_root"],
    "accounting": lambda log: log["run"]["accounting_gate"],
    "manifest": lambda log: log["run"]["output_manifest"],
    "assignment": lambda log: log["content"]["doc_ids"]["assignments"],
    "reconciliation": lambda log: log["content"]["reconciliation"],
    "renumbering": lambda log: log["run"]["renumbering_warnings"],
    "bates_ranges": lambda log: log["content"]["bates"],
    "timings_s": lambda log: log["run"]["stage_ms"],
    "walk_notes": lambda log: log["run"]["invocation_notes"],
    "termination": lambda log: log["run"]["terminal_status"],
    "published": lambda log: log["run"]["published"],
    # The log IS the file, and `incomplete_dir` is the directory holding it --
    # a lookup inside the document would be circular.
    "log": lambda log: log["content_sha256"],
    "incomplete_dir": lambda log: log["run"]["deliverables_note"],
}
_NOT_DURABLE = {
    # A refused run removes nothing: the supersede list is computed for a
    # publishable termination only, and `_stale_deliverables` raises for any
    # other. There is no value here to record, rather than a value being
    # dropped.
    "stale_removed": "a refused run replaced nothing, so there is nothing to record",
}


def test_every_outcome_field_of_a_refused_run_has_a_durable_home(
    tmp_path, monkeypatch
):
    """FAIL-BEFORE: four of these lookups raise ``KeyError`` on the old code --
    ``accounting``, ``manifest``, ``assignment`` and ``reconciliation`` -- which
    is B-5 and the two siblings the finding did not name."""
    import dataclasses

    refused, log = _refused_log(tmp_path, monkeypatch)

    declared = set(_DURABLE_HOME) | set(_NOT_DURABLE)
    actual = {f.name for f in dataclasses.fields(pipeline.PipelineOutcome)}
    assert actual == declared, (
        "PipelineOutcome gained or lost a field without a decision about where "
        "the durable record keeps it: "
        f"undeclared={sorted(actual - declared)}, stale={sorted(declared - actual)}")

    for name, find in sorted(_DURABLE_HOME.items()):
        value = find(log)
        assert value is not None, (
            f"{name} is declared durable but the quarantined log records it as "
            "null -- the in-memory outcome is the only place it exists")


def test_a_corpus_order_refusal_names_the_documents_it_refused_over(
    tmp_path, monkeypatch
):
    """FAIL-BEFORE: `corpus_sort_check` returned a bare bool, so the refusal
    said only that the corpus was out of order.

    Enumerated with the D-30 class — a probe that reports a status and discards
    the evidence behind it. This one gates PUBLICATION, so the discarded
    evidence is what an operator holding a refused matter folder needs in order
    to do anything at all: which two documents, at which position.

    Real disorder, not a stub: the run's own documents are reversed inside the
    check, so what reaches the log is what
    `corpus_sort_disagreements` actually produces.
    """
    real = pipeline.corpus_sort_disagreements
    monkeypatch.setattr(
        "dociq.pipeline.corpus_sort_disagreements",
        lambda result: real(_reversed(result)))

    out = tmp_path / "matter"
    refused = _run(out)
    assert not refused.published
    assert refused.incomplete_dir is not None

    log = json.loads(
        (refused.incomplete_dir / "processing_log.json").read_text(encoding="utf-8"))
    detail = "\n".join(
        d["detail"] for d in log["run"]["accounting_gate"]["discrepancies"]
        if d["kind"] == "refused-corpus-order")
    assert detail, "the corpus-order gate did not reach the durable record"
    assert "position " in detail, (
        "the refusal does not name a position — 'the corpus is not in canonical "
        "order' over 9,000 documents is unactionable")
    ids = [d.doc_id for d in refused.result.documents if d.doc_id]
    assert any(doc_id in detail for doc_id in ids), (
        "the refusal names no document; the operator cannot find the disorder")


def _reversed(result):
    """The same result with its documents reversed — genuine disorder."""
    import dataclasses

    return dataclasses.replace(result, documents=tuple(reversed(result.documents)))


def test_the_order_evidence_is_capped_out_loud_not_silently(tmp_path):
    """A fully reversed corpus must not produce one discrepancy per document,
    and the cap must SAY it is a cap.

    A silent truncation is the same defect as a discarded stack: the reader
    cannot tell "these are the problems" from "these are the first ten
    problems". No silent caps.
    """
    out = tmp_path / "matter"
    good = _run(out)
    disordered = _reversed(good.result)
    entries = pipeline.corpus_sort_disagreements(disordered)

    # The number of positions that genuinely disagree, computed here rather
    # than taken from the function under test.
    from dociq.contracts import document_sort_key

    canonical = sorted(disordered.documents, key=document_sort_key)
    disagreeing = sum(1 for a, b in zip(disordered.documents, canonical) if a is not b)
    assert disagreeing > 10, (
        "the fixture corpus is too small to exercise the cap — this test would "
        "pass without ever reaching it")

    assert entries, "reversing the corpus produced no disagreement"
    assert len(entries) <= 11, "the cap did not hold"
    assert "further position(s)" in entries[-1] and f"{disagreeing} in all" in entries[-1], (
        f"{disagreeing} positions disagree and the list stops at {len(entries)} without "
        f"saying so — a silent cap is the same defect as a discarded stack: "
        f"the reader cannot tell 'these are the problems' from 'these are the "
        f"first ten problems'")
    assert pipeline.corpus_sort_check(good.result) is True
    assert pipeline.corpus_sort_check(disordered) is False, (
        "the boolean and the evidence disagree about whether the corpus is "
        "ordered")
