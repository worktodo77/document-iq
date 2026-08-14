"""Stage 5 builds in staging and publishes — and what publishing does NOT promise.

``docs/reviews/sprint-1_merge_readiness.md``, NOT PROVEN item 8: *"A crash during
emit, after the purge, can still leave a partly-replaced output folder. Distinct
from the aborted-walk case that B-1 closed; it needs write-to-staging-then-swap."*

The failure it names is specific and worth restating, because the tests below are
shaped by it rather than by the word "atomic". A re-run used to delete the
previous run's deliverables and then write the new ones into the same folder. A
crash in between — and emit is where a full-corpus run spends real time — left
``clean_text/`` holding some of run 1's documents and some of run 2's, under Doc
IDs that need not agree, with an ``output_manifest.json`` from run 1 describing a
set that no longer exists. Nothing on disk said so.

**D-32 (2026-08-06) — MOST OF WHAT THIS FILE USED TO PROVE NO LONGER EXISTS, and
the properties that replaced it are weaker. Read this before trusting the file.**

Six consecutive review generations each found a new defect inside the previous
generation's fix, all in the publication protocol: a readiness marker, a
``pending → aside → publishing → published`` phase machine, a durable inventory
of what the last run published, set-aside trees under ``.dociq/``, and
roll-forward / roll-back recovery. The diagnosis was that the design could not
represent its own remaining failure modes. Alex ruled the whole protocol
descoped. It is **gone**, and roughly two-thirds of this file went with it.

What is proven here now:

1. **Crash-during-emit leaves the previous run complete.** Not "mostly complete",
   not "the manifest is stale": byte-for-byte the folder run 1 left. Everything
   is built in ``.dociq/staging/``, so nothing before publication touches the
   matter folder. UNCHANGED by the descope, and it is the property that made
   staging worth keeping.
2. **Publication is remove-then-move, and its failures are loud.** An empty
   staging directory is refused; a previous deliverable that cannot be removed
   stops publication before any staged file passes it; a move that fails leaves
   the rest of the staged set on disk. Each raises
   :class:`~dociq.emit.paths.PublicationFailed`, whose message says the folder is
   mixed.
3. **The staging path is in no hashed artifact** (criterion 7). Two runs whose
   staging directories are at different absolute paths produce one corpus hash,
   one log ``content`` hash, and byte-identical adjacent files. This is the check
   that would have caught ``output_root`` inside hashed log content in Sprint 1.
4. **THE WINDOW, asserted rather than described.** A crash inside publication
   leaves the matter folder holding part of two runs' evidence, permanently; no
   later run detects it, repairs it, or warns that it happened. Section 4 proves
   that this is what happens. It is a test of a known hole, written so that the
   hole cannot be quietly closed-in-documentation without a test going red, and
   so that nobody reading this file believes a guarantee the code does not make.

**What is NO LONGER proven, because it is no longer true.** An interrupted
publication is not rolled forward. There is no marker, so there is no
fail-closed read of one. Nothing under the matter root is set aside before it is
deleted — publication deletes. A deliverable an older build wrote under a name
this build does not write is left in the folder for good (Codex review #2's B-8,
knowingly reopened; section 4 has the probe). Every test that asserted one of
those properties was DELETED rather than adapted, because there is nothing left
to adapt it to.

**What this file still does NOT prove, and where that lives instead.** That a set
which fails §4 Stage 6 is never published at all (B-1) is in
``tests/test_publication_gate.py``. That gate is the reason staging survived
D-32: the audit runs over the staged set, so a red gate costs a run and never an
evidence set.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from dociq import pipeline
from dociq.contracts import RunConfig
from dociq.emit import paths as emit_paths
from dociq.emit.paths import OutputLayout
from dociq.ingest import extract as ex
from dociq.ingest import walker
from dociq.profiles.model import OperatorStamp
from dociq.runstate import COMPLETED
from dociq.verify import manifest as mf

from .conftest import FIXTURES

NL = chr(10)

STAMP = OperatorStamp("test", "2026-07-30T00:00:00Z", "test-host")


def _run(out, **kw):
    cfg = RunConfig(
        source_root=str(FIXTURES),
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


def _fingerprint(root) -> dict[str, str]:
    """Every file in the matter folder, hashed. DocIQ's own run state is
    excluded — it is scratch, not a deliverable, and the manifest excludes it for
    the same reason."""
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith(f"{emit_paths.STATE_DIRNAME}/"):
            continue
        out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _staging_of(out) -> Path:
    return out / emit_paths.STATE_DIRNAME / emit_paths.STAGING_DIRNAME


def _stage_a_second_set(tmp_path, out, name="second"):
    """Put a COMPLETE, REAL set of deliverables into ``out``'s staging tree.

    Produced by an actual run into a scratch destination and copied in, so the
    set publication is asked to move is the set the pipeline builds — not a
    hand-written stand-in whose shape could drift from the emitters'.

    Returns ``(layout, staging, plan)``, where ``plan`` is what
    :func:`dociq.pipeline._stale_deliverables` says this run supersedes — the
    same value the pipeline passes to publication.
    """
    second_out = tmp_path / name
    if not second_out.exists():
        _run(second_out)
    layout = OutputLayout.at(out)
    staging = _staging_of(out)
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(second_out, staging,
                    ignore=shutil.ignore_patterns(emit_paths.STATE_DIRNAME))
    return layout, staging, pipeline._stale_deliverables(layout, COMPLETED)


class Boom(RuntimeError):
    """A crash in the middle of emit, at a point chosen to be maximally
    destructive under the old write path: after the purge, after some of
    ``clean_text/`` is written, before the index and the log."""


# ---------------------------------------------------------------------------
# 1. The crash during emit — UNCHANGED by D-32, and the reason staging stayed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "victim",
    [
        "dociq.pipeline.write_sources_json",
        "dociq.pipeline.write_index_csv",
        "dociq.pipeline.write_processing_log",
        "dociq.pipeline.build_upload_package",
    ],
)
def test_a_crash_during_emit_leaves_the_previous_run_untouched(
    tmp_path, monkeypatch, victim
):
    """FAIL-BEFORE: with Stage 5 writing straight into the matter folder (the
    Sprint-1 shape), every one of these four crash points leaves the folder
    holding a purged, half-written mixture — watched red before the fix by
    reverting Stage 5 to the destination layout.

    The four points are an ENUMERATION of the class, not one repro: one per
    emitter that writes a deliverable after the first bytes of ``clean_text/``
    land, so a future emitter added between any two of them is covered by its
    neighbours.
    """
    out = tmp_path / "matter"
    first = _run(out)
    assert first.published
    before = _fingerprint(out)
    assert before, "the first run wrote nothing to compare against"

    module, name = victim.rsplit(".", 1)
    monkeypatch.setattr(
        f"{module}.{name}",
        lambda *a, **k: (_ for _ in ()).throw(Boom(name)),
    )
    with pytest.raises(Boom):
        _run(out)

    assert _fingerprint(out) == before, (
        f"a crash in {name} changed the matter folder; the previous run's "
        "deliverables must survive a failed re-run byte for byte"
    )


def test_the_next_run_discards_an_unfinished_staging_directory(tmp_path, monkeypatch):
    """The half-written set from the crash above is not a head start."""
    out = tmp_path / "matter"
    _run(out)
    monkeypatch.setattr(
        "dociq.pipeline.write_index_csv",
        lambda *a, **k: (_ for _ in ()).throw(Boom("index")),
    )
    with pytest.raises(Boom):
        _run(out)
    staging = _staging_of(out)
    assert staging.is_dir(), "the crashed run left no staging to discard"

    monkeypatch.undo()
    third = _run(out)
    assert third.published
    assert not staging.exists(), "staging survived a completed run"
    assert third.ok


# ---------------------------------------------------------------------------
# 2. Publication — remove, then move, and every failure is loud
# ---------------------------------------------------------------------------


def test_publication_replaces_the_whole_previous_set(tmp_path):
    """The ordinary case, and the one that has to keep working: a re-run's
    folder holds the second run's set and nothing of the first's."""
    out = tmp_path / "matter"
    _run(out)
    stray = out / "clean_text" / "LI-99999-from-run-one.txt"
    stray.write_text("run one only\n", encoding="utf-8", newline="")

    second = _run(out)

    assert second.published and second.ok
    assert not stray.exists(), (
        "a clean-text file only run 1 wrote survived run 2's publication")
    assert (out / "sources.json").is_file()
    assert not _staging_of(out).exists(), "publication left its staging tree"
    assert second.superseded_residue == (), (
        f"an ordinary re-run reported residue: {second.superseded_residue}")


def test_the_upload_package_no_longer_survives_a_shrinking_re_run(tmp_path):
    """FAIL-BEFORE: with ``upload_package`` absent from ``_STALE_PATTERNS`` — as
    it was, on the reasoning that the emitter rebuilt it in place — publication
    leaves the previous run's copies in the folder an operator uploads whole.

    The reasoning was true of the old write path and is withdrawn with it: the
    rebuild happens in staging and never touches the destination.
    """
    out = tmp_path / "matter"
    _run(out)
    stray = out / "upload_package" / "LI-99999-not-from-this-run.txt"
    stray.write_text("evidence from another run\n", encoding="utf-8", newline="")

    _run(out)
    assert not stray.exists(), (
        "a previous run's upload_package file survived a re-run"
    )


def test_publication_refuses_an_empty_staging_directory(tmp_path):
    """FAIL-BEFORE: without the guard, this empties the matter folder and puts
    nothing in its place — the one shape of accident that turns a plan into a
    deletion tool.

    It is not reachable from ``pipeline.run`` (Stage 6 refuses a set with no
    manifest long before this), and that is exactly why it is asserted at this
    level: what protects the folder here is four lines in
    :func:`~dociq.emit.paths.publish_staging`, not the distance from the caller.
    """
    out = tmp_path / "matter"
    _run(out)
    before = _fingerprint(out)
    layout = OutputLayout.at(out)
    plan = pipeline._stale_deliverables(layout, COMPLETED)
    assert plan, "the fixture staged no plan, so the guard is untested"
    _staging_of(out).mkdir(parents=True, exist_ok=True)

    with pytest.raises(emit_paths.PublicationFailed) as exc:
        emit_paths.publish_staging(layout, plan)

    assert "Nothing was removed" in str(exc.value)
    assert _fingerprint(out) == before, (
        "publication with an empty staging directory changed the folder")


def test_a_locked_previous_deliverable_stops_publication_before_any_move(
        tmp_path):
    """A REAL open handle, not an injected exception.

    Windows refuses to unlink a file another handle has open without
    ``FILE_SHARE_DELETE``, which is precisely the antivirus / backup / "the
    analyst has it open in Excel" case this project keeps meeting. The removal
    is retried (``_retry_io``, 2.54 s) and then fails, and what matters is the
    ORDER: the failure happens during the removal pass, so no staged file has
    been moved and the complete new set is still on disk.

    FAIL-BEFORE: with ``_remove_or_fail`` reverted to ``rmtree(...,
    ignore_errors=True)`` / a suppressed ``unlink``, publication reports success
    over a file that is still there.
    """
    out = tmp_path / "matter"
    _run(out)
    layout, staging, plan = _stage_a_second_set(tmp_path, out)
    victim = out / "document_index.csv"
    assert victim.relative_to(out).as_posix() in plan

    staged_names = {p.relative_to(staging).as_posix()
                    for p in staging.rglob("*") if p.is_file()}
    with victim.open("rb"):
        with pytest.raises(emit_paths.PublicationFailed) as exc:
            emit_paths.publish_staging(layout, plan)

    assert "THIS FOLDER IS NOW MIXED" in str(exc.value)
    assert str(staging) in str(exc.value), (
        "the message does not tell the operator where the complete new set is")
    assert victim.is_file(), "the locked file was reported removed"
    still_staged = {p.relative_to(staging).as_posix()
                    for p in staging.rglob("*") if p.is_file()}
    assert still_staged == staged_names, (
        "publication moved staged files even though the removal pass failed; "
        "the removal pass runs to completion first, on purpose")

    # B-9 (Codex, 2026-08-14), pinned here because the fix was made in code and
    # left unasserted. This message used to end "...or move the staged files
    # into this folder by hand." Removal stops at the FIRST failure, so the
    # previous run's LATER deliverables are still present, and an operator who
    # followed that advice got `a.txt = NEW` beside `z.txt = OLD` with the stale
    # file absent from the new set — the mixture the message is warning about.
    #
    # Asserted as a prohibition AND as the absence of the old advice, because
    # the two can drift apart: someone could add the warning and leave the
    # original sentence below it.
    message = str(exc.value)
    assert "Do NOT move the staged files into this folder by hand" in message, (
        "the removal-failure message does not prohibit the manual move that "
        "produces a mixed folder (B-9)")
    assert "or move the staged files into this folder by hand" not in message, (
        "the removal-failure message still offers the manual move as a remedy; "
        "following it produces exactly the mixture the message warns about")
    assert "RE-RUN THE MATTER" in message, (
        "the message prohibits the unsafe route without naming the safe one")


def test_a_failed_move_leaves_the_unmoved_staged_files_on_disk(tmp_path):
    """The second half of the same promise: what publication has not moved, it
    has not lost.

    A REAL open handle again — this time on a SOURCE file in staging, which
    Windows refuses to rename. There is no recovery from this state and the test
    does not pretend otherwise; what it proves is that the material for a manual
    recovery is all still there and the message says where.
    """
    out = tmp_path / "matter"
    _run(out)
    layout, staging, plan = _stage_a_second_set(tmp_path, out)
    # A file in the MIDDLE of the deterministic order, so the failure lands
    # after several successful moves and BEFORE several others — the mixed
    # state, with unmoved siblings still to protect. Holding the last file
    # instead would leave nothing behind it and the assertion below would pass
    # against a publication that threw the remainder away.
    staged = sorted(p for p in staging.rglob("*") if p.is_file())
    held = staged[len(staged) // 2]
    behind = staged[len(staged) // 2 + 1:]
    assert behind, "the fixture staged too few files to have a remainder"

    with held.open("rb"):
        with pytest.raises(emit_paths.PublicationFailed) as exc:
            emit_paths.publish_staging(layout, plan)

    assert "THIS FOLDER IS NOW MIXED" in str(exc.value)
    assert held.is_file(), "the staged file that could not be moved was lost"
    assert staging.is_dir(), (
        "publication discarded staging on the failure path — the only copy of "
        "the unpublished files")
    assert all(p.is_file() for p in behind), (
        "the staged files BEHIND the failure were discarded; they are the only "
        "copy of deliverables the matter folder no longer holds")


def test_dociq_state_inside_staging_is_never_published(tmp_path):
    """A staging layout is an ``OutputLayout``, so anything run against it makes
    its own ``.dociq/`` inside it — the package builder does. Publishing that
    into the matter root would put DocIQ's scratch where the manifest expects
    deliverables. Held by construction rather than by the one consumer that
    happens to clean up after itself."""
    out = tmp_path / "matter"
    _run(out)
    layout, staging, plan = _stage_a_second_set(tmp_path, out)
    ghost = staging / emit_paths.STATE_DIRNAME / "scratch.txt"
    ghost.parent.mkdir(parents=True, exist_ok=True)
    ghost.write_text("DocIQ's own state", encoding="utf-8", newline="")

    emit_paths.publish_staging(layout, plan)
    assert not (out / emit_paths.STATE_DIRNAME / "scratch.txt").exists()
    assert not (out / "scratch.txt").exists()


def test_the_plan_names_the_files_and_never_the_directory(tmp_path):
    """FAIL-BEFORE (fourth fix round, F-3, and the reason it stays asserted).

    ``_stale_deliverables`` runs at the TOP of Stage 5 and the removals it plans
    happen after the whole of Stage 5 and Stage 6 — minutes on a real matter. A
    plan that named ``clean_text`` as a DIRECTORY would remove whatever was
    inside it at removal time, including a file that reached it after the plan
    was taken. The plan names files.

    The window is reproduced exactly: the plan is taken, an analyst's note is
    written into every planned tree, publication runs.

    **Widened after Codex finding A-8.** This test asserted the property for
    ``clean_text`` alone, and ``upload_package`` was still planned as a bare
    directory — so F-3 shipped fixed for one deliverable tree and unfixed for
    its sibling, which D-32's own register entry had named. A test that asserts
    a class by naming one member of it is how the same defect returns under a
    new number. It now asserts over EVERY directory the plan touches, so a
    deliverable tree added later is covered the moment it exists.
    """
    out = tmp_path / "matter"
    _run(out)
    layout = OutputLayout.at(out)

    # A NESTED directory inside a planned tree, present BEFORE the plan is
    # taken. Codex's second A-8 round: the first widening passed only because
    # every planned tree in the fixture was flat, so `upload_package/*` never
    # matched a directory and the `is_dir()` branch of the planner was never
    # reached. A test whose fixture cannot contain the defect proves nothing,
    # and that is the same failure as asserting a class by naming one member —
    # one level further down.
    nested = out / "upload_package" / "analyst_notes"
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "seen_at_plan_time.md").write_text(
        "# replaced, legitimately\n", encoding="utf-8", newline="")

    plan = pipeline._stale_deliverables(layout, COMPLETED)

    # The class: no plan entry may name a directory. Derived from the plan and
    # the disk, not from a list of tree names someone has to remember to extend.
    bare_dirs = sorted(rel for rel in plan if (out / rel).is_dir())
    assert not bare_dirs, (
        f"the plan names {bare_dirs} as DIRECTORIES, so everything inside them "
        f"is decided by a disk read that is minutes stale by the time the "
        f"removal happens — an analyst's file saved meanwhile is destroyed and "
        f"stale_outputs_replaced never names it")

    trees = sorted({rel.split("/")[0] for rel in plan if "/" in rel})
    assert {"clean_text", "upload_package"} <= set(trees), (
        f"the fixture's premise is gone: the plan reaches into {trees}, and "
        f"this test is only meaningful over the trees it actually plans")

    # Written AFTER the plan, into the nested directory that already existed.
    kept = nested / "queries.md"
    kept.write_text("# mine, not DocIQ's\n", encoding="utf-8", newline="")

    # An analyst's file in EVERY planned tree, written after the plan was taken.
    notes = []
    for tree in trees:
        note = out / tree / "analyst_note.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text(f"# not DocIQ's, in {tree}\n", encoding="utf-8",
                        newline="")
        notes.append((tree, note))

    _, _, _ = _stage_a_second_set(tmp_path, out)
    emit_paths.publish_staging(layout, plan)

    for tree, note in notes:
        assert note.is_file(), (
            f"a file the plan never named was removed from {tree}/, because "
            f"the plan decided a whole directory from a stale disk read")
        assert note.read_text(encoding="utf-8") == f"# not DocIQ's, in {tree}\n"

    # The nested case, and the distinction that makes it meaningful. A file
    # present INSIDE a DocIQ-owned tree when the plan is taken is legitimately
    # replaced — `clean_text/*` has always done that. What must survive is a
    # file that arrives AFTER the plan, which is the whole TOCTOU property, and
    # it can only survive if the plan named files rather than the directory.
    assert kept.is_file(), (
        "a file written into a NESTED directory AFTER the plan was taken was "
        "removed: the plan named the directory, so publication deleted "
        "whatever it held at removal time")
    assert kept.read_text(encoding="utf-8") == "# mine, not DocIQ's\n"
    assert (out / "sources.json").is_file(), "publication did not publish"


def test_the_replaced_record_names_the_files_not_the_directory(tmp_path):
    """The disclosure half of the property above.

    A coarsened plan silently coarsens ``stale_outputs_replaced`` from the files
    to ``["clean_text"]``, so the durable record of what a re-run replaced stops
    naming any of it. Asserted separately from the data half because a reader
    losing the names is a defect on its own.
    """
    out = tmp_path / "matter"
    _run(out)
    second = _run(out)
    assert any(rel.startswith("clean_text/") for rel in second.stale_removed), (
        f"the record names no clean-text file: {second.stale_removed}")
    assert "clean_text" not in second.stale_removed


# ---------------------------------------------------------------------------
# 3. Criterion 7 — the staging path is in nothing that is hashed
# ---------------------------------------------------------------------------


def test_two_destinations_produce_one_identity_and_identical_bytes(tmp_path):
    """The check that catches a staged path leaking into a hashed artifact.

    Two runs, two destinations, therefore two different staging paths. If the
    staging directory reached ``sources.json``, ``_hashes_of``'s relative keys,
    the log's hashed content or the upload package, exactly one of these
    assertions fails — and which one names where it leaked.
    """
    a = _run(tmp_path / "a")
    b = _run(tmp_path / "b")

    assert a.manifest.corpus_sha256 == b.manifest.corpus_sha256
    assert a.manifest.run_identity_sha256 == b.manifest.run_identity_sha256
    assert a.manifest.log_content_sha256 == b.manifest.log_content_sha256
    assert not a.manifest.unclassified and not b.manifest.unclassified

    assert a.manifest.deterministic == b.manifest.deterministic
    assert a.manifest.adjacent == b.manifest.adjacent, (
        "an adjacent deliverable differs between destinations"
    )
    assert mf.compare(a.manifest, b.manifest) == []

    for name in ("clean_text", "sources.json", "document_index.csv",
                 "upload_package", "doc_ids_issued.json"):
        left, right = tmp_path / "a" / name, tmp_path / "b" / name
        if left.is_file():
            assert left.read_bytes() == right.read_bytes(), name


def test_no_hashed_artifact_mentions_the_staging_directory(tmp_path):
    """Belt and braces, and it fails differently from the test above: a leak
    into a NON-compared artifact (the summary PDF, the workbook) is invisible to
    a two-destination hash comparison of the deterministic set."""
    out = tmp_path / "matter"
    _run(out)
    needles = (emit_paths.STAGING_DIRNAME.encode(),
               emit_paths.STATE_DIRNAME.encode())
    for path in sorted(out.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(out).as_posix()
        if rel.startswith(f"{emit_paths.STATE_DIRNAME}/"):
            continue
        blob = path.read_bytes()
        for needle in needles:
            assert needle not in blob, f"{rel} names DocIQ's own run state"


def test_the_state_directory_name_matches_the_walkers(tmp_path):
    """A copied constant is a constant that can drift. :mod:`dociq.emit` may not
    import :mod:`dociq.ingest`, so the two literals are checked here instead."""
    assert emit_paths.STATE_DIRNAME == walker.STATE_DIR


def test_publication_reaches_no_hashed_artifact(tmp_path):
    """Criterion 7 against the descope specifically.

    The removed protocol wrote its state under ``.dociq/`` and the manifest
    excluded the whole prefix, so nothing publication did could reach a hash.
    The new design writes no state at all, which is a stronger version of the
    same property — but "stronger" is an argument, and this is the measurement:
    a run into a folder that has a previous set and a run into an empty folder
    produce the same hashed content, so *what publication had to do* is not part
    of the run's identity.
    """
    first = _run(tmp_path / "a")
    rerun = _run(tmp_path / "a")
    fresh = _run(tmp_path / "b")

    assert rerun.stale_removed, "the re-run replaced nothing, so this proves nothing"
    assert fresh.stale_removed == ()
    assert rerun.manifest.corpus_sha256 == fresh.manifest.corpus_sha256
    assert rerun.manifest.log_content_sha256 == fresh.manifest.log_content_sha256
    assert rerun.manifest.run_identity_sha256 == first.manifest.run_identity_sha256


# ---------------------------------------------------------------------------
# 4. THE WINDOW D-32 ACCEPTED — asserted, not described
#
# Every test in this section proves that something BAD happens and is NOT
# handled. That is deliberate. The project's recurring failure is a document or
# a test claiming a property the code does not have, and the descope's whole
# argument is that one clearly-stated hole beats several hidden ones. A hole
# nobody can find in the test suite is a hidden one.
#
# If a future change closes any of these, the test goes red and the reviewer is
# forced to update the claim in `emit/paths.py`'s module docstring and in
# `docs/verification/d32_descope_2026-08-06.md` at the same time. That coupling
# is the point.
# ---------------------------------------------------------------------------


def _crash_mid_publication(monkeypatch, after: int = 2):
    """Kill publication part way through the MOVE pass, like a process death.

    ``Boom`` rather than ``OSError`` on purpose: an ``OSError`` is retried and
    then converted into :class:`PublicationFailed`, which is the *handled*
    failure section 2 covers. This is the unhandled one — the power cut, the
    kill, the crash in code publication does not own — and it is the state D-32
    accepted.
    """
    real = os.replace
    seen = {"n": 0}

    def die(src, dst):
        seen["n"] += 1
        if seen["n"] > after:
            raise Boom("the process died mid-publication")
        return real(src, dst)

    monkeypatch.setattr(emit_paths.os, "replace", die)
    return seen


def test_a_crash_inside_publication_leaves_a_MIXED_matter_folder(
        tmp_path, monkeypatch):
    """**This is D-32's accepted window, and it is asserted so it cannot be
    quietly denied.**

    The previous run's deliverables have been removed and only some of the new
    run's have arrived. The folder holds part of two runs' evidence. Nothing on
    disk records that a publication was in progress.

    The old protocol made this state recoverable and took six review generations
    of defects doing it. Alex ruled the trade explicitly. This test does not
    argue with the ruling; it makes the cost visible in the suite.
    """
    out = tmp_path / "matter"
    _run(out)
    before = _fingerprint(out)
    layout, staging, plan = _stage_a_second_set(tmp_path, out)
    _crash_mid_publication(monkeypatch, after=2)

    with pytest.raises(Boom):
        emit_paths.publish_staging(layout, plan)
    monkeypatch.undo()

    after = _fingerprint(out)
    assert after != before, "the fixture did not reach the move pass"
    missing = set(before) - set(after)
    assert missing, (
        "nothing of the previous set is gone — the removal pass did not run, "
        "so this is not the window under test")
    assert set(after), "nothing of the new set arrived either"

    # THE POINT: no file on disk says a publication was interrupted. The state
    # directory holds the staging tree and nothing else — no marker, no phase,
    # no inventory.
    state = out / emit_paths.STATE_DIRNAME
    assert sorted(p.name for p in state.iterdir()) == [
        emit_paths.STAGING_DIRNAME], (
        "publication left state on disk; D-32 removed the protocol that read "
        "it, so anything here is state nothing consumes")


def test_the_next_run_does_not_detect_or_repair_the_mixture(
        tmp_path, monkeypatch):
    """The second half, and the one most likely to be misremembered as safe.

    A later run does end the mixture — but only because it replaces everything,
    and only if it succeeds. It does not notice it happened, does not say so,
    and would not have said so had it also failed. The only trace is the
    leftover staging tree, disclosed as ``run.state_residue_before_run``.
    """
    out = tmp_path / "matter"
    _run(out)
    layout, staging, plan = _stage_a_second_set(tmp_path, out)
    _crash_mid_publication(monkeypatch, after=2)
    with pytest.raises(Boom):
        emit_paths.publish_staging(layout, plan)
    monkeypatch.undo()
    assert staging.is_dir(), "the fixture left no staging tree to be found"

    third = _run(out)
    assert third.published and third.ok

    payload = json.loads(
        (out / "processing_log.json").read_text(encoding="utf-8"))
    disclosed = payload["run"]["state_residue_before_run"]
    assert disclosed == [f"{emit_paths.STATE_DIRNAME}/"
                         f"{emit_paths.STAGING_DIRNAME}"], disclosed

    # The run block only: "recovered via pdf extractor" is an extraction note in
    # the hashed content and has nothing to do with publication.
    text = json.dumps(payload["run"]).lower()
    for word in ("recovered", "rolled forward", "roll_forward", "interrupted"):
        assert word not in text, (
            f"the log claims {word!r}; nothing detects or repairs an "
            f"interrupted publication and the record must not imply otherwise")
    assert "state_residue_before_run" not in json.dumps(payload["content"]), (
        "a fact about this folder's history reached the hashed content")


def test_a_retired_output_from_an_older_build_is_LEFT_in_the_folder(tmp_path):
    """**Codex review #2's finding B-8, knowingly reopened by D-32.**

    A former build emitted ``legacy_report.json``; this build retired the name,
    so no pattern matches it and no staged file lands on it. The durable
    inventory of what the last run actually published is what used to catch
    this, and it was part of the removed protocol.

    §4 Stage 6 cannot catch it either: the manifest is built over STAGING, not
    over the destination root, so the gate has never seen the file.

    Asserted as a LIMITATION so it stays disclosed. If a future change closes
    it, this test goes red and whoever closed it must withdraw the statement in
    ``emit/paths.py`` and in ``pipeline._STALE_PATTERNS``'s docstring saying it
    is open.
    """
    out = tmp_path / "matter"
    _run(out)
    retired = out / "legacy_report.json"
    retired.write_text('{"from": "a build that emitted this"}\n',
                       encoding="utf-8", newline="")
    assert "legacy_report.json" not in json.dumps(list(pipeline._STALE_PATTERNS))

    outcome = _run(out)

    assert outcome.published and outcome.ok
    assert retired.is_file(), (
        "the retired output was removed — B-8 is closed again, and the "
        "documents that say it is open are now wrong")
    assert "legacy_report.json" not in outcome.stale_removed
    log = json.loads((out / "processing_log.json").read_text(encoding="utf-8"))
    assert "B-8" in log["run"]["stale_outputs_plan_source"], (
        "the run does not disclose that its plan cannot see a retired name")


def test_a_drained_staging_that_cannot_be_removed_is_a_success_with_a_residue(
        tmp_path, monkeypatch):
    """Amendment A-16, after the descope, on the one residue that survives it.

    Publication moves every staged file out and then removes the drained tree.
    A failure of that last step cannot make the matter folder wrong — everything
    of value has already left — so it is a residue, reported, not an error. What
    remains under ``.dociq/`` is empty directories.

    FAIL-BEFORE: with ``state_residue`` not consulted after the move pass,
    ``PublishResult.residue`` is empty and the operator is told nothing.
    """
    out = tmp_path / "matter"
    _run(out)
    layout, staging, plan = _stage_a_second_set(tmp_path, out)
    real_rmtree = shutil.rmtree

    def refuse_staging(path, *a, **kw):
        if Path(path) == staging:
            return  # `ignore_errors=True`'s behaviour when the removal fails
        return real_rmtree(path, *a, **kw)

    monkeypatch.setattr(emit_paths.shutil, "rmtree", refuse_staging)
    result = emit_paths.publish_staging(layout, plan)
    monkeypatch.undo()

    assert (out / "sources.json").is_file(), "the publication did not land"
    assert result.published, "nothing was recorded as published"
    assert result.residue == (
        f"{emit_paths.STATE_DIRNAME}/{emit_paths.STAGING_DIRNAME}",), (
        f"the undeletable staging tree was not disclosed: {result.residue}")
    assert not [p for p in staging.rglob("*") if p.is_file()], (
        "the residue holds files — publication did not drain it first, and the "
        "'a residue cannot make the folder wrong' claim would be false")


def test_nothing_on_disk_can_block_a_run(tmp_path):
    """The property the descope BOUGHT, stated as plainly as the ones it cost.

    The removed protocol could refuse to start: an unreadable marker or a
    corrupt inventory produced a BLOCKED run before Stage 1, by design, and that
    was the right call while the state existed. There is no such state now, so
    there is no such refusal — a folder full of junk under ``.dociq/`` cannot
    stop an operator getting a run.
    """
    out = tmp_path / "matter"
    _run(out)
    state = out / emit_paths.STATE_DIRNAME
    (state / "staging_ready.json").write_text(
        '{"superseded": ["clean', encoding="utf-8", newline="")
    (state / "published_set.json").write_text("not json at all",
                                              encoding="utf-8", newline="")
    (state / "superseded").mkdir(exist_ok=True)
    (state / "superseded" / "sources.json").write_text(
        "an old set-aside tree\n", encoding="utf-8", newline="")

    outcome = _run(out)
    assert outcome.published and outcome.ok, (
        "leftover state from the removed protocol blocked a run")
    log = json.loads((out / "processing_log.json").read_text(encoding="utf-8"))
    assert f"{emit_paths.STATE_DIRNAME}/superseded" in log["run"][
        "state_residue_before_run"], (
        "a set-aside tree left by the pre-D-32 build holds a previous run's "
        "deliverables and was not reported")
