"""Publication is REFUSED, not merely reported on — Codex review #2, B-1.

**D-32 (2026-08-06): B-1 SURVIVES IN FULL; B-2 IS GONE WITH THE MARKER IT WAS
ABOUT.** The descope deleted the publication protocol — readiness marker, phase
machine, durable inventory, set-aside trees, recovery — and every B-2 test in
this file went with it, because there is nothing left to corrupt or to read
fail-closed. The ``unreadable-swap-marker`` route was removed from the
unpublishable-route enumeration rather than left to pass vacuously: no state on
disk can make a run unpublishable before it starts.

B-1's fix is untouched, and it is now the whole reason the staging directory
still exists — the gate audits the STAGED set, so a red gate costs a run and
never an evidence set. The B-2 section below is kept as the record of a finding
whose fix was withdrawn with its subject matter.

Two findings, one sentence between them: *state was computed and then not acted
on*.

**B-1.** §4 Stage 6 computed page accounting and built the manifest, and then
published unconditionally. A red set replaced
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
from dociq.operator import OperatorStamp
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
    """FAIL-BEFORE: with the unconditional ``publish_staging`` call restored,
    every one of these three publishes the red set over the good one and returns
    ``ok=False`` about a folder that has already been replaced.

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
    assert not (out / emit_paths.STATE_DIRNAME / emit_paths.STAGING_DIRNAME).exists(), (
        "the refused set was left staged, where a later hand could publish it")


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
# The CLASS, not the repro
# ---------------------------------------------------------------------------
#
# B-1 is one sentence: state was computed and then not acted on. The two probes
# below hold the class rather than the instance.
#
# B-2's marker probes are GONE (D-32) — with the readiness marker they tested.
# They are deleted rather than adapted because there is nothing left to adapt
# them to: publication is a direct call that writes no state to disk.


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
    # `unreadable-swap-marker` is GONE with the marker it corrupted (D-32).
    # There is no longer any folder state that can make a run unpublishable
    # before it starts — a route REMOVED, not a route missed.
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
    breaks both, but reordering the gate below ``publish_staging`` — which is
    exactly what the pre-fix code was — could leave a red set published while
    every forced-gate test above still passed by luck of what it asserted.

    It is deliberately a source check and not a coverage trick: there is no
    runtime observation that distinguishes "publication ran after the gate" from
    "publication ran, and the gate happened to be green".

    **This probe is why staging survived D-32.** The descope deleted the
    publication protocol and kept exactly one thing from it — the staged set the
    gate audits — so this ordering is the whole of B-1's fix now.
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
    publish = index_of(lambda n: calls(n, "publish_staging"))

    assert gate >= 0, (
        "pipeline.run has no top-level gate that returns _refuse_publication — "
        "Stage 6 is computing checks and publishing regardless (Codex B-1)")
    assert publish >= 0, "publication left pipeline.run's top level"
    assert gate < publish, (
        f"publication is not downstream of the gate: gate at statement {gate}, "
        f"publish_staging at {publish}")


# ---------------------------------------------------------------------------
# B-5 — the quarantined log is the record that outlives the process
# ---------------------------------------------------------------------------


def _master_index(tmp_path):
    """A tiny synthetic master index, so a run RECONCILES.

    D-04's regime, not the client's file: three rows with a perfect
    ``Original Sort`` sequence, which is all that is needed for
    ``reconciliation`` to be a real section rather than ``null``. Rows that
    match nothing in the fixture corpus reconcile as index-only, which is a
    populated report and therefore exactly what these assertions need.

    It exists because of B-7. The refusal tests below used to run **without** an
    index and still assert that ``content.reconciliation`` was populated — which
    passed only because ``_abort`` recorded the always-created no-index report
    where the published path recorded ``null``. The assertions were true of a
    projection that was itself the defect, so the fixture is what changes rather
    than the assertion.
    """
    headers = ["Original Sort", "Filename", "File Extension", "Filepath",
               "Size\n(KB)", "Date", "Source Received", "Date Received"]
    rows = [
        ["1", "Letter 001.pdf", "pdf", r"P 495\LETTERS", "1879", "5/22/2026",
         "Client Link", "2026-05-22"],
        ["2", "Letter 002.pdf", "pdf", r"P 495\LETTERS", "1185", "6/7/2026",
         "Client Link", "2026-06-08"],
        ["3", "Letter 003.pdf", "pdf", r"P 495\LETTERS", "1200", "6/8/2026",
         "Client Link", "2026-06-09"],
    ]
    lines = [",".join(f'"{h}"' for h in headers)]
    lines += [",".join(f'"{c}"' for c in row) for row in rows]
    path = tmp_path / "master_index.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path


def _refused_log(tmp_path, monkeypatch, force=_force_accounting_red, *,
                 with_index=True):
    """A real Stage-6 refusal, and the log it left ON DISK.

    Returns the outcome and the parsed ``incomplete_run/processing_log.json``,
    because every assertion in this section is a comparison between the two.
    That pairing is the finding: the fix round asserted only the outcome, and
    the outcome is the one artifact that does not exist once DocIQ has exited.

    ``with_index`` decides whether a master index is supplied, because after
    B-7 that is what decides whether ``content.reconciliation`` is a section or
    ``null`` — on the refusal path exactly as on the published one.
    """
    out = tmp_path / "matter"
    force(monkeypatch)
    extra = (
        {"master_index_path": str(_master_index(tmp_path))} if with_index else {}
    )
    refused = _run(out, **extra)
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
    # FULL equality, not four named sections. **This is the reach that missed
    # B-7** (Codex review #2, second fix round): the four-field version passed
    # while `content.reconciliation` and `content.output_hashes` disagreed
    # between the two logs, because neither was one of the four. The whole
    # section is compared, so a field added to `content` is covered the day it
    # is added rather than the day somebody remembers to list it.
    assert published["content"] == log_a["content"], (
        "a refused run and a published run over one corpus produced different "
        "hashed content: differing keys = "
        + ", ".join(sorted(
            k for k in set(published["content"]) | set(log_a["content"])
            if published["content"].get(k) != log_a["content"].get(k)))
    )
    assert published["content_sha256"] == log_a["content_sha256"]


def test_a_refused_log_gives_one_identity_for_its_own_hashed_content(
    tmp_path, monkeypatch
):
    """FAIL-BEFORE (Codex review #2, second fix round, B-7).

    ON DISK, over the ordinary NO-INDEX configuration Codex reproduced. Two
    facts, and the second is the one an auditor trips over:

    1. the refused log's ``content`` and ``content_sha256`` equal the published
       log's over the same corpus;
    2. the refused log's OWN ``content_sha256`` equals the
       ``run.output_manifest.log_content_sha256`` it carries.

    (2) is the whole finding. The manifest is built over the STAGED,
    published-style log before Stage 6 refuses; ``_abort`` then rebuilt the
    quarantined log with a different projection, so one durable audit file
    carried two different hashes for its own hashed section and a verifier had
    to choose which to believe.

    The no-index case is the trigger and is therefore the fixture: with no
    master index the published path recorded ``reconciliation: null`` and
    ``_abort`` recorded the always-created "no master index was supplied"
    report. ``output_hashes`` was the second divergence, found by enumerating
    ``content``'s keys rather than by being reported.
    """
    refused, log = _refused_log(tmp_path, monkeypatch, with_index=False)

    assert log["content"]["reconciliation"] is None, (
        "a run given no master index recorded a reconciliation section; the "
        "published path records null and the two must agree")

    embedded = log["run"]["output_manifest"]["log_content_sha256"]
    assert embedded, "the refused log carries no manifest hash to compare"
    assert embedded == log["content_sha256"], (
        "the refused log's embedded manifest hash and its own top-level content "
        "hash disagree: one audit file, two identities for one section")

    monkeypatch.undo()
    clean = _run(tmp_path / "clean")
    assert clean.published
    published = json.loads(
        (tmp_path / "clean" / "processing_log.json").read_text(encoding="utf-8"))
    assert published["content"] == log["content"], (
        "refused and published hashed content differ over the same no-index "
        "corpus: differing keys = "
        + ", ".join(sorted(
            k for k in set(published["content"]) | set(log["content"])
            if published["content"].get(k) != log["content"].get(k)))
    )
    assert published["content_sha256"] == log["content_sha256"]
    # And the published side agrees with ITS manifest too, so the property is
    # stated over both branches rather than only the one that broke. A published
    # run's manifest is a file beside the log rather than a section inside it —
    # the refusal path embeds it because the staging set it describes is
    # discarded and the file would exist nowhere else.
    on_disk_manifest = json.loads(
        (tmp_path / "clean" / mf.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert (on_disk_manifest["log_content_sha256"]
            == published["content_sha256"]), (
        "the published manifest and the published log disagree about the log's "
        "own hashed content")


def test_a_refused_log_with_an_index_also_agrees_with_its_manifest(
    tmp_path, monkeypatch
):
    """The WITH-index branch of the same property, enumerated with it.

    B-7's reproduction is the no-index configuration, but the property is not
    about indexes — it is that the quarantined log and the manifest it embeds
    hash the same bytes. A fix that only held where ``reconciliation`` is
    ``null`` would be the repro, not the class.
    """
    refused, log = _refused_log(tmp_path, monkeypatch, with_index=True)
    assert log["content"]["reconciliation"] is not None
    assert (log["run"]["output_manifest"]["log_content_sha256"]
            == log["content_sha256"])


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
    # A refused run never reaches publication, so it can leave no residue of
    # its own. Residue left by an EARLIER run IS recorded, in
    # `run.state_residue_before_run` -- but only on a run that got as far as
    # building a log, which a refused run does not do twice.
    "superseded_residue": (
        "a refused run never publishes, so it leaves no residue of its own; an "
        "earlier run's residue is recorded by the run that observes it in "
        "`run.state_residue_before_run`"
    ),
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
