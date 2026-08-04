"""Stage 5 writes to staging and swaps — the scheduled Sprint-1 gap.

``docs/reviews/sprint-1_merge_readiness.md``, NOT PROVEN item 8: *"A crash during
emit, after the purge, can still leave a partly-replaced output folder. Distinct
from the aborted-walk case that B-1 closed; it needs write-to-staging-then-swap."*

The failure it names is specific and worth restating, because the tests below are
shaped by it rather than by the words "atomic". A re-run used to delete the
previous run's deliverables and then write the new ones into the same folder. A
crash in between — and emit is where a full-corpus run spends real time — left
``clean_text/`` holding some of run 1's documents and some of run 2's, under Doc
IDs that need not agree, with an ``output_manifest.json`` from run 1 describing a
set that no longer exists. Nothing on disk said so.

Three properties are proven here:

1. **Crash-during-emit leaves the previous run complete.** Not "mostly complete",
   not "the manifest is stale": byte-for-byte the folder run 1 left.
2. **An interrupted swap is rolled forward**, so the exposed state is the moves
   themselves rather than the whole of emit — and the next run finishes them
   before it reads anything.
3. **The staging path is in no hashed artifact** (criterion 7). Two runs whose
   staging directories are at different absolute paths produce one corpus hash,
   one log ``content`` hash, and byte-identical adjacent files. This is the check
   that would have caught ``output_root`` inside hashed log content in Sprint 1,
   and it is the one this change could most plausibly break.

**What this file does NOT prove, and where that lives instead.** Codex review #2
found two things these three properties are silent about, both in
``tests/test_publication_gate.py``: that a set which fails §4 Stage 6 must never
be marked ready at all (B-1 — the tests here all start from a run whose gates
were green), and that an interrupted swap whose MARKER is unreadable must fail
closed rather than roll forward with an empty supersede list (B-2 — property 2
above only ever exercises a readable one).
"""

from __future__ import annotations

import hashlib
import json
import shutil

import pytest

from dociq import pipeline
from dociq.contracts import RunConfig
from dociq.emit import paths as emit_paths
from dociq.emit.paths import OutputLayout
from dociq.ingest import extract as ex
from dociq.ingest import walker
from dociq.profiles.model import OperatorStamp
from dociq.verify import manifest as mf

from .conftest import FIXTURES

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


class Boom(RuntimeError):
    """A crash in the middle of emit, at a point chosen to be maximally
    destructive under the old write path: after the purge, after some of
    ``clean_text/`` is written, before the index and the log."""


# ---------------------------------------------------------------------------
# 1. The crash
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
    # And the wreckage is not left where a reader could mistake it for output.
    assert not emit_paths.pending_swap(OutputLayout.at(out)), (
        "a run that never finished staging must not leave a readiness marker"
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
    staging = out / emit_paths.STATE_DIRNAME / emit_paths.STAGING_DIRNAME
    assert staging.is_dir(), "the crashed run left no staging to discard"

    monkeypatch.undo()
    third = _run(out)
    assert third.published
    assert not staging.exists(), "staging survived a completed run"
    assert third.ok


# ---------------------------------------------------------------------------
# 2. The interrupted swap
# ---------------------------------------------------------------------------


def test_an_interrupted_swap_is_rolled_forward_by_the_next_run(tmp_path):
    """Stage a complete set, mark it ready, move ONE file, then stop — the
    mid-swap state — and prove the next run completes it before doing anything
    else, rather than reading a folder that is half of each run."""
    out = tmp_path / "matter"
    _run(out)
    layout = OutputLayout.at(out)

    # A second run's complete set, captured before the swap.
    second_out = tmp_path / "second"
    _run(second_out)
    staging = out / emit_paths.STATE_DIRNAME / emit_paths.STAGING_DIRNAME
    shutil.copytree(second_out, staging, ignore=shutil.ignore_patterns(".dociq"))
    expected = _fingerprint(staging)
    emit_paths.mark_ready(layout, ("document_index.csv",))

    # The interruption: one file moved, then nothing.
    moved = staging / "sources.json"
    moved.replace(out / "sources.json")
    assert emit_paths.pending_swap(layout)

    recovered = emit_paths.recover_pending(layout)
    assert "document_index.csv" in recovered
    assert not emit_paths.pending_swap(layout), "the marker outlived the swap"
    assert not staging.exists()

    after = _fingerprint(out)
    for rel, digest in expected.items():
        assert after.get(rel) == digest, f"{rel} did not survive the roll-forward"


def test_the_roll_forward_happens_before_the_next_run_reads_anything(tmp_path):
    """It is the first statement of :func:`dociq.pipeline.run` for a reason: the
    ledger the D-04 renumbering check reads lives in the folder a pending swap is
    still replacing.

    **This test used to pass with recovery switched off.** A rehearsal review
    replaced ``recover_pending``'s call site with ``recovered = ()`` — nothing
    rolled forward at all — and it stayed green. Two reasons, both in the setup:
    it staged a single bogus ``sources.json`` and called ``mark_ready`` with an
    EMPTY superseded list, so there was nothing for recovery to move; and it
    asserted only that a disclosure APPEARED, which is gated on a marker having
    been present at start rather than on recovery having done anything. A run
    that recovered nothing and then simply succeeded on its own was
    indistinguishable from a run that rolled a swap forward.

    It now stages a complete set the way the sibling test above does, and
    asserts the disclosure NAMES what was rolled forward."""
    out = tmp_path / "matter"
    _run(out)
    layout = OutputLayout.at(out)

    # A complete set from a real run, staged and marked ready — so recovery has
    # something real to move, and its absence is observable.
    second_out = tmp_path / "second"
    _run(second_out)
    staging = out / emit_paths.STATE_DIRNAME / emit_paths.STAGING_DIRNAME
    shutil.copytree(second_out, staging, ignore=shutil.ignore_patterns(".dociq"))
    superseded = ("document_index.csv",)
    emit_paths.mark_ready(layout, superseded)
    assert emit_paths.pending_swap(layout)

    second = _run(out)
    assert second.published
    assert not emit_paths.pending_swap(layout)
    assert any("RECOVERED" in n for n in second.walk_notes.invocation), (
        "a swap completed by a later run must be disclosed, not silent"
    )
    payload = json.loads((out / "processing_log.json").read_text(encoding="utf-8"))
    assert "recovered_swap" in payload["run"]

    # The load-bearing part: the disclosure must name what was actually rolled
    # forward. With recovery disabled this is empty, and the test goes red.
    rolled = payload["run"]["recovered_swap"]
    assert rolled, (
        "the run disclosed a recovered swap that moved nothing — the "
        "disclosure is gated on a marker being present, not on recovery "
        "having happened, so this must assert what moved"
    )
    assert any(name in json.dumps(rolled) for name in superseded), (
        f"the recovery disclosure does not name the superseded deliverables "
        f"{superseded} it completed: {rolled!r}"
    )
    assert "recovered_swap" not in json.dumps(payload["content"]), (
        "an invocation fact reached the hashed content"
    )


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


def test_the_upload_package_no_longer_survives_a_shrinking_re_run(tmp_path):
    """FAIL-BEFORE: with ``upload_package`` absent from ``_STALE_PATTERNS`` — as
    it was, on the reasoning that the emitter rebuilt it in place — the swap
    leaves the previous run's copies in the folder an operator uploads whole.

    The reasoning was true of the old write path and is withdrawn with it: the
    rebuild now happens in staging and never touches the destination.
    """
    out = tmp_path / "matter"
    _run(out)
    stray = out / "upload_package" / "LI-99999-not-from-this-run.txt"
    stray.write_text("evidence from another run\n", encoding="utf-8", newline="")

    _run(out)
    assert not stray.exists(), (
        "a previous run's upload_package file survived a re-run"
    )


# ---------------------------------------------------------------------------
# 4. A destructive step that cannot complete must not publish (B-4)
# ---------------------------------------------------------------------------


def _stage_a_second_set(tmp_path, out, superseded):
    """A complete second set in ``out``'s staging directory, marked ready.

    The set is a real run's output rather than hand-built files, because the
    property under test is about the swap moving a COMPLETE set over a
    superseded one — a synthetic staging directory would prove the same code
    path over evidence nobody would publish.
    """
    layout = OutputLayout.at(out)
    second_out = tmp_path / "second"
    _run(second_out)
    staging = out / emit_paths.STATE_DIRNAME / emit_paths.STAGING_DIRNAME
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(second_out, staging, ignore=shutil.ignore_patterns(".dociq"))
    emit_paths.mark_ready(layout, superseded)
    return layout, staging


def test_a_directory_removal_that_fails_cannot_publish_a_mixed_set(tmp_path):
    """FAIL-BEFORE (Codex review #2 fix round, B-4).

    One file inside a superseded DIRECTORY is held open, which is the ordinary
    Windows case this project's environment notes already document: an antivirus
    or backup agent has a handle on a file the swap is about to delete. The
    removal genuinely fails — no mock, a real ``PermissionError`` from a real
    open handle.

    With ``shutil.rmtree(path, ignore_errors=True)``, which is what this
    replaced, the swap absorbed that failure, recorded the directory as removed,
    moved the new package in beside the survivor and DELETED the readiness
    marker: a folder holding two builds with nothing left saying so. The
    assertions below are written against that state, so the test is red on the
    old code for the reason B-4 names rather than for an incidental one.
    """
    out = tmp_path / "matter"
    _run(out)
    package = out / "upload_package"
    assert package.is_dir(), "the fixture run did not produce a package to supersede"

    stale = package / "LI-99999-from-an-earlier-run.txt"
    stale.write_text("evidence from another run\n", encoding="utf-8", newline="")
    layout, staging = _stage_a_second_set(tmp_path, out, ("upload_package",))
    staged_now = _fingerprint(staging)

    held = stale.open("rb")  # the antivirus handle
    try:
        with pytest.raises(OSError) as excinfo:
            emit_paths.commit_staging(layout)
        assert str(stale) in str(excinfo.value) or "upload_package" in str(
            excinfo.value
        ), "the failure does not name the directory it could not remove"

        # The three facts B-4 is about, in the order they were violated.
        assert emit_paths.pending_swap(layout), (
            "the readiness marker was deleted over a superseded directory that "
            "is still on disk — nothing remains to disclose or repair the folder"
        )
        assert stale.exists(), "the fixture's premise (a surviving stale file) is gone"
        # Nothing moved. The new set is still whole, in staging, where a
        # roll-forward can still publish it as a set — rather than half in the
        # folder beside a superseded file that outlived its own removal.
        assert _fingerprint(staging) == staged_now, (
            "the new set was moved into the folder over a superseded directory "
            "that is still on disk — this is the mixed set"
        )
    finally:
        held.close()

    # Roll-forward is still possible: the marker survived, so the next run
    # finishes the swap the moment the lock is released.
    emit_paths.recover_pending(layout)
    assert not emit_paths.pending_swap(layout)
    assert not stale.exists(), "the roll-forward left the stale file behind"
    after = _fingerprint(out)
    for rel, digest in staged_now.items():
        assert after.get(rel) == digest, f"{rel} did not survive the roll-forward"


def test_a_file_removal_that_fails_cannot_publish_a_mixed_set(tmp_path):
    """The FILE sibling of the test above, enumerated with it rather than after.

    ``unlink`` was already retried, so this passed before the B-4 fix — it is
    here because "the retried step and the unretried step behave the same way
    under a real lock" is the property, and a class fix that only ever proves
    the branch the reviewer named has not been shown to be a class fix.
    """
    out = tmp_path / "matter"
    _run(out)
    victim = out / "document_index.csv"
    assert victim.is_file()
    layout, staging = _stage_a_second_set(tmp_path, out, ("document_index.csv",))

    held = victim.open("rb")
    try:
        with pytest.raises(OSError):
            emit_paths.commit_staging(layout)
        assert emit_paths.pending_swap(layout), "the marker did not survive"
    finally:
        held.close()

    emit_paths.recover_pending(layout)
    assert not emit_paths.pending_swap(layout)
