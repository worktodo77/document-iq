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

**D-31 (2026-08-05) — the swap no longer deletes before it publishes**, and
section 4 below is rewritten against that. The old design removed the previous
run's deliverables and then moved the staged ones in, so every failure mode was
"half-deleted" and three consecutive review rounds each found a new window inside
the previous round's fix (B-1/B-2, then B-4/B-5, then B-6). The swap now RENAMES
the current set into ``.dociq/<aside>/``, renames the staged set into place, and
deletes only after that. A fourth property is therefore proven here:

4. **Nothing under the matter root is ever deleted or overwritten by the swap or
   by recovery.** Not "the deletion is retried and proven" — there is no
   deletion. That is asserted as a CLASS, by auditing every destructive
   filesystem call made during a swap, rather than one failure mode at a time.
"""

from __future__ import annotations

import hashlib
import json
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
# 4. DELETE-LAST — the swap renames, and recovery destroys nothing published
#    (D-31; the class B-1/B-2, B-4/B-5 and B-6 were instances of)
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

    Under D-31 the step it exercises is a RENAME rather than an ``unlink``, and
    the lock is still real: Windows refuses to rename a file another process
    holds open. The property it asserts is unchanged and is the one that
    matters — a superseded deliverable that cannot leave the folder stops the
    swap with the marker still on disk.
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


# --- D-31: the marker cannot authorize destroying a published set (B-6) -----


def _keep_the_marker_name(monkeypatch):
    """``unlink()`` returns and the marker's NAME survives.

    Codex's B-6 scenario verbatim: Windows delete-on-close semantics, an
    on-access scanner, or a filesystem shim lets the call return while the entry
    is still visible. Simulated by monkeypatch rather than by a real exclusive
    lock, because the state being reproduced — *the call succeeded and the name
    is still there* — is precisely the one a real lock does NOT produce (a real
    lock makes ``unlink`` raise, which this code already handles).
    """
    real = Path.unlink

    def keep(self, *a, **kw):
        if self.name == emit_paths.MARKER_NAME:
            return None
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "unlink", keep)


def test_a_marker_that_outlives_its_swap_deletes_nothing(tmp_path, monkeypatch):
    """FAIL-BEFORE (Codex review #2, second fix round, B-6).

    The swap completes and publishes the staged set; the marker's ``unlink()``
    returns while its name survives. On the OLD design that marker still carried
    the supersede list and nothing else, so the next ``recover_pending()`` read
    it, deleted the newly published files as superseded, found no staged
    replacement, removed the marker and returned. Codex's reproduction:
    ``after_first: new, marker=True`` then
    ``after_recovery: sources_exists=False, marker=False``.

    Under delete-last the same surviving marker authorizes nothing: it says
    ``published``, and independently of that the staging directory is empty, so
    there is nothing to publish and therefore nothing may be set aside. The
    assertion is the one Codex's reproduction violated — the published bytes are
    still there.
    """
    out = tmp_path / "matter"
    _run(out)
    layout, staging = _stage_a_second_set(
        tmp_path, out, ("sources.json", "document_index.csv"))
    staged_now = _fingerprint(staging)

    _keep_the_marker_name(monkeypatch)
    emit_paths.commit_staging(layout)
    monkeypatch.undo()

    assert emit_paths.pending_swap(layout), (
        "the fixture's premise is gone: the marker did not survive its unlink")
    published = _fingerprint(out)
    for rel, digest in staged_now.items():
        assert published.get(rel) == digest, f"{rel} was not published"

    emit_paths.recover_pending(layout)

    assert _fingerprint(out) == published, (
        "a recovery driven by a stale marker changed the published set — this "
        "is B-6: the marker outlived the swap and authorized deleting the files "
        "the swap had just put in place")
    assert (out / "sources.json").is_file()
    assert not emit_paths.pending_swap(layout), (
        "the second recovery did not clear the stale marker")


def test_a_stale_pending_marker_beside_an_empty_staging_deletes_nothing(
        tmp_path):
    """The same class one step further out, and it does not need a phase field.

    A marker left at ``pending`` — hand-restored, restored by a backup agent,
    written by an older build — beside a staging directory holding nothing. The
    names on disk are the primary evidence and they say there is nothing to
    publish, so nothing may be set aside. This is the guard that would hold even
    if the recorded phase were wrong.
    """
    out = tmp_path / "matter"
    _run(out)
    layout = OutputLayout.at(out)
    published = _fingerprint(out)
    assert published

    emit_paths.mark_ready(layout, ("sources.json", "clean_text", "upload_package"))
    staging = out / emit_paths.STATE_DIRNAME / emit_paths.STAGING_DIRNAME
    if staging.exists():
        shutil.rmtree(staging)
    assert emit_paths.pending_swap(layout)

    moved = emit_paths.recover_pending(layout)

    assert moved == (), f"a stale marker reported replacing {moved}"
    assert _fingerprint(out) == published, (
        "a stale pending marker moved the published set out of the folder")
    assert not emit_paths.pending_swap(layout)


# --- D-31: the class assertion — no destructive call reaches a deliverable --


class _DestructiveCallAudit:
    """Every filesystem-destroying call made while it is installed, by target.

    The class-level probe for D-31. The claim is not "this failure mode no
    longer deletes the published set" — it is that **the swap and its recovery
    contain no code path that deletes or overwrites anything under the matter
    root at all**. That is a property of the whole subsystem, so it is asserted
    over the calls it makes rather than over one scenario's outcome.

    ``os.replace`` is audited alongside the deletions because an overwrite IS a
    delete: it destroys whatever occupied the destination, with no name left to
    read afterwards. That is exactly the substitution D-31 forbids, and the swap
    used to publish with it.
    """

    def __init__(self):
        self.targets: list[str] = []

    def install(self, monkeypatch):
        import os as _os

        for mod, name, argno in (
            (shutil, "rmtree", 0),
            (_os, "remove", 0),
            (_os, "unlink", 0),
            (_os, "rmdir", 0),
            (_os, "replace", 1),  # the DESTINATION is what gets destroyed
        ):
            real = getattr(mod, name)

            def wrapper(*a, _real=real, _argno=argno, **kw):
                if len(a) > _argno:
                    self.targets.append(str(a[_argno]))
                return _real(*a, **kw)

            monkeypatch.setattr(mod, name, wrapper)

        real_unlink = Path.unlink

        def path_unlink(this, *a, **kw):
            self.targets.append(str(this))
            return real_unlink(this, *a, **kw)

        monkeypatch.setattr(Path, "unlink", path_unlink)

        real_replace = Path.replace

        def path_replace(this, target, *a, **kw):
            self.targets.append(str(target))
            return real_replace(this, target, *a, **kw)

        monkeypatch.setattr(Path, "replace", path_replace)

    def outside(self, root: Path) -> list[str]:
        """Targets under ``root`` that are NOT DocIQ's own state."""
        state = (root / emit_paths.STATE_DIRNAME).resolve()
        bad = []
        for t in self.targets:
            path = Path(t)
            if not path.is_absolute():
                continue
            try:
                path.resolve().relative_to(root.resolve())
            except ValueError:
                continue  # outside the matter folder entirely (a temp file)
            try:
                path.resolve().relative_to(state)
            except ValueError:
                bad.append(t)
        return bad


def test_the_swap_destroys_nothing_under_the_matter_root(tmp_path, monkeypatch):
    """FAIL-BEFORE: on the delete-first design this reports ``sources.json``,
    ``document_index.csv``, ``upload_package`` and every ``clean_text/*.txt``
    the previous run wrote — the supersede loop's ``unlink``/``rmtree`` and the
    publish loop's ``os.replace``. The count is the finding.

    The property is stated as an ENUMERATION of destructive primitives rather
    than as an outcome, so a future step that reaches for a different one is
    covered the day it is written.
    """
    out = tmp_path / "matter"
    _run(out)
    layout, staging = _stage_a_second_set(
        tmp_path, out,
        ("sources.json", "document_index.csv", "upload_package"))

    audit = _DestructiveCallAudit()
    audit.install(monkeypatch)
    emit_paths.commit_staging(layout)
    monkeypatch.undo()

    assert audit.targets, "the audit recorded nothing; it is not installed"
    assert audit.outside(out) == [], (
        "the swap deleted or overwrote something under the matter root that is "
        f"not DocIQ's own state: {audit.outside(out)}")


def test_recovery_destroys_nothing_under_the_matter_root(tmp_path, monkeypatch):
    """The same probe over RECOVERY, which is where B-6 lived.

    Recovery is the path a stale or hostile marker reaches, so it gets its own
    assertion rather than inheriting the swap's.
    """
    out = tmp_path / "matter"
    _run(out)
    layout, staging = _stage_a_second_set(
        tmp_path, out, ("sources.json", "upload_package"))

    # Interrupt mid-swap: one file set aside by hand, marker left as it is.
    aside = out / emit_paths.STATE_DIRNAME / emit_paths.ASIDE_PREFIX
    aside.mkdir(parents=True, exist_ok=True)
    (out / "sources.json").rename(aside / "sources.json")

    audit = _DestructiveCallAudit()
    audit.install(monkeypatch)
    emit_paths.recover_pending(layout)
    monkeypatch.undo()

    assert audit.outside(out) == [], (
        f"recovery destroyed something published: {audit.outside(out)}")
    assert (out / "sources.json").is_file(), "the roll-forward did not finish"
    assert not emit_paths.pending_swap(layout)


# --- D-31: what each failure LEAVES, read from the names on disk ------------


def test_a_blocked_set_aside_leaves_the_previous_set_and_no_mixture(
        tmp_path, monkeypatch):
    """Step 1 fails partway. The matter folder must hold the previous run's
    files and NONE of the new run's — incomplete is allowed, mixed is not."""
    out = tmp_path / "matter"
    _run(out)
    before = _fingerprint(out)
    superseded = ("sources.json", "document_index.csv")
    layout, staging = _stage_a_second_set(tmp_path, out, superseded)
    staged_now = _fingerprint(staging)

    real = emit_paths._rename_or_fail

    # The plan is acted on in sorted order, so `document_index.csv` moves and
    # `sources.json` is the one that cannot.
    def refuse_the_second(src, dst):
        if src.name == "sources.json":
            raise OSError("[WinError 32] held by another process")
        return real(src, dst)

    monkeypatch.setattr(emit_paths, "_rename_or_fail", refuse_the_second)
    with pytest.raises(OSError):
        emit_paths.commit_staging(layout)
    monkeypatch.undo()

    assert emit_paths.pending_swap(layout), "the marker did not survive"
    # Nothing of the new set is in the folder.
    assert _fingerprint(staging) == staged_now, "the new set was disturbed"
    assert (out / "sources.json").is_file(), (
        "the deliverable that could not be moved is gone — a rename that fails "
        "must destroy nothing")
    # What DID leave the folder is INTACT under `.dociq/`, moved not deleted.
    aside = out / emit_paths.STATE_DIRNAME / emit_paths.ASIDE_PREFIX
    kept = aside / "document_index.csv"
    assert kept.is_file()
    assert hashlib.sha256(kept.read_bytes()).hexdigest() == (
        before["document_index.csv"])
    # And the roll-forward finishes it.
    emit_paths.recover_pending(layout)
    after = _fingerprint(out)
    for rel, digest in staged_now.items():
        assert after.get(rel) == digest, f"{rel} did not survive the roll-forward"
    assert not emit_paths.pending_swap(layout)


def test_a_blocked_publish_leaves_an_incomplete_set_never_a_mixed_one(
        tmp_path, monkeypatch):
    """Step 2 fails partway — the case D-31 is most pointed about.

    The previous set is entirely out of the folder by then, so what a reader
    sees is *some of the new run and none of the old*. That is incomplete and
    it is honest; the old design's equivalent state was some of each, under Doc
    IDs that need not agree, with nothing saying so.
    """
    out = tmp_path / "matter"
    _run(out)
    before = _fingerprint(out)
    superseded = tuple(sorted(before))
    layout, staging = _stage_a_second_set(tmp_path, out, superseded)
    staged_now = _fingerprint(staging)

    real = emit_paths._rename_or_fail
    state = {"published": 0}

    def refuse_after_two_publishes(src, dst):
        if str(src).startswith(str(staging)):
            state["published"] += 1
            if state["published"] > 2:
                raise OSError("[WinError 32] held by another process")
        return real(src, dst)

    monkeypatch.setattr(emit_paths, "_rename_or_fail", refuse_after_two_publishes)
    with pytest.raises(OSError):
        emit_paths.commit_staging(layout)
    monkeypatch.undo()

    landed = _fingerprint(out)
    assert landed, "nothing was published at all; the fixture proves nothing"
    assert len(landed) < len(staged_now), "the publish did not actually stop"
    for rel, digest in landed.items():
        assert staged_now.get(rel) == digest, (
            f"{rel} in the matter folder belongs to neither the staged set nor "
            "nothing — this is the mixture delete-last exists to forbid")

    # The whole previous set is intact, moved not modified.
    aside = out / emit_paths.STATE_DIRNAME / emit_paths.ASIDE_PREFIX
    for rel, digest in before.items():
        kept = aside / rel
        assert kept.is_file(), f"{rel} is neither published nor set aside"
        assert hashlib.sha256(kept.read_bytes()).hexdigest() == digest

    emit_paths.recover_pending(layout)
    after = _fingerprint(out)
    for rel, digest in staged_now.items():
        assert after.get(rel) == digest
    assert not emit_paths.pending_swap(layout)


def test_a_blocked_cleanup_publishes_and_discloses_the_residue(
        tmp_path, monkeypatch):
    """Step 3 fails. This is a SUCCESS with a residue, not a failure.

    A lock on one file in a tree that has already been superseded must not turn
    a published run into a failed one — and the residue must not be silent,
    because nobody opens ``.dociq/``.
    """
    out = tmp_path / "matter"
    _run(out)
    layout, staging = _stage_a_second_set(tmp_path, out, ("sources.json",))
    staged_now = _fingerprint(staging)

    def refuse(path):
        if path.name.startswith(emit_paths.ASIDE_PREFIX):
            raise OSError("[WinError 32] held by another process")

    monkeypatch.setattr(emit_paths, "_remove_tree_or_fail", refuse)
    moved = emit_paths.commit_staging(layout)  # no exception
    monkeypatch.undo()

    assert "sources.json" in moved
    after = _fingerprint(out)
    for rel, digest in staged_now.items():
        assert after.get(rel) == digest, f"{rel} was not published"
    assert not emit_paths.pending_swap(layout), (
        "a cleanup failure left the folder declared mid-swap after a "
        "successful publish")
    residue = emit_paths.superseded_residue(layout)
    assert residue, "the surviving set-aside tree was not disclosed"
    assert all(r.startswith(f"{emit_paths.STATE_DIRNAME}/") for r in residue)

    # The NEXT swap must not collide with it, and must not overwrite it.
    marker = emit_paths.mark_ready(layout, ("sources.json",))
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["aside"] != emit_paths.ASIDE_PREFIX, (
        "the next swap chose a set-aside name the residue already occupies")


# --- D-31: the marker is state read from disk, and is checked as such -------


@pytest.mark.parametrize("aside", [
    "../clean_text", "..", "C:/Windows", "clean_text", "sources.json",
    "superseded/../..", "", 7,
])
def test_a_marker_cannot_point_the_cleanup_outside_dociq(tmp_path, aside):
    """The set-aside name selects what gets DELETED at the end of the swap, so
    a hand-edited or corrupt one must not be able to name a deliverable.

    The sibling of ``_validate_superseded_entry`` pointed the other way, and
    enumerated with it rather than after it: one check governs what a marker can
    move, the other what it can move things INTO and later delete.
    """
    out = tmp_path / "matter"
    _run(out)
    layout = OutputLayout.at(out)
    before = _fingerprint(out)

    marker = out / emit_paths.STATE_DIRNAME / emit_paths.MARKER_NAME
    marker.write_text(json.dumps({
        "staging": emit_paths.STAGING_DIRNAME,
        "superseded": ["sources.json"],
        "aside": aside,
        "phase": emit_paths.PHASE_PENDING,
    }), encoding="utf-8", newline="")

    with pytest.raises(emit_paths.PendingSwapUnreadable):
        emit_paths.recover_pending(layout)
    assert _fingerprint(out) == before, "the folder was touched by a refusal"
    assert emit_paths.pending_swap(layout), "a refusal deleted the marker"


def test_a_marker_without_a_phase_or_an_aside_fails_closed(tmp_path):
    """The B-2 rule applied to the two fields D-31 added.

    Nothing has shipped, so a marker missing them is not an older format — it is
    a marker this code did not write, and guessing a phase would be guessing
    whether the previous set is still in the matter folder.
    """
    out = tmp_path / "matter"
    _run(out)
    layout = OutputLayout.at(out)
    marker = out / emit_paths.STATE_DIRNAME / emit_paths.MARKER_NAME

    for payload in (
        {"staging": "staging", "superseded": ["sources.json"]},
        {"staging": "staging", "superseded": [], "aside": "superseded"},
        {"staging": "staging", "superseded": [], "aside": "superseded",
         "phase": "halfway"},
    ):
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps(payload), encoding="utf-8", newline="")
        with pytest.raises(emit_paths.PendingSwapUnreadable):
            emit_paths.recover_pending(layout)


def test_dociq_state_inside_staging_is_never_published(tmp_path):
    """A staging layout is an ``OutputLayout``, so anything run against it makes
    its own ``.dociq/`` inside it — the package builder does. Publishing that
    into the matter root would put DocIQ's scratch where the manifest expects
    deliverables. Held by construction rather than by the one consumer that
    happens to clean up after itself."""
    out = tmp_path / "matter"
    _run(out)
    layout, staging = _stage_a_second_set(tmp_path, out, ())
    ghost = staging / emit_paths.STATE_DIRNAME / "scratch.txt"
    ghost.parent.mkdir(parents=True, exist_ok=True)
    ghost.write_text("DocIQ's own state", encoding="utf-8", newline="")

    emit_paths.commit_staging(layout)
    assert not (out / emit_paths.STATE_DIRNAME / "scratch.txt").exists()
    assert not (out / "scratch.txt").exists()
